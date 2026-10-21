#!/usr/bin/env python3
"""Generate hypothetical skills for requests and pseudo-requests for skills (Section 3.2, Section 4).

Example (vLLM serving Qwen3.6-27B at http://localhost:8000):
  python scripts/generate_expansions.py --api http://localhost:8000/v1/chat/completions \
      --requests data/sra/requests.jsonl --hypothetical_out data/sra/hypothetical_skills.json
  python scripts/generate_expansions.py --api http://localhost:8000/v1/chat/completions \
      --pool data/unipool/pool.jsonl --pseudo_out data/unipool/pseudo_requests.json
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.data import load_jsonl
from skillrep import generation as G


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True, help="OpenAI-compatible chat completions URL")
    ap.add_argument("--model", default="Qwen3.6-27B")
    ap.add_argument("--api_key", default=os.environ.get("LLM_API_KEY"))
    ap.add_argument("--workers", type=int, default=64)
    ap.add_argument("--requests")
    ap.add_argument("--hypothetical_out")
    ap.add_argument("--pool")
    ap.add_argument("--pseudo_out")
    a = ap.parse_args()
    if a.requests and a.hypothetical_out:
        reqs = load_jsonl(a.requests)
        out = G.generate(G.hypothetical_jobs(reqs), a.hypothetical_out, a.api, a.model,
                         G.HYPOTHETICAL_SYSTEM, max_tokens=600, workers=a.workers, api_key=a.api_key)
        print(f"hypothetical skills: {sum(bool(t) for v in out.values() for t in v)} texts for {len(out)} requests")
    if a.pool and a.pseudo_out:
        pool = load_jsonl(a.pool)
        out = G.generate(G.pseudo_jobs(pool), a.pseudo_out, a.api, a.model,
                         G.PSEUDO_SYSTEM, max_tokens=120, workers=a.workers, api_key=a.api_key)
        print(f"pseudo-requests: {sum(bool(t) for v in out.values() for t in v)} texts for {len(out)} skills")


if __name__ == "__main__":
    main()
