#!/usr/bin/env python3
"""Step 4: fine-tune the encoder with an in-batch InfoNCE loss.

Each request (training instruction + request[:1500]) is contrasted with its positive and with every
positive and negative in the batch, gathered across all GPUs; documents are "name | description[:500] |
body[:8000]". Last-token pooling, L2 normalization, temperature 0.05. Defaults: Qwen3-Embedding-0.6B, 4 requests per
GPU (with 8 GPUs, 32 requests and their negatives per contrastive batch), 4 gradient accumulation
steps, one epoch, AdamW (lr 2e-5, weight decay 0.01), cosine schedule with 5% warmup, gradient clipping
at 1.0, max length 1,024, bf16, gradient checkpointing, seed 42.

Example (8 GPUs):
  torchrun --nproc_per_node=8 train/train_encoder.py --train_data data/train_triplets.jsonl \
      --base_model Qwen/Qwen3-Embedding-0.6B --output_dir outputs/encoder
"""
import argparse
import json
import logging
import os
import random
import sys
from pathlib import Path

import torch
import torch.distributed as dist
import torch.nn.functional as F
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, Dataset, DistributedSampler
from transformers import AutoModel, AutoTokenizer, get_cosine_schedule_with_warmup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import TRAIN_INSTRUCTION, format_skill  # noqa: E402
from skillrep.encoder import last_token_pool  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
QUERY_MAX_CHARS = 1500


class TripletDataset(Dataset):
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self.records = [json.loads(line) for line in f if line.strip()]

    def __len__(self):
        return len(self.records)

    def __getitem__(self, i):
        r = self.records[i]
        return (TRAIN_INSTRUCTION + r["query"][:QUERY_MAX_CHARS], format_skill(r["positive"]),
                [format_skill(n) for n in r["negatives"]])


def collate(batch):
    queries, positives, negatives = zip(*batch)
    return list(queries), list(positives), list(negatives)


