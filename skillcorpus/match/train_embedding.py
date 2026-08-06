"""Step 4: Fine-tune bi-encoder with in-batch InfoNCE loss.

Reproduces the SkillRouter embedding model training:
  - Base model: Qwen3-Emb-0.6B
  - Loss: In-batch InfoNCE with temperature τ=0.05
  - Query format: instruction-prefixed
  - Skill format: name | description | body
  - 1 epoch, lr=2e-5, cosine schedule, 5% warmup

Usage:
    # Single GPU:
    python3 train_embedding.py --config configs/embedding.yaml
    # Multi-GPU (DDP):
    torchrun --nproc_per_node=4 train_embedding.py --config configs/embedding.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
from pathlib import Path

import torch
import torch.distributed as dist
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, Dataset, DistributedSampler
from transformers import (
    AutoModel,
    AutoTokenizer,
    get_cosine_schedule_with_warmup,
)

try:
    import yaml
except ImportError:
    yaml = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

QUERY_INSTRUCTION = (
    "Instruct: Given a task description, retrieve the most relevant "
    "skill document that would help an agent complete the task\nQuery:"
)


def last_token_pool(hidden, mask):
    if mask[:, -1].sum() == mask.shape[0]:
        return hidden[:, -1]
    seq_len = mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), seq_len]


class TripletDataset(Dataset):
    def __init__(self, data_path: str, query_max: int = 1500,
                 desc_max: int = 500, body_max: int = 8000):
        self.records = []
        with open(data_path) as f:
            for line in f:
                if line.strip():
                    self.records.append(json.loads(line))
        self.query_max = query_max
        self.desc_max = desc_max
        self.body_max = body_max

    def __len__(self):
        return len(self.records)

    def format_query(self, text: str) -> str:
        return f"{QUERY_INSTRUCTION}{text[:self.query_max]}"

    def format_skill(self, skill: dict) -> str:
        name = skill.get("name", "")
        desc = (skill.get("description") or "")[:self.desc_max]
        body = (skill.get("body") or "")[:self.body_max]
        return f"{name} | {desc} | {body}"

    def __getitem__(self, idx):
        rec = self.records[idx]
        query_text = self.format_query(rec["instruction_text"])
        pos_text = self.format_skill(rec["positive"])
        neg_texts = [self.format_skill(n) for n in rec["negatives"]]
        return query_text, pos_text, neg_texts


def collate_fn(batch):
    queries, positives, neg_lists = zip(*batch)
    return list(queries), list(positives), list(neg_lists)


class InfoNCETrainer:
    def __init__(self, model, tokenizer, device, args,
                 rank=0, world_size=1, raw_model=None):
        self.model = model
        self.raw_model = raw_model or model
        self.tokenizer = tokenizer
        self.device = device
        self.args = args
        self.rank = rank
        self.world_size = world_size
        self.temperature = args.temperature

    def encode_batch(self, texts: list[str]) -> torch.Tensor:
        encoded = self.tokenizer(
            texts, padding=True, truncation=True,
            max_length=self.args.max_length, return_tensors="pt",
        )
        encoded = {k: v.to(self.device) for k, v in encoded.items()}
        outputs = self.model(**encoded)
        embs = last_token_pool(outputs.last_hidden_state, encoded["attention_mask"])
        return F.normalize(embs, p=2, dim=1)

    def encode_all(self, queries, positives, all_negs):
        all_texts = queries + positives + all_negs
        all_embs = self.encode_batch(all_texts)
        nq = len(queries)
        np_ = len(positives)
        return all_embs[:nq], all_embs[nq:nq+np_], all_embs[nq+np_:] if all_negs else None

    def _gather_embs(self, embs: torch.Tensor) -> torch.Tensor:
        if self.world_size <= 1:
            return embs
        gathered = [torch.zeros_like(embs) for _ in range(self.world_size)]
        dist.all_gather(gathered, embs.contiguous())
        gathered[self.rank] = embs
        return torch.cat(gathered, dim=0)

    def _gather_var_embs(self, embs: torch.Tensor) -> torch.Tensor:
        if self.world_size <= 1:
            return embs
        local_size = torch.tensor([embs.shape[0]], device=self.device)
        sizes = [torch.zeros_like(local_size) for _ in range(self.world_size)]
        dist.all_gather(sizes, local_size)
        max_size = max(s.item() for s in sizes)
        padded = torch.zeros(max_size, embs.shape[1], device=self.device, dtype=embs.dtype)
        padded[:embs.shape[0]] = embs
        gathered = [torch.zeros_like(padded) for _ in range(self.world_size)]
        dist.all_gather(gathered, padded)
        gathered[self.rank][:embs.shape[0]] = embs
        return torch.cat([g[:s.item()] for g, s in zip(gathered, sizes)], dim=0)

    def compute_infonce_loss(self, query_embs, pos_embs, neg_embs=None):
        all_query = self._gather_embs(query_embs)
        all_pos = self._gather_embs(pos_embs)
        if neg_embs is not None and neg_embs.shape[0] > 0:
            all_neg = self._gather_var_embs(neg_embs)
            all_doc_embs = torch.cat([all_pos, all_neg], dim=0)
        else:
            all_doc_embs = all_pos
        sim_matrix = all_query @ all_doc_embs.T / self.temperature
        labels = torch.arange(all_query.size(0), device=self.device)
        loss = F.cross_entropy(sim_matrix, labels)
        return loss

    def train(self, train_loader, num_epochs, lr, warmup_ratio, weight_decay,
              grad_accum_steps, output_dir, save_steps):
        self.model.train()
        optimizer = torch.optim.AdamW(
            self.model.parameters(), lr=lr, weight_decay=weight_decay)

        total_steps = len(train_loader) * num_epochs // grad_accum_steps
        warmup_steps = int(total_steps * warmup_ratio)
        scheduler = get_cosine_schedule_with_warmup(
            optimizer, warmup_steps, total_steps)

        global_step = 0
        use_ddp = self.world_size > 1

        if self.rank == 0:
            log.info("Training: %d steps, %d warmup, lr=%.1e (world_size=%d)",
                     total_steps, warmup_steps, lr, self.world_size)

        for epoch in range(num_epochs):
            if hasattr(train_loader.sampler, "set_epoch"):
                train_loader.sampler.set_epoch(epoch)
            epoch_loss = 0.0
            optimizer.zero_grad()

            for batch_idx, (queries, positives, neg_lists) in enumerate(train_loader):
                is_sync_step = (batch_idx + 1) % grad_accum_steps == 0

                all_negs = [n for neg_list in neg_lists for n in neg_list]
                if use_ddp and not is_sync_step:
                    with self.model.no_sync():
                        query_embs, pos_embs, neg_embs = self.encode_all(queries, positives, all_negs)
                        loss = self.compute_infonce_loss(query_embs, pos_embs, neg_embs)
                        loss = loss / grad_accum_steps
                        loss.backward()
                else:
                    query_embs, pos_embs, neg_embs = self.encode_all(queries, positives, all_negs)
                    loss = self.compute_infonce_loss(query_embs, pos_embs, neg_embs)
                    loss = loss / grad_accum_steps
                    loss.backward()

                epoch_loss += loss.item() * grad_accum_steps

                if is_sync_step:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad()
                    global_step += 1

                    if self.rank == 0 and global_step % 50 == 0:
                        avg_loss = epoch_loss / (batch_idx + 1)
                        log.info("Epoch %d step %d/%d loss=%.4f lr=%.2e",
                                 epoch, global_step, total_steps, avg_loss,
                                 scheduler.get_last_lr()[0])

                    if self.rank == 0 and save_steps > 0 and global_step % save_steps == 0:
                        ckpt_dir = Path(output_dir) / f"checkpoint-{global_step}"
                        self._save(ckpt_dir)

            if self.rank == 0:
                log.info("Epoch %d finished, avg loss=%.4f",
                         epoch, epoch_loss / max(len(train_loader), 1))

        if self.rank == 0:
            final_dir = Path(output_dir) / "final"
            self._save(final_dir)
            log.info("Training complete. Model saved to %s", final_dir)

        if use_ddp:
            dist.barrier()
            dist.destroy_process_group()

    def _save(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        self.raw_model.save_pretrained(path)
        self.tokenizer.save_pretrained(path)
        log.info("Saved checkpoint to %s", path)


def load_config(config_path: str | None) -> dict:
    if config_path and yaml and Path(config_path).exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--train_data", type=str, default="data/train_triplets.jsonl")
    parser.add_argument("--base_model", type=str, default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--output_dir", type=str, default="outputs/embedding")
    parser.add_argument("--max_length", type=int, default=2048)
    parser.add_argument("--batch_size", type=int, default=8)
    parser.add_argument("--grad_accum_steps", type=int, default=4)
    parser.add_argument("--num_epochs", type=int, default=1)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--weight_decay", type=float, default=0.01)
    parser.add_argument("--warmup_ratio", type=float, default=0.05)
    parser.add_argument("--temperature", type=float, default=0.05)
    parser.add_argument("--save_steps", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bf16", action="store_true", default=True)
    parser.add_argument("--gradient_checkpointing", action="store_true", default=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    for k, v in cfg.items():
        if hasattr(args, k):
            setattr(args, k, v)

    use_ddp = "LOCAL_RANK" in os.environ
    if use_ddp:
        dist.init_process_group(backend="nccl")
        local_rank = int(os.environ["LOCAL_RANK"])
        rank = dist.get_rank()
        world_size = dist.get_world_size()
        device = torch.device(f"cuda:{local_rank}")
        torch.cuda.set_device(device)
    else:
        rank, world_size = 0, 1
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    if rank == 0:
        log.info("Device: %s (world_size=%d)", device, world_size)
        log.info("Loading base model: %s", args.base_model)

    dtype = torch.bfloat16 if args.bf16 else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model, trust_remote_code=True, padding_side="left")
    model = AutoModel.from_pretrained(
        args.base_model, trust_remote_code=True, torch_dtype=dtype)
    if tokenizer.pad_token is None and tokenizer.eos_token is not None:
        tokenizer.pad_token = tokenizer.eos_token

    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
    model.to(device)

    raw_model = model
    if use_ddp:
        model = DDP(model, device_ids=[local_rank])

    if rank == 0:
        log.info("Loading training data: %s", args.train_data)
    dataset = TripletDataset(args.train_data)
    if rank == 0:
        log.info("Training samples: %d", len(dataset))

    sampler = DistributedSampler(dataset, shuffle=True) if use_ddp else None
    loader = DataLoader(
        dataset, batch_size=args.batch_size, shuffle=(sampler is None),
        sampler=sampler, collate_fn=collate_fn, num_workers=4, pin_memory=True)

    trainer = InfoNCETrainer(
        model, tokenizer, device, args,
        rank=rank, world_size=world_size, raw_model=raw_model,
    )
    trainer.train(
        train_loader=loader,
        num_epochs=args.num_epochs,
        lr=args.lr,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        grad_accum_steps=args.grad_accum_steps,
        output_dir=args.output_dir,
        save_steps=args.save_steps,
    )


if __name__ == "__main__":
    main()
