from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from skillsearch.sources.marketplace_source import MarketplaceClient, MarketplaceSkillSource


def bundle() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("demo/SKILL.md", "---\nname: demo\ndescription: demo\n---\n\nUse the demo workflow.\n")
        archive.writestr("demo/scripts/run.sh", "echo ok\n")
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "search_path", "payload"),
    [
        (
            "clawhub",
            "/api/v1/search",
            {
                "results": [
                    {
                        "id": "one",
                        "slug": "demo",
                        "displayName": "Demo",
                        "summary": "Useful demo",
                        "score": 0.9,
                        "ownerHandle": "alice",
                        "trust": {"installability": "installable"},
                    }
                ]
            },
        ),
        (
            "skillhub_cn",
            "/api/skills",
            {
                "code": 0,
                "data": {
                    "skills": [
                        {"slug": "demo", "name": "Demo", "description": "Useful demo", "score": 8.0, "version": "1.0.0"}
                    ]
                },
            },
        ),
    ],
)
async def test_marketplace_search_caps_and_installs(tmp_path: Path, kind: str, search_path: str, payload: dict) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == search_path:
            return httpx.Response(200, json=payload)
        if request.url.path == "/api/v1/download":
            return httpx.Response(200, content=bundle())
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MarketplaceClient(kind, "https://example.test", cache_dir=tmp_path, client=http)
    source = MarketplaceSkillSource(client)
    hits = await source.search("demo", [], 9)
    assert len(hits) == 1
    assert hits[0].meta["source"] == kind
    installed = await client.install(hits[0])
    assert installed["skill_md"] == "Use the demo workflow.\n"
    assert Path(installed["dir"], "scripts", "run.sh").is_file()
    assert any(request.url.path == "/api/v1/download" for request in seen)
    await http.aclose()


@pytest.mark.asyncio
async def test_clawhub_rejects_suspicious_results(tmp_path: Path) -> None:
    payload = {"results": [{"slug": "bad", "displayName": "Bad", "native": {"skill": {"isSuspicious": True}}}]}
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    client = MarketplaceClient("clawhub", "https://example.test", cache_dir=tmp_path, client=http)
    assert await client.search("bad") == []
    await http.aclose()