class Trainer:
    def __init__(self, model, raw_model, tokenizer, device, args, rank, world_size):
        self.model, self.raw_model, self.tok = model, raw_model, tokenizer
        self.device, self.args, self.rank, self.world_size = device, args, rank, world_size

    def encode(self, texts):
        batch = self.tok(texts, padding=True, truncation=True, max_length=self.args.max_length,
                         return_tensors="pt").to(self.device)
        hidden = self.model(**batch).last_hidden_state
        return F.normalize(last_token_pool(hidden, batch["attention_mask"]), p=2, dim=1)

    def gather(self, x):
        """All-gather rows of variable count; the local slice keeps its gradient."""
        if self.world_size == 1:
            return x
        size = torch.tensor([x.shape[0]], device=self.device)
        sizes = [torch.zeros_like(size) for _ in range(self.world_size)]
        dist.all_gather(sizes, size)
        padded = torch.zeros(max(int(s) for s in sizes), x.shape[1], device=self.device, dtype=x.dtype)
        padded[:x.shape[0]] = x
        parts = [torch.zeros_like(padded) for _ in range(self.world_size)]
        dist.all_gather(parts, padded)
        parts[self.rank] = padded  # carries the gradient of x
        return torch.cat([p[:int(s)] for p, s in zip(parts, sizes)], dim=0)

    def loss(self, queries, positives, negatives):
        emb = self.encode(queries + positives + negatives)
        nq, npos = len(queries), len(positives)
        q, docs = self.gather(emb[:nq]), self.gather(emb[nq:nq + npos])
        if negatives:
            docs = torch.cat([docs, self.gather(emb[nq + npos:])], dim=0)
        sim = q @ docs.T / self.args.temperature
        return F.cross_entropy(sim, torch.arange(q.shape[0], device=self.device))

    def train(self, loader):
        a = self.args
        opt = torch.optim.AdamW(self.model.parameters(), lr=a.lr, weight_decay=a.weight_decay)
        total = len(loader) * a.num_epochs // a.grad_accum_steps
        if a.max_steps:
            total = min(total, a.max_steps)
        sched = get_cosine_schedule_with_warmup(opt, int(total * a.warmup_ratio), total)
        if self.rank == 0:
            log.info("%d updates, %d warmup, lr %.1e, world size %d", total, int(total * a.warmup_ratio), a.lr,
                     self.world_size)
        step = 0
        for epoch in range(a.num_epochs):
            if isinstance(loader.sampler, DistributedSampler):
                loader.sampler.set_epoch(epoch)
            running = 0.0
            opt.zero_grad()
            for i, (queries, positives, neg_lists) in enumerate(loader):
                negatives = [n for ns in neg_lists for n in ns]
                sync = (i + 1) % a.grad_accum_steps == 0
                if self.world_size > 1 and not sync:
                    with self.model.no_sync():
                        loss = self.loss(queries, positives, negatives) / a.grad_accum_steps
                        loss.backward()
                else:
                    loss = self.loss(queries, positives, negatives) / a.grad_accum_steps
                    loss.backward()
                running += loss.item() * a.grad_accum_steps
                if not sync:
                    continue
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                opt.step()
                sched.step()
                opt.zero_grad()
                step += 1
                if self.rank == 0 and (step % a.log_steps == 0 or step == total):
                    log.info("epoch %d update %d/%d loss %.4f lr %.2e", epoch, step, total, running / (i + 1),
                             sched.get_last_lr()[0])
                if self.rank == 0 and a.save_steps and step % a.save_steps == 0:
                    self.save(Path(a.output_dir) / f"checkpoint-{step}")
                if step >= total:
                    break
        if self.rank == 0:
            self.save(Path(a.output_dir) / "final")

    def save(self, path):
        path.mkdir(parents=True, exist_ok=True)
        self.raw_model.save_pretrained(path)
        self.tok.save_pretrained(path)
        log.info("saved %s", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train_data", required=True, help="output of build_triplets.py")
    ap.add_argument("--base_model", default="Qwen/Qwen3-Embedding-0.6B")
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--max_length", type=int, default=1024)
    ap.add_argument("--batch_size", type=int, default=4, help="requests per GPU per micro-batch")
    ap.add_argument("--grad_accum_steps", type=int, default=4)
    ap.add_argument("--num_epochs", type=int, default=1)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--weight_decay", type=float, default=0.01)
    ap.add_argument("--warmup_ratio", type=float, default=0.05)
    ap.add_argument("--temperature", type=float, default=0.05)
    ap.add_argument("--save_steps", type=int, default=100)
    ap.add_argument("--log_steps", type=int, default=50)
    ap.add_argument("--max_steps", type=int, default=0, help="stop early (0 = full run); for smoke tests")
    ap.add_argument("--max_samples", type=int, default=0, help="use only the first N triplets; for smoke tests")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no_gradient_checkpointing", action="store_true")
    a = ap.parse_args()

    ddp = "LOCAL_RANK" in os.environ
    if ddp:
        dist.init_process_group(backend="nccl")
        local_rank, rank, world_size = int(os.environ["LOCAL_RANK"]), dist.get_rank(), dist.get_world_size()
        device = torch.device(f"cuda:{local_rank}")
        torch.cuda.set_device(device)
    else:
        rank, world_size = 0, 1
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    random.seed(a.seed)
    torch.manual_seed(a.seed)

    tok = AutoTokenizer.from_pretrained(a.base_model, padding_side="left")
    if tok.pad_token is None and tok.eos_token is not None:
        tok.pad_token = tok.eos_token
    model = AutoModel.from_pretrained(a.base_model, dtype=torch.bfloat16)
    if not a.no_gradient_checkpointing:
        model.gradient_checkpointing_enable()
    model.to(device)
    raw = model
    if ddp:
        model = DDP(model, device_ids=[local_rank], find_unused_parameters=True)

    data = TripletDataset(a.train_data)
    if a.max_samples:
        data.records = data.records[:a.max_samples]
    if rank == 0:
        log.info("%d training triplets", len(data))
    sampler = DistributedSampler(data, shuffle=True) if ddp else None
    loader = DataLoader(data, batch_size=a.batch_size, shuffle=sampler is None, sampler=sampler,
                        collate_fn=collate, num_workers=4, pin_memory=True)
    Trainer(model, raw, tok, device, a, rank, world_size).train(loader)
    if ddp:
        dist.barrier()
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
