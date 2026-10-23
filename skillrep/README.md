# SkillRep

Code for *SkillRep: Characterizing and Understanding the Representation Design Space of Agent Skill
Retrieval*.

SkillRep combines five representation choices for retrieving agent skills as modules of one retriever:

| Module | What it does | Code |
|---|---|---|
| Encoder fine-tuning | contrastive fine-tuning of Qwen3-Embedding-0.6B on synthesized request-skill pairs | `train/` |
| Query-side expansion | mixes LLM-written *hypothetical skills* into the request embedding (w = 0.3) | `skillrep/pipeline.py` |
| Document-side expansion | mixes LLM-written *pseudo-requests* into each skill embedding (u = 0.2) | `skillrep/pipeline.py` |
| Background projection | removes the top-2 singular directions of the skill embeddings, then two directions of the library's most generic words | `skillrep/background.py` |
| Lexical channel | BM25 over name, description and body, fused with the dense channel by z-scored scores (alpha = 0.3) | `skillrep/lexical.py`, `skillrep/fusion.py` |

The analyses measure the request-skill gap with three per-request quantities, the background, the
vocabulary a request leaves unsaid (coverage) and dilution, and relate each module's gain to them
(`skillrep/measures.py`, `analysis/`).

This repository contains code only. The benchmarks are public:
[SRA-Bench](https://huggingface.co/datasets/WeihangSu/SRA-Bench),
[SkillsBench](https://huggingface.co/datasets/pipizhao/SkillRouter-Eval-Core) (the retrieval version released
with SkillRouter) and [SkillRet](https://huggingface.co/datasets/ThakiCloud/SKILLRET). UniPool, the
111,247-skill pool built from public registries, is coming soon; until then, the results on UniPool
(the UniPool results of the main tables and all analyses) cannot be rerun,
while those on the official SRA-Bench pool, SkillsBench and SkillRet can. The fine-tuned encoder is
available at [EverMind-AI/skillrep-embedding-0.6b](https://huggingface.co/EverMind-AI/skillrep-embedding-0.6b).

## Layout

```
skillrep/    library: data formats, encoder, LLM generation, BM25, background projection, fusion,
             the retrieval pipeline, metrics, and the three quantities
scripts/     entry points: generate texts, encode, run SkillRep, ablation
analysis/    the analyses of the paper
train/       encoder fine-tuning (request synthesis, hard negatives, training)
```

## Setup

```
pip install -e .
```

`requirements.txt` lists the same dependencies and the versions the code was tested with.

A GPU is needed for encoding; retrieval on a 111K-skill pool fits in a few GB of GPU memory. Text
generation uses Qwen3.6-27B behind an OpenAI-compatible endpoint (for example vLLM).

## Data formats

```
pool.jsonl       {"pid": int, "name": str, "description": str, "body": str}
requests.jsonl   {"task": str, "query": str, "gt_pids": [int, ...], "dataset": str}
hypothetical     {task: [text, text]}          hypothetical skills of each request
pseudo           {pid (as str): [text, text]}  pseudo-requests of each skill
```

Metrics are macro-averaged over the `dataset` field (SRA-Bench's six source datasets); a benchmark
without it is one group. R@10 is the share of a request's gold skills in the top ten, which on
SkillsBench gives the same numbers as SkillRouter's scoring script.

## Running SkillRep

1. Generate the texts of the two expansion modules (temperature 0.3, thinking disabled):

```
python scripts/generate_expansions.py --api <chat completions URL> \
    --requests data/sra/requests.jsonl --hypothetical_out data/sra/hypothetical_skills.json
python scripts/generate_expansions.py --api <chat completions URL> \
    --pool data/unipool/pool.jsonl --pseudo_out data/unipool/pseudo_requests.json
```

2. Encode skill bodies, requests, hypothetical skills and pseudo-requests with the fine-tuned encoder
   (`--instruction untuned` for the untuned Qwen3-Embedding-0.6B):

```
python scripts/encode.py --model EverMind-AI/skillrep-embedding-0.6b --pool data/unipool/pool.jsonl \
    --requests data/sra/requests.jsonl --hypothetical data/sra/hypothetical_skills.json \
    --pseudo data/unipool/pseudo_requests.json --out_dir emb/unipool_sra_ft
```

3. Retrieve and evaluate (R@10, MRR@10, Hit@1):

```
python scripts/run_skillrep.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
    --emb_dir emb/unipool_sra_ft --hypothetical data/sra/hypothetical_skills.json \
    --out results/unipool_full.json
```

Every module can be switched off: `--w 0` (query-side expansion, including its text in the BM25
query), `--u 0`, `--svd_rank 0 --generic_rank 0`, `--no_lexical`.

From Python:

```python
from skillrep import SkillRep, SkillRepConfig, evaluate
from skillrep.data import load_embeddings, load_json, load_jsonl

pool, reqs = load_jsonl("data/unipool/pool.jsonl"), load_jsonl("data/sra/requests.jsonl")
emb = load_embeddings("emb/unipool_sra_ft", reqs, pool)
sr = SkillRep(pool, emb["docs"], SkillRepConfig(), pseudo=emb["pseudo"], device="cuda:0")
rankings = sr.search(reqs, emb["requests"], hypothetical=emb["hypothetical"],
                     hypothetical_texts=load_json("data/sra/hypothetical_skills.json"))
print(evaluate(rankings, reqs))
```

## Reproducing the paper

All analyses use SRA-Bench's 5,400 requests on UniPool and take the same arguments: `--pool`,
`--requests`, `--hypothetical`, `--emb_ft` and `--emb_untuned` (outputs of `scripts/encode.py` for the
two encoders), `--device` and `--out`. Each script's docstring gives its exact command.

| Result | Script |
|---|---|
| Main results (SkillRep on SRA-Bench, SkillsBench and SkillRet) | `scripts/run_skillrep.py` (`--w 0` for "w/o query-side exp.") |
| Ablation (each module alone and last) | `scripts/ablation.py --emb finetuned=... --emb untuned=...` |
| The three quantities, their correlations, and the partial correlations of each module's gain with them | `analysis/quantities.py`, then `analysis/module_gains.py`, then `analysis/partial_correlations.py` |
| The background and the background gap | `analysis/background.py` |
| Deleting words from the hypothetical skills | `analysis/qe_vocabulary.py` |
| Which retriever dilution hurts | `analysis/encode_sections.py` (both modes), then `analysis/dilution.py` |
| Padding the gold documents | `analysis/padding.py` |
| The lexical channel's mechanism | `analysis/lexical_mechanism.py` |
| Why the modules overlap | `analysis/module_overlap.py` |
| Score against rank fusion | `analysis/fusion.py` |
| The expansion weights | `analysis/weights.py`, `analysis/weights_coverage.py`, `analysis/weights_encode_views.py` |
| Background projection: controls and dimensionality | `analysis/background_dims.py`; the gap for 1-6 directions in `analysis/background.py` |
| The three quantities beyond skills | `analysis/beyond_skills.py` |
| Gating modules per request | `analysis/gating.py` |

## Notes

- Requests and pseudo-requests are encoded with an instruction followed by one space
  (`skillrep/data.py`); skill bodies (first 8,000 characters) and hypothetical skills are encoded
  without one. Pseudo-requests use at most 512 tokens, everything else 4,096.
- The encoder runs in bfloat16 with left padding, so embeddings shift slightly with batch
  composition, and BM25 is recomputed rather than read from cached lists; reproduced numbers can
  differ from the paper in the second decimal.
- BM25 uses k1 = 1.5 and b = 0.75 over name, description and the first 8,000 characters of the body.
  With query-side expansion on, its query is the request followed by the hypothetical skills.
