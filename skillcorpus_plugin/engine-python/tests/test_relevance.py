from __future__ import annotations

from typing import Any

import pytest

from skillsearch.relevance import check_keyword_relevance, query_terms
from skillsearch.sources.hub_source import HubSkillSource


def test_forced_unrelated_top_k_is_rejected() -> None:
    result = check_keyword_relevance(
        "zqxjkv no such task 93847", name="get-task", description="Get a task by ID", tags=[]
    )
    assert result["passed"] is False


def test_alias_and_core_object_are_matched() -> None:
    assert query_terms("Please deploy this to K8s") == ["deploy", "kubernetes"]
    result = check_keyword_relevance(
        "Please deploy this to K8s", name="Kubernetes deployment",
        description="Deploy workloads to a Kubernetes cluster", tags=[]
    )
    assert result["passed"] is True


class Catalog:
    async def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        del query, limit
        return [
            {"id": "bad", "name": "get-task", "description": "Get a task by ID", "quality_score": 0.9},
            {"id": "one", "name": "PDF table extractor",
             "description": "Extract tables from PDF", "quality_score": 0.8},
            {"id": "two", "name": "PDF parser", "description": "Parse PDF table data", "quality_score": 0.7},
            {"id": "three", "name": "PDF OCR", "description": "OCR scanned PDF", "quality_score": 0.7},
        ]


@pytest.mark.asyncio
async def test_hub_filters_forced_hits_and_returns_at_most_two() -> None:
    source = HubSkillSource(Catalog())  # type: ignore[arg-type]
    hits = await source.search("pdf table extraction", [], 10)
    assert [hit.qualified_id for hit in hits] == ["hub/one", "hub/two"]


def test_chinese_query_is_segmented_and_matched_without_rewriter() -> None:
    result = check_keyword_relevance(
        "帮我提取PDF表格", name="PDF 表格提取", description="从 PDF 文档中提取表格数据", tags=[]
    )
    assert result["passed"] is True


def test_kubernetes_is_not_mangled_by_plural_normalization() -> None:
    assert query_terms("kubernetes deployment") == ["kubernetes", "deployment"]
    result = check_keyword_relevance(
        "kubernetes deployment", name="Kubernetes", description="Manage Kubernetes deployments", tags=[]
    )
    assert result["passed"] is True
