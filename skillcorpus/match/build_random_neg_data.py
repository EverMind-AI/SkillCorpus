"""Build embedding training data with only random negatives (no filtering).

For each (query, positive_skill) pair, samples N random negatives from the skill pool.
No false negative filtering applied.

Usage:
    python3 build_random_neg_data.py \
        --queries data/queries.jsonl \
        --skills data/skills.jsonl \
        --output data/train_triplets_v6.jsonl \
        --num_neg 3
"""

import argparse
import json
import random
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--skills", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--num_neg", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    queries = []
    with open(args.queries) as f:
        for line in f:
            if line.strip():
                queries.append(json.loads(line))
    print(f"Loaded {len(queries)} queries")

    skills = []
    with open(args.skills) as f:
        for line in f:
            if line.strip():
                skills.append(json.loads(line))
    print(f"Loaded {len(skills)} skills")

    skill_id_to_idx = {s["skill_id"]: i for i, s in enumerate(skills)}

    total = 0
    skipped = 0

    with open(args.output, "w") as out_f:
        for qi, query in enumerate(queries):
            pos_skill_id = query["skill_id"]
            pos_idx = skill_id_to_idx.get(pos_skill_id)
            if pos_idx is None:
                skipped += 1
                continue

            pos_skill = skills[pos_idx]

            neg_indices = []
            seen = {pos_idx}
            while len(neg_indices) < args.num_neg:
                ci = random.randint(0, len(skills) - 1)
                if ci not in seen:
                    seen.add(ci)
                    neg_indices.append(ci)

            record = {
                "query_id": query["query_id"],
                "skill_id": pos_skill_id,
                "instruction_text": query["instruction_text"],
                "positive": {
                    "skill_id": pos_skill_id,
                    "name": pos_skill["name"],
                    "description": pos_skill.get("description", ""),
                    "body": pos_skill.get("body", ""),
                },
                "negatives": [
                    {
                        "skill_id": skills[ni]["skill_id"],
                        "name": skills[ni]["name"],
                        "description": skills[ni].get("description", ""),
                        "body": skills[ni].get("body", ""),
                    }
                    for ni in neg_indices
                ],
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            total += 1

            if (qi + 1) % 5000 == 0:
                print(f"Progress: {qi + 1}/{len(queries)}")

    print(f"Done. {total} records written to {args.output} (skipped {skipped})")


if __name__ == "__main__":
    main()
