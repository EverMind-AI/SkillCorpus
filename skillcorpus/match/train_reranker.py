"""Step 6: Fine-tune reranker with listwise cross-entropy loss.

  - Base model: Qwen3-Rank-0.6B (causal LM)
  - Loss: Listwise cross-entropy over top-K candidates
  - Score: logit(yes) - logit(no) for each (query, candidate) pair
  - 1 epoch, lr=1e-5, cosine schedule, 5% warmup
  - Batch=1 listwise group, grad_accum=16

Usage:
    # Single GPU:
    python3 train_reranker.py --config configs/reranker.yaml
    # Multi-GPU (DDP):
    torchrun --nproc_per_node=2 train_reranker.py --config configs/reranker.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
from pathlib import Path

import torch
import torch.distributed as dist
import torch.nn.functional as F
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    get_cosine_schedule_with_warmup,
)

try:
    import yaml
except ImportError:
    yaml = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

RERANK_INSTRUCTION = (
    "Given a task description, judge whether the skill document "
    "is relevant and useful for completing the task"
)

SYSTEM_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements '
    'based on the Query and the Instruct provided. Note that the answer can '
    'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
SYSTEM_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'


def format_rerank_prompt(query_text: str, name: str, desc: str, body: str,
                         desc_max: int = 500, body_max: int = 2000) -> str:
    desc = desc[:desc_max]
    body = body[:body_max]
    doc_text = f"{name} | {desc} | {body}"
    return (
        f"<Instruct>: {RERANK_INSTRUCTION}\n\n"
        f"<Query>: {query_text}\n\n"
        f"<Document>: {doc_text}"
    )


class RerankerDataset(Dataset):
    def __init__(self, data_path: str):
        self.records = []
        with open(data_path) as f:
            for line in f:
                if line.strip():
                    self.records.append(json.loads(line))

    def __len__(self):
        return len(self.records)

    def __getitem__(self, idx):
        return self.records[idx]


class ListwiseRerankerTrainer:
    def __init__(self, model, tokenizer, device, args,
                 rank=0, world_size=1, raw_model=None):
        self.model = model
        self.raw_model = raw_model or model
        self.tokenizer = tokenizer
        self.device = device
        self.args = args
        self.rank = rank
        self.world_size = world_size

        self.prefix_tokens = tokenizer.encode(SYSTEM_PREFIX, add_special_tokens=False)
        self.suffix_tokens = tokenizer.encode(SYSTEM_SUFFIX, add_special_tokens=False)
        self.yes_id = tokenizer.convert_tokens_to_ids("yes")
        self.no_id = tokenizer.convert_tokens_to_ids("no")
        self.pad_id = tokenizer.pad_token_id or 0

    def tokenize_pair(self, query_text: str, candidate: dict) -> list[int]:
        prompt = format_rerank_prompt(
            query_text,
            candidate.get("name", ""),
            candidate.get("description", ""),
            candidate.get("body", ""),
        )
        inner_max = self.args.max_length - len(self.prefix_tokens) - len(self.suffix_tokens)
        inner = self.tokenizer(
            prompt, padding=False, truncation=True,
            max_length=inner_max, return_attention_mask=False,
        )
        return self.prefix_tokens + inner["input_ids"] + self.suffix_tokens

    def score_candidates(self, query_text: str, candidates: list[dict]) -> torch.Tensor:
        tokenized = [self.tokenize_pair(query_text, c) for c in candidates]
        max_len = max(len(t) for t in tokenized)

        input_ids = torch.full((len(tokenized), max_len), self.pad_id, dtype=torch.long)
        attn_mask = torch.zeros((len(tokenized), max_len), dtype=torch.long)

        for i, ids in enumerate(tokenized):
            pad_len = max_len - len(ids)
            input_ids[i, pad_len:] = torch.tensor(ids, dtype=torch.long)
            attn_mask[i, pad_len:] = 1

        input_ids = input_ids.to(self.device)
        attn_mask = attn_mask.to(self.device)

        logits = self.model(input_ids=input_ids, attention_mask=attn_mask).logits[:, -1, :]
        scores = logits[:, self.yes_id] - logits[:, self.no_id]
        return scores

    def compute_listwise_loss(self, scores: torch.Tensor, labels: list[int],
                              temperature: float = 1.0) -> torch.Tensor:
        """Listwise cross-entropy: positive candidates should score highest."""
        labels_t = torch.tensor(labels, dtype=torch.float, device=self.device)
        pos_mask = labels_t > 0

        if pos_mask.sum() == 0:
            return torch.tensor(0.0, device=self.device, requires_grad=True)

        scaled = scores / temperature
        log_softmax = F.log_softmax(scaled, dim=0)
        pos_log_probs = log_softmax[pos_mask]
        loss = -pos_log_probs.mean()
        return loss

    def train(self, dataset, num_epochs, lr, warmup_ratio, weight_decay,
              grad_accum_steps, output_dir, save_steps):
        self.model.train()
        optimizer = torch.optim.AdamW(
            self.model.parameters(), lr=lr, weight_decay=weight_decay)

        indices = list(range(len(dataset)))

        if self.world_size > 1:
            local_accum = grad_accum_steps // self.world_size
            per_rank = len(indices) // self.world_size
        else:
            local_accum = grad_accum_steps
            per_rank = len(indices)

        total_steps = per_rank * num_epochs // local_accum
        warmup_steps = int(total_steps * warmup_ratio)
        scheduler = get_cosine_schedule_with_warmup(
            optimizer, warmup_steps, total_steps)

        global_step = 0
        if self.rank == 0:
            log.info("Training: %d steps, %d warmup, lr=%.1e (world_size=%d, local_accum=%d)",
                     total_steps, warmup_steps, lr, self.world_size, local_accum)

        use_ddp = self.world_size > 1

        for epoch in range(num_epochs):
            random.seed(self.args.seed + epoch)
            random.shuffle(indices)
            rank_indices = indices[self.rank * per_rank : (self.rank + 1) * per_rank]

            epoch_loss = 0.0
            optimizer.zero_grad()

            for step_in_epoch, idx in enumerate(rank_indices):
                record = dataset[idx]
                query_text = record["instruction_text"]
                candidates = record["candidates"]
                labels = [c["label"] for c in candidates]

                is_sync_step = (step_in_epoch + 1) % local_accum == 0

                if use_ddp and not is_sync_step:
                    with self.model.no_sync():
                        scores = self.score_candidates(query_text, candidates)
                        loss = self.compute_listwise_loss(scores, labels)
                        loss = loss / local_accum
                        loss.backward()
                else:
                    scores = self.score_candidates(query_text, candidates)
                    loss = self.compute_listwise_loss(scores, labels)
                    loss = loss / local_accum
                    loss.backward()

                epoch_loss += loss.item() * local_accum

                if is_sync_step:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad()
                    global_step += 1

                    if self.rank == 0 and global_step % 50 == 0:
                        avg_loss = epoch_loss / (step_in_epoch + 1)
                        log.info("Epoch %d step %d/%d loss=%.4f lr=%.2e",
                                 epoch, global_step, total_steps, avg_loss,
                                 scheduler.get_last_lr()[0])

                    if self.rank == 0 and save_steps > 0 and global_step % save_steps == 0:
                        ckpt_dir = Path(output_dir) / f"checkpoint-{global_step}"
                        self._save(ckpt_dir)

            if self.rank == 0:
                log.info("Epoch %d finished, avg loss=%.4f",
                         epoch, epoch_loss / max(len(rank_indices), 1))

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
    parser.add_argument("--train_data", type=str, default="data/reranker_train.jsonl")
    parser.add_argument("--base_model", type=str, default="Qwen/Qwen3-Reranker-0.6B")
    parser.add_argument("--output_dir", type=str, default="outputs/reranker")
    parser.add_argument("--max_length", type=int, default=4096)
    parser.add_argument("--num_epochs", type=int, default=1)
    parser.add_argument("--lr", type=float, default=1e-5)
    parser.add_argument("--weight_decay", type=float, default=0.01)
    parser.add_argument("--warmup_ratio", type=float, default=0.05)
    parser.add_argument("--grad_accum_steps", type=int, default=16)
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
        args.base_model, padding_side="left", trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model, torch_dtype=dtype, trust_remote_code=True)
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
    dataset = RerankerDataset(args.train_data)
    if rank == 0:
        log.info("Training samples: %d", len(dataset))

    trainer = ListwiseRerankerTrainer(
        model, tokenizer, device, args,
        rank=rank, world_size=world_size, raw_model=raw_model,
    )
    trainer.train(
        dataset=dataset,
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
