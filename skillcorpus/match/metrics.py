from __future__ import annotations

import numpy as np


def dcg_at_k(relevances: list[float], k: int) -> float:
    return sum(rel / np.log2(i + 2) for i, rel in enumerate(relevances[:k]))


def ndcg_at_k(relevances: list[float], ideal_relevances: list[float], k: int) -> float:
    dcg = dcg_at_k(relevances, k)
    ideal_dcg = dcg_at_k(sorted(ideal_relevances, reverse=True), k)
    return dcg / ideal_dcg if ideal_dcg > 0 else 0.0


def mrr_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    for i, rid in enumerate(ranked_ids[:k]):
        if rid in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def hit_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    return 1.0 if any(rid in relevant_ids for rid in ranked_ids[:k]) else 0.0


def precision_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    hits = sum(1 for rid in ranked_ids[:k] if rid in relevant_ids)
    return hits / k if k > 0 else 0.0


def recall_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    return len(set(ranked_ids[:k]) & relevant_ids) / len(relevant_ids)


def full_coverage_at_k(ranked_ids: list[str], required_ids: set[str], k: int) -> float:
    if not required_ids:
        return 1.0
    return 1.0 if required_ids.issubset(set(ranked_ids[:k])) else 0.0


def compute_all_metrics(
    ranked_ids: list[str],
    gt_skill_ids: set[str],
    relevance_map: dict[str, float] | None = None,
) -> dict[str, float]:
    if relevance_map:
        relevances = [float(relevance_map.get(rid, 0)) for rid in ranked_ids]
        all_relevance_values = list(relevance_map.values())
    else:
        relevances = [1.0 if rid in gt_skill_ids else 0.0 for rid in ranked_ids]
        all_relevance_values = [1.0] * len(gt_skill_ids) + [0.0] * max(0, len(ranked_ids) - len(gt_skill_ids))

    return {
        "nDCG@1": ndcg_at_k(relevances, all_relevance_values, 1),
        "nDCG@3": ndcg_at_k(relevances, all_relevance_values, 3),
        "nDCG@10": ndcg_at_k(relevances, all_relevance_values, 10),
        "Hit@1": hit_at_k(ranked_ids, gt_skill_ids, 1),
        "Precision@3": precision_at_k(ranked_ids, gt_skill_ids, 3),
        "MRR@10": mrr_at_k(ranked_ids, gt_skill_ids, 10),
        "Recall@10": recall_at_k(ranked_ids, gt_skill_ids, 10),
        "Recall@20": recall_at_k(ranked_ids, gt_skill_ids, 20),
        "Recall@50": recall_at_k(ranked_ids, gt_skill_ids, 50),
        "FullCoverage@3": full_coverage_at_k(ranked_ids, gt_skill_ids, 3),
        "FullCoverage@5": full_coverage_at_k(ranked_ids, gt_skill_ids, 5),
        "FullCoverage@10": full_coverage_at_k(ranked_ids, gt_skill_ids, 10),
    }
