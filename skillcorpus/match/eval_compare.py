"""Unified evaluation: compare embedding + reranker on multiple skill pools.

Supports three skill pools (tiers):
  - easy:  eval_core easy pool (78,361 skills)
  - hard:  eval_core hard pool (79,141 skills)
  - skillcorpus: 70K skill-corpus training pool + 196 GT skills injected

All tiers use the same 75 eval_core benchmark queries.
Skill embeddings are cached in ./cache/ (override with CACHE_DIR).

Usage:
    CUDA_VISIBLE_DEVICES=0 python3 -u eval_compare.py --tiers easy hard skillcorpus
    CUDA_VISIBLE_DEVICES=0 python3 -u eval_compare.py --phase embed --tiers easy
    CUDA_VISIBLE_DEVICES=0 python3 -u eval_compare.py --phase rerank --tiers skillcorpus
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

import os

# All data locations are relative to this package by default; override via env vars.
SUBMIT_DIR = Path(__file__).resolve().parent
EVAL_ROOT = Path(os.environ.get("EVAL_CORE_DIR", SUBMIT_DIR / "eval_core"))
CACHE = Path(os.environ.get("CACHE_DIR", SUBMIT_DIR / "cache"))
DATA_DIR = Path(os.environ.get("DATA_DIR", SUBMIT_DIR / "data"))
CACHE.mkdir(parents=True, exist_ok=True)

from metrics import compute_all_metrics

DTYPE = torch.bfloat16
MAX_LENGTH = 4096
BATCH_SIZE = 16
TOP_K = 20

QUERY_INSTRUCTION = (
    "Instruct: Given a task description, retrieve the most relevant "
    "skill document that would help an agent complete the task\nQuery:"
)
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

# Only this package's model is registered; override via MODEL_PATH.
EMBED_MODELS = {
    "Ours": os.environ.get("MODEL_PATH", str(SUBMIT_DIR / "outputs" / "embedding" / "final")),
}

RERANK_MODELS = {}

RERANK_PIPELINES = {}

CACHED_SKILL_EMBS = {}


def last_token_pool(hidden, attention_mask):
    left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
    if left_padding:
        return hidden[:, -1]
    seq_len = attention_mask.sum(dim=1) - 1
    bs = hidden.shape[0]
    return hidden[torch.arange(bs, device=hidden.device), seq_len]


def format_skill(skill, desc_max=500, body_max=8000):
    name = skill.get("name", "")
    desc = (skill.get("description") or "")[:desc_max]
    body = (skill.get("body") or "")[:body_max]
    return f"{name} | {desc} | {body}"


def encode_texts(model, tokenizer, texts, device, batch_size=BATCH_SIZE, label=""):
    all_embs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        encoded = tokenizer(
            batch, padding=True, truncation=True,
            max_length=MAX_LENGTH, return_tensors="pt",
        )
        encoded = {k: v.to(device) for k, v in encoded.items()}
        with torch.no_grad():
            out = model(**encoded)
            emb = last_token_pool(out.last_hidden_state, encoded["attention_mask"])
            emb = F.normalize(emb, p=2, dim=1)
        all_embs.append(emb.cpu())
        if label and i > 0 and (i // batch_size) % 100 == 0:
            print(f"    {label}: {i}/{len(texts)}")
    return torch.cat(all_embs, dim=0)


def load_evalcore_tier(tier):
    skills = []
    for p in sorted((EVAL_ROOT / tier).glob("part-*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                skills.append(json.loads(line))
    return skills


def load_skillcorpus_pool():
    """Load the 70K skill-corpus pool + inject 196 GT skills from eval_core."""
    skills = []
    with open(DATA_DIR / "skills.jsonl") as f:
        for line in f:
            if line.strip():
                skills.append(json.loads(line))

    existing_ids = {s["skill_id"] for s in skills}
    gt_injected = 0
    for p in sorted((EVAL_ROOT / "easy").glob("part-*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                s = json.loads(line)
                if s.get("source") == "gt" and s["skill_id"] not in existing_ids:
                    skills.append(s)
                    existing_ids.add(s["skill_id"])
                    gt_injected += 1

    print(f"  skillcorpus pool: {len(skills) - gt_injected} base + {gt_injected} GT injected = {len(skills)}")
    return skills


def load_tier(tier):
    if tier in ("easy", "hard"):
        return load_evalcore_tier(tier)
    elif tier == "skillcorpus":
        return load_skillcorpus_pool()
    else:
        raise ValueError(f"Unknown tier: {tier}")


def load_eval_tasks(rewritten=False):
    fname = "tasks_rewritten.jsonl" if rewritten else "tasks.jsonl"
    tasks = [json.loads(l) for l in open(EVAL_ROOT / fname)]
    rel = json.load(open(EVAL_ROOT / "relevance.json"))
    return tasks, rel


def eval_retrieval(ranked_by_task, tasks, rel, pool_id_set):
    all_metrics = []
    for t in tasks:
        tid = t["task_id"]
        e = rel.get(tid, {})
        if e.get("task_type") == "generic_only":
            continue
        gt = set(e.get("core_gt_ids") or e.get("gt_skill_ids", []))
        gt_in = gt & pool_id_set
        if not gt_in or tid not in ranked_by_task:
            continue
        tier_rel = {k: float(v) for k, v in e.get("relevance", {}).items() if k in pool_id_set}
        m = compute_all_metrics(ranked_by_task[tid], gt_in, tier_rel or None)
        all_metrics.append(m)
    if not all_metrics:
        return {}
    agg = {k: float(np.mean([m[k] for m in all_metrics])) for k in all_metrics[0]}
    agg["count"] = len(all_metrics)
    return agg


# -- Phase 1: Embedding -------------------------------------------------------

def run_embedding_phase(device, tiers, model_names, rewritten=False):
    print("\n" + "=" * 70)
    print("PHASE 1: EMBEDDING RETRIEVAL" + (" [REWRITTEN]" if rewritten else ""))
    print("=" * 70)

    tasks, rel = load_eval_tasks(rewritten)

    tier_data = {}
    for tier in tiers:
        skills = load_tier(tier)
        pool_ids = [s["skill_id"] for s in skills]
        pool_texts = [format_skill(s) for s in skills]
        tier_data[tier] = (skills, pool_ids, pool_texts, set(pool_ids))

    results = {}
    retrieval_results = {}

    for model_name in model_names:
        model_path = EMBED_MODELS[model_name]
        print(f"\n--- {model_name}: {model_path} ---")

        t0 = time.time()
        tokenizer = AutoTokenizer.from_pretrained(model_path, padding_side="left", trust_remote_code=True)
        model = AutoModel.from_pretrained(model_path, dtype=DTYPE, trust_remote_code=True)
        if tokenizer.pad_token is None and tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token
        model.to(device).eval()
        print(f"  Loaded in {time.time()-t0:.1f}s")

        q_texts = [f"{QUERY_INSTRUCTION}{t.get('instruction_text', t.get('query', ''))[:2000]}" for t in tasks]
        q_ids = [t["task_id"] for t in tasks]
        q_embs = encode_texts(model, tokenizer, q_texts, device)
        print(f"  Queries encoded: {q_embs.shape}")

        results[model_name] = {}
        for tier in tiers:
            skills, pool_ids, pool_texts, pool_id_set = tier_data[tier]
            cache_key = (model_name, tier)

            if MAX_LENGTH == 4096:
                cache_path = CACHED_SKILL_EMBS.get(cache_key)
            else:
                cache_path = CACHE / (f"doc_emb_{tier}_{model_name.lower().replace('-','_')}"
                                      f"_desc500_body8000_ml{MAX_LENGTH}.pt")
            if cache_path and cache_path.exists():
                print(f"  [{tier}] Loading cached skill embeddings...")
                skill_embs = torch.load(cache_path, map_location="cpu", weights_only=True)
                skill_embs = skill_embs.float()
                skill_embs = F.normalize(skill_embs, p=2, dim=1)
            else:
                print(f"  [{tier}] Encoding {len(skills)} skills...")
                skill_embs = encode_texts(model, tokenizer, pool_texts, device, label=f"{model_name}-{tier}")
                save_path = cache_path or CACHE / f"doc_emb_{tier}_{model_name.lower().replace('-','_')}_desc500_body8000_ml{MAX_LENGTH}.pt"
                # Save in fp32: a bf16 round-trip flips tasks on the ranking margin
                # (skillcorpus Hit@1 0.5467->0.5733); fp32 keeps cached loads bit-identical to online encoding
                torch.save(skill_embs, save_path)
                print(f"  [{tier}] Saved embeddings to {save_path}")

            assert skill_embs.shape[0] == len(pool_ids), \
                f"Cache mismatch: {skill_embs.shape[0]} embeddings vs {len(pool_ids)} skills"

            print(f"  [{tier}] skill_embs: {skill_embs.shape}")
            sim = q_embs.float() @ skill_embs.float().T

            ranked_by_task = {}
            for qi, tid in enumerate(q_ids):
                _, topk_idx = torch.topk(sim[qi], min(TOP_K, len(pool_ids)))
                ranked_by_task[tid] = [pool_ids[idx] for idx in topk_idx.tolist()]

            retrieval_results[(model_name, tier)] = ranked_by_task

            agg = eval_retrieval(ranked_by_task, tasks, rel, pool_id_set)
            results[model_name][tier] = agg
            print(f"  [{tier}] n={agg.get('count', 0)}")
            for k in ["Hit@1", "nDCG@1", "nDCG@3", "nDCG@10", "MRR@10", "Recall@10", "Recall@50"]:
                print(f"    {k:>12s}: {agg.get(k, 0):.4f}")

        del model
        torch.cuda.empty_cache()
        print(f"  Done in {time.time()-t0:.1f}s")

    return results, retrieval_results


# -- Phase 2: Reranker --------------------------------------------------------

def run_reranker_phase(device, tiers, model_names, retrieval_results, rewritten=False):
    print("\n" + "=" * 70)
    print("PHASE 2: RERANKER (Embed -> Rerank pipeline)" + (" [REWRITTEN]" if rewritten else ""))
    print("=" * 70)

    tasks, rel = load_eval_tasks(rewritten)
    tasks_by_id = {t["task_id"]: t for t in tasks}

    tier_pool_index = {}
    for tier in tiers:
        skills = load_tier(tier)
        tier_pool_index[tier] = {s["skill_id"]: s for s in skills}

    pipelines = []
    for rname in model_names:
        if rname not in RERANK_MODELS:
            continue
        ename = RERANK_PIPELINES.get(rname, rname)
        if ename not in EMBED_MODELS:
            continue
        pipelines.append((ename, rname))

    results = {}

    for embed_name, rerank_name in pipelines:
        rerank_path = RERANK_MODELS[rerank_name]
        label = f"{embed_name}-Emb -> {rerank_name}-Rank"
        print(f"\n--- {label} ---")

        if not Path(rerank_path).exists() and not rerank_path.startswith(("pipizhao/", "Qwen/")):
            print(f"  SKIP — model not found: {rerank_path}")
            continue

        t0 = time.time()
        tokenizer = AutoTokenizer.from_pretrained(rerank_path, padding_side="left", trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(rerank_path, dtype=DTYPE, trust_remote_code=True)
        if tokenizer.pad_token is None and tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token
        model.to(device).eval()

        prefix_t = tokenizer.encode(SYSTEM_PREFIX, add_special_tokens=False)
        suffix_t = tokenizer.encode(SYSTEM_SUFFIX, add_special_tokens=False)
        yes_id = tokenizer.convert_tokens_to_ids("yes")
        no_id = tokenizer.convert_tokens_to_ids("no")
        pad_id = tokenizer.pad_token_id or 0

        print(f"  Loaded in {time.time()-t0:.1f}s")

        results[label] = {}

        for tier in tiers:
            retrieval = retrieval_results.get((embed_name, tier))
            if not retrieval:
                print(f"  [{tier}] SKIP — no retrieval results")
                continue

            pool_index = tier_pool_index[tier]
            pool_id_set = set(pool_index.keys())
            reranked_by_task = {}

            for ti, (tid, candidates) in enumerate(retrieval.items()):
                task = tasks_by_id.get(tid)
                if not task:
                    continue
                query = task["instruction_text"]

                pairs = []
                for sid in candidates:
                    s = pool_index.get(sid)
                    if not s:
                        continue
                    doc = f"{s.get('name','')} | {s.get('description','')[:500]} | {s.get('body','')[:2000]}"
                    prompt = (f"<Instruct>: {RERANK_INSTRUCTION}\n\n"
                              f"<Query>: {query}\n\n"
                              f"<Document>: {doc}")
                    inner_max = MAX_LENGTH - len(prefix_t) - len(suffix_t)
                    inner = tokenizer(prompt, padding=False, truncation=True,
                                      max_length=inner_max, return_attention_mask=False)
                    tokens = prefix_t + inner["input_ids"] + suffix_t
                    pairs.append((sid, tokens))

                if not pairs:
                    reranked_by_task[tid] = candidates
                    continue

                scores = []
                for i in range(0, len(pairs), 8):
                    batch = pairs[i:i + 8]
                    max_len = max(len(t) for _, t in batch)
                    input_ids = torch.full((len(batch), max_len), pad_id, dtype=torch.long)
                    attn = torch.zeros((len(batch), max_len), dtype=torch.long)
                    for j, (_, ids) in enumerate(batch):
                        input_ids[j, max_len - len(ids):] = torch.tensor(ids)
                        attn[j, max_len - len(ids):] = 1
                    with torch.no_grad():
                        out = model(input_ids=input_ids.to(device), attention_mask=attn.to(device))
                    logits = out.logits[:, -1, :]
                    sc = logits[:, yes_id] - logits[:, no_id]
                    scores.extend(sc.float().cpu().tolist())

                sorted_pairs = sorted(zip([s for s, _ in pairs], scores), key=lambda x: -x[1])
                reranked_by_task[tid] = [s for s, _ in sorted_pairs]

                if (ti + 1) % 20 == 0:
                    print(f"    reranked {ti+1}/{len(retrieval)}")

            agg = eval_retrieval(reranked_by_task, tasks, rel, pool_id_set)
            results[label][tier] = agg
            print(f"  [{tier}] n={agg.get('count', 0)}")
            for k in ["Hit@1", "nDCG@1", "nDCG@3", "nDCG@10", "MRR@10", "Recall@10", "Recall@50"]:
                print(f"    {k:>12s}: {agg.get(k, 0):.4f}")

        del model
        torch.cuda.empty_cache()
        print(f"  Done in {time.time()-t0:.1f}s")

    return results


# -- Summary ------------------------------------------------------------------

def print_table(title, results, key_metrics, tiers):
    print(f"\n{'='*90}")
    print(title)
    print(f"{'='*90}")
    names = list(results.keys())
    if not names:
        return
    col_w = max(max(len(n) for n in names) + 2, 12)
    for tier in tiers:
        print(f"\n  --- {tier.upper()} ---")
        header = f"  {'Metric':>12s}" + "".join(f"  {n:>{col_w}s}" for n in names)
        print(header)
        print("  " + "-" * (len(header) - 2))
        for m in key_metrics:
            row = f"  {m:>12s}"
            for n in names:
                v = results.get(n, {}).get(tier, {}).get(m, 0)
                row += f"  {v:>{col_w}.4f}"
            print(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["embed", "rerank", "all"], default="all")
    parser.add_argument("--tiers", nargs="+", choices=["easy", "hard", "skillcorpus"],
                        default=["easy", "hard"])
    parser.add_argument("--models", nargs="+", default=None,
                        help="Model names to evaluate (default: all)")
    parser.add_argument("--rewritten", action="store_true",
                        help="Use LLM-rewritten queries (tasks_rewritten.jsonl)")
    parser.add_argument("--max_length", type=int, default=4096,
                        help="Tokenizer max_length for query/skill encoding (default 4096)")
    args = parser.parse_args()

    global MAX_LENGTH
    MAX_LENGTH = args.max_length
    print(f"Max length: {MAX_LENGTH}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    print(f"Tiers:  {args.tiers}")

    requested = args.models or list(EMBED_MODELS.keys())
    embed_model_names = [m for m in requested if m in EMBED_MODELS]
    rerank_model_names = [m for m in requested if m in RERANK_MODELS]
    # Also include embedding models needed by rerank pipelines
    for rname in rerank_model_names:
        ename = RERANK_PIPELINES.get(rname, rname)
        if ename in EMBED_MODELS and ename not in embed_model_names:
            embed_model_names.append(ename)
    print(f"Models: embed={embed_model_names}, rerank={rerank_model_names}")

    key_metrics = ["Hit@1", "nDCG@1", "nDCG@3", "nDCG@10", "MRR@10",
                   "Recall@10", "Recall@20", "Recall@50"]

    embed_results = {}
    rerank_results = {}
    retrieval_results = {}

    if args.rewritten:
        print("Using REWRITTEN queries")

    if args.phase in ("embed", "all"):
        embed_results, retrieval_results = run_embedding_phase(device, args.tiers, embed_model_names, args.rewritten)

    if args.phase in ("rerank", "all"):
        rerank_results = run_reranker_phase(device, args.tiers, rerank_model_names, retrieval_results, args.rewritten)

    if embed_results:
        print_table("EMBEDDING RETRIEVAL (top-20)", embed_results, key_metrics, args.tiers)
    if rerank_results:
        print_table("FULL PIPELINE (Embed -> Rerank top-20)", rerank_results, key_metrics, args.tiers)

    suffix = "" if args.max_length == 4096 else f"_ml{args.max_length}"
    out_path = SUBMIT_DIR / "outputs" / "eval" / f"comparison_results{suffix}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "embedding": embed_results,
        "pipeline": rerank_results,
    }, indent=2, ensure_ascii=False))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
