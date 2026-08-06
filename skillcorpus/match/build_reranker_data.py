"""Step 5b: Build reranker training data (v2).

Difference from v1 (build_reranker_data.py):
  - Inject ground-truth positive when encoder misses it in top-K
    (v1 drops the query entirely; v2 keeps it by forcing the positive in)

Same as v1: encoder top-K retrieval, three-layer FN filtering, no shuffle.

Usage:
    python3 build_reranker_data.py \
        --queries data/queries.jsonl \
        --skills data/skills.jsonl \
        --encoder_path /path/to/encoder \
        --output data/reranker_train.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.request
from pathlib import Path

import torch
import torch.nn.functional as F

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

QUERY_INSTRUCTION = (
    "Instruct: Given a task description, retrieve the most relevant "
    "skill document that would help an agent complete the task\nQuery:"
)


def remote_encode(texts: list[str], embed_url: str, batch_size: int = 256) -> torch.Tensor:
    url = f"{embed_url}/embed"
    all_embs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        payload = json.dumps({"texts": batch}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
        all_embs.extend(result["embeddings"])
        if i > 0 and (i // batch_size) % 10 == 0:
            log.info("  encoded %d / %d", i, len(texts))
    return torch.tensor(all_embs, dtype=torch.float32)


def last_token_pool(hidden, mask):
    if mask[:, -1].sum() == mask.shape[0]:
        return hidden[:, -1]
    seq_len = mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), seq_len]


def local_encode(model, tokenizer, texts, max_length, batch_size, device):
    model.eval()
    all_embs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        enc = tokenizer(batch, padding=True, truncation=True,
                        max_length=max_length, return_tensors="pt")
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            out = model(**enc)
            embs = last_token_pool(out.last_hidden_state, enc["attention_mask"])
            embs = F.normalize(embs, p=2, dim=1)
        all_embs.append(embs.cpu())
        if (i // batch_size) % 100 == 0 and i > 0:
            log.info("  encoded %d / %d", i, len(texts))
    return torch.cat(all_embs, dim=0)


def format_skill_text(s: dict, desc_max=500, body_max=8000) -> str:
    name = s.get("name", "")
    desc = (s.get("description") or "")[:desc_max]
    body = (s.get("body") or "")[:body_max]
    return f"{name} | {desc} | {body}"


def char_trigrams(text: str) -> set[str]:
    text = text.lower()
    return {text[i:i+3] for i in range(len(text) - 2)}


def trigram_jaccard(a: str, b: str) -> float:
    sa, sb = char_trigrams(a), char_trigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def passes_fn_filter(neg_skill: dict, pos_skill: dict,
                     pos_emb: torch.Tensor, neg_emb: torch.Tensor,
                     jaccard_threshold: float, cosine_threshold: float) -> bool:
    """Three-layer false negative filter. Returns True if the negative is clean."""
    if neg_skill["name"].lower() == pos_skill["name"].lower():
        return False
    neg_body = neg_skill.get("body", "")
    pos_body = pos_skill.get("body", "")
    if trigram_jaccard(pos_body, neg_body) > jaccard_threshold:
        return False
    if neg_emb is not None and pos_emb is not None:
        cos_sim = float(F.cosine_similarity(pos_emb.unsqueeze(0), neg_emb.unsqueeze(0)))
        if cos_sim > cosine_threshold:
            return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--skills", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoder_path", type=str, default=None)
    parser.add_argument("--embed_url", type=str, default=None)
    parser.add_argument("--top_k", type=int, default=20)
    parser.add_argument("--max_length", type=int, default=4096)
    parser.add_argument("--batch_size", type=int, default=32)
    parser.add_argument("--jaccard_threshold", type=float, default=0.6)
    parser.add_argument("--cosine_threshold", type=float, default=0.92)
    parser.add_argument("--cache_dir", type=Path, default=None,
                        help="Directory to cache skill/query embeddings as .pt files")
    parser.add_argument("--skill_emb_cache", type=Path, default=None,
                        help="Pre-computed skill embeddings .pt file (skips skill encoding)")
    args = parser.parse_args()

    if not args.encoder_path and not args.embed_url:
        parser.error("Must specify either --encoder_path or --embed_url")

    args.output.parent.mkdir(parents=True, exist_ok=True)

    queries = []
    with open(args.queries) as f:
        for line in f:
            if line.strip():
                queries.append(json.loads(line))
    log.info("Loaded %d queries", len(queries))

    skills = []
    with open(args.skills) as f:
        for line in f:
            if line.strip():
                skills.append(json.loads(line))
    log.info("Loaded %d skills", len(skills))

    skill_id_to_idx = {s["skill_id"]: i for i, s in enumerate(skills)}

    # Encode with caching
    cache_dir = args.cache_dir
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    skill_cache_path = cache_dir / "skill_embs.pt" if cache_dir else None
    query_cache_path = cache_dir / "query_embs.pt" if cache_dir else None

    # Skill embeddings: prefer --skill_emb_cache > cache_dir > encode
    skill_embs = None
    if args.skill_emb_cache and args.skill_emb_cache.exists():
        log.info("Loading pre-computed skill embeddings: %s", args.skill_emb_cache)
        skill_embs = torch.load(args.skill_emb_cache, weights_only=True).float()
        log.info("Loaded skill_embs %s", skill_embs.shape)
    elif skill_cache_path and skill_cache_path.exists():
        log.info("Loading cached skill embeddings: %s", skill_cache_path)
        skill_embs = torch.load(skill_cache_path, weights_only=True).float()
        log.info("Loaded skill_embs %s", skill_embs.shape)

    # Query embeddings: check cache_dir
    query_embs = None
    if query_cache_path and query_cache_path.exists():
        log.info("Loading cached query embeddings: %s", query_cache_path)
        query_embs = torch.load(query_cache_path, weights_only=True).float()
        log.info("Loaded query_embs %s", query_embs.shape)

    # Encode whatever is missing
    if skill_embs is None or query_embs is None:
        if args.embed_url:
            log.info("Using remote encoder: %s", args.embed_url)
            if skill_embs is None:
                skill_texts = [format_skill_text(s) for s in skills]
                log.info("Encoding %d skills...", len(skills))
                skill_embs = remote_encode(skill_texts, args.embed_url, args.batch_size)
            if query_embs is None:
                query_texts = [f"{QUERY_INSTRUCTION}{q['instruction_text'][:2000]}" for q in queries]
                log.info("Encoding %d queries...", len(queries))
                query_embs = remote_encode(query_texts, args.embed_url, args.batch_size)
        elif args.encoder_path:
            from transformers import AutoModel, AutoTokenizer
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            log.info("Loading encoder: %s", args.encoder_path)
            tokenizer = AutoTokenizer.from_pretrained(
                args.encoder_path, trust_remote_code=True, padding_side="left")
            model = AutoModel.from_pretrained(
                args.encoder_path, trust_remote_code=True, torch_dtype=torch.bfloat16)
            if tokenizer.pad_token is None and tokenizer.eos_token is not None:
                tokenizer.pad_token = tokenizer.eos_token
            model.to(device).eval()

            if skill_embs is None:
                skill_texts = [format_skill_text(s) for s in skills]
                log.info("Encoding %d skills...", len(skills))
                skill_embs = local_encode(model, tokenizer, skill_texts, args.max_length, args.batch_size, device)
            if query_embs is None:
                query_texts = [f"{QUERY_INSTRUCTION}{q['instruction_text'][:2000]}" for q in queries]
                log.info("Encoding %d queries...", len(queries))
                query_embs = local_encode(model, tokenizer, query_texts, args.max_length, args.batch_size, device)

            del model, tokenizer
            torch.cuda.empty_cache()
        else:
            raise RuntimeError("Need --encoder_path or --embed_url to encode missing embeddings")

        # Save to cache_dir
        if skill_cache_path and not skill_cache_path.exists():
            torch.save(skill_embs, skill_cache_path)
        if query_cache_path and not query_cache_path.exists():
            torch.save(query_embs, query_cache_path)
        if cache_dir:
            log.info("Cached embeddings to %s", cache_dir)

    log.info("Computing retrieval and building reranker data...")
    skill_embs = skill_embs.float()
    query_embs = query_embs.float()
    sim_matrix = query_embs @ skill_embs.T

    out_f = open(args.output, "w")
    total = 0
    stats = {"pos_injected": 0, "fn_filtered": 0}
    t0 = time.time()

    for qi, query in enumerate(queries):
        pos_skill_id = query["skill_id"]
        pos_idx = skill_id_to_idx.get(pos_skill_id)
        if pos_idx is None:
            continue

        pos_skill = skills[pos_idx]
        pos_emb = skill_embs[pos_idx]

        sims = sim_matrix[qi]
        _, topk_idx = torch.topk(sims, min(args.top_k + 10, len(skills)))
        topk_idx = topk_idx.tolist()

        candidates = []
        pos_in_topk = False

        for idx in topk_idx:
            if len(candidates) >= args.top_k:
                break

            s = skills[idx]
            is_positive = (s["skill_id"] == pos_skill_id)

            if is_positive:
                pos_in_topk = True
                candidates.append({
                    "skill_id": s["skill_id"],
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "body": s.get("body", ""),
                    "label": 1,
                })
                continue

            if not passes_fn_filter(s, pos_skill, pos_emb, skill_embs[idx],
                                    args.jaccard_threshold, args.cosine_threshold):
                stats["fn_filtered"] += 1
                continue

            candidates.append({
                "skill_id": s["skill_id"],
                "name": s["name"],
                "description": s.get("description", ""),
                "body": s.get("body", ""),
                "label": 0,
            })

        # v2: inject positive if encoder missed it
        if not pos_in_topk:
            stats["pos_injected"] += 1
            candidates.append({
                "skill_id": pos_skill_id,
                "name": pos_skill["name"],
                "description": pos_skill.get("description", ""),
                "body": pos_skill.get("body", ""),
                "label": 1,
            })

        has_pos = any(c["label"] == 1 for c in candidates)
        has_neg = any(c["label"] == 0 for c in candidates)
        if not has_pos or not has_neg:
            continue

        record = {
            "query_id": query["query_id"],
            "instruction_text": query["instruction_text"],
            "candidates": candidates,
        }
        out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
        total += 1

        if (qi + 1) % 2000 == 0:
            log.info("Progress: %d/%d, %d valid records, %.1f q/s",
                     qi + 1, len(queries), total,
                     (qi + 1) / (time.time() - t0))

    out_f.close()
    log.info("Done. %d reranker training records saved to %s", total, args.output)
    log.info("Stats: %s", json.dumps(stats))
    log.info("  pos_injected: %d queries where positive was forced in (not in encoder top-K)",
             stats["pos_injected"])


if __name__ == "__main__":
    main()
