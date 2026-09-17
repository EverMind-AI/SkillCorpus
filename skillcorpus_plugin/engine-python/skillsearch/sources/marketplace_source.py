"""Public ClawHub and skillhub.cn adapters with safe bundle caching."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx

from skillsearch import provenance
from skillsearch.hub_client import SkillHubClient
from skillsearch.local_store import _parse_frontmatter
from skillsearch.provenance import identity
from skillsearch.types import RouterHit

MarketplaceKind = Literal["clawhub", "skillhub_cn"]


class MarketplaceClient:
    def __init__(
        self,
        kind: MarketplaceKind,
        endpoint: str,
        *,
        cache_dir: Path,
        # Set to the shared skills directory to install there instead of into
        # the cache. A skill under the cache is deliberately outside every
        # scan; one here is scanned, shared with the other hosts, and carries
        # a provenance marker so fusion knows it is the catalogue's own entry.
        install_root: Path | None = None,
        timeout_s: float = 5.0,
        download_timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.kind = kind
        self._base = endpoint.rstrip("/")
        self._cache_dir = cache_dir
        self._install_root = install_root
        self._download_timeout_s = download_timeout_s
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, query: str, *, limit: int = 2) -> list[dict[str, Any]]:
        return await (
            self._search_clawhub(query, limit) if self.kind == "clawhub" else self._search_skillhub_cn(query, limit)
        )

    async def install(self, hit: RouterHit) -> dict[str, str]:
        slug = str(hit.meta.get("slug") or hit.meta.get("id"))
        owner = str(hit.meta.get("owner") or "")
        version = str(hit.meta.get("version") or "v0")
        if self._install_root is not None:
            return await self._install_shared(hit, slug, owner, version)
        key = re.sub(r"[^A-Za-z0-9_.@-]+", "_", f"{self.kind}-{owner + '_' if owner else ''}{slug}@{version}")
        destination = self._cache_dir / key
        was_cached = destination.exists()
        if not was_cached:
            staging = destination.with_name(f"{destination.name}.incoming-{os.getpid()}-{uuid.uuid4().hex[:8]}")
            try:
                SkillHubClient._safe_extract(await self._download(slug, owner, version), staging)
                try:
                    staging.rename(destination)
                except OSError:
                    if not destination.exists():
                        raise
                    shutil.rmtree(staging, ignore_errors=True)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        try:
            root = SkillHubClient._bundle_root(destination)
            text = (root / "SKILL.md").read_text(encoding="utf-8")
            _, body = _parse_frontmatter(text)
            return {"dir": str(root), "skill_md": body}
        except (OSError, UnicodeError):
            # A directory is a cache hit only when it contains a readable
            # SKILL.md. Remove incomplete/corrupt entries so one interrupted
            # install cannot poison every later turn. Retry a pre-existing
            # cache once; a freshly downloaded bad archive still fails.
            shutil.rmtree(destination, ignore_errors=True)
            if was_cached:
                return await self.install(hit)
            raise

    async def _install_shared(self, hit: RouterHit, slug: str, owner: str, version: str) -> dict[str, str]:
        """Install into the shared directory, one directory per identity.

        Per identity rather than per version, unlike the cache: the shared
        directory is scanned, so ``x@1`` beside ``x@2`` would put two ranked
        copies of one skill in front of the model. An update replaces, and
        `swap_into_place` keeps the previous version live until the new one is
        complete.

        A same-version reinstall is skipped, so a retrieval that happens to hit
        an installed skill does not rewrite it every turn.
        """
        install_root = self._install_root
        if install_root is None:  # pragma: no cover - unreachable via `install`
            raise RuntimeError("no install root")
        marketplace_slug = f"{owner}_{slug}" if owner else slug
        dest = provenance.slug_dir(install_root, self.kind, marketplace_slug)
        existing = provenance.read_marker(dest)
        if existing is None or existing.version != version:
            staging = dest.with_name(f"{dest.name}.incoming-{os.getpid()}-{uuid.uuid4().hex[:8]}")
            try:
                SkillHubClient._safe_extract(await self._download(slug, owner, version), staging)
                body_root = SkillHubClient._bundle_root(staging)
                text = (body_root / "SKILL.md").read_text(encoding="utf-8")
                _, body = _parse_frontmatter(text)
                provenance.write_marker(
                    body_root,
                    provenance.Origin(
                        origin=provenance.identity(self.kind, marketplace_slug),
                        source=self.kind,
                        slug=marketplace_slug,
                        version=version,
                        sha256=provenance.body_digest(body),
                        installed_at=provenance.now(),
                    ),
                )
                provenance.swap_into_place(staging, dest)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        root = SkillHubClient._bundle_root(dest)
        text = (root / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(text)
        return {"dir": str(root), "skill_md": body}

    async def _search_clawhub(self, query: str, limit: int) -> list[dict[str, Any]]:
        response = await self._client.get(
            f"{self._base}/api/v1/search",
            params={
                "q": query,
                "limit": limit,
                "nonSuspiciousOnly": "true",
            },
        )
        response.raise_for_status()
        output = []
        for raw in (response.json() or {}).get("results", []):
            skill = (raw.get("native") or {}).get("skill") or {}
            trust = raw.get("trust") or {}
            slug = str(raw.get("slug") or "")
            if not slug or skill.get("isSuspicious") or trust.get("visibility") == "blocked":
                continue
            if trust.get("installability") not in (None, "installable"):
                continue
            output.append(
                {
                    "id": raw.get("id") or slug,
                    "slug": slug,
                    "name": raw.get("displayName") or slug,
                    "description": raw.get("summary") or skill.get("summary") or "",
                    "score": float(raw.get("score") or 0),
                    "owner": raw.get("ownerHandle") or "",
                    "version": raw.get("version") or skill.get("latestVersionId") or "v0",
                    "tags": skill.get("topics") or [],
                }
            )
        return output

    async def _search_skillhub_cn(self, query: str, limit: int) -> list[dict[str, Any]]:
        response = await self._client.get(
            f"{self._base}/api/skills",
            params={
                "keyword": query,
                "sortBy": "score",
                "order": "desc",
                "page": 1,
                "pageSize": limit,
            },
        )
        response.raise_for_status()
        payload = response.json() or {}
        if payload.get("code") != 0:
            raise RuntimeError("skillhub.cn search failed")
        output = []
        for raw in (payload.get("data") or {}).get("skills", []):
            if _malicious(raw.get("securityReports")):
                continue
            namespace = raw.get("namespace") or {}
            slug = str(raw.get("slug") or "")
            if not slug:
                continue
            output.append(
                {
                    "id": namespace.get("canonicalName") or slug,
                    "slug": slug,
                    "name": raw.get("name") or slug,
                    "description": raw.get("description_zh") or raw.get("description") or "",
                    "score": float(raw.get("score") or 0),
                    "owner": raw.get("ownerName") or namespace.get("handle") or "",
                    "version": raw.get("version") or "v0",
                    "tags": [],
                }
            )
        return output

    async def _download(self, slug: str, owner: str, version: str) -> bytes:
        params = {"slug": slug, "source": "dsh" if self.kind == "skillhub_cn" else "cli"}
        if self.kind == "clawhub" and owner:
            params["ownerHandle"] = owner
        if self.kind == "skillhub_cn" and version != "v0":
            params["version"] = version
        response = await self._client.get(
            f"{self._base}/api/v1/download",
            params=params,
            timeout=httpx.Timeout(self._download_timeout_s),
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.content


class MarketplaceSkillSource:
    def __init__(self, client: MarketplaceClient, *, weight: float = 0.75) -> None:
        self.client = client
        self.name = client.kind
        self.weight = weight

    async def search(self, query: str, history: list[dict[str, Any]], k: int) -> list[RouterHit]:
        del history
        items = await self.client.search(query, limit=min(2, max(0, k)))
        return [
            RouterHit(
                qualified_id=f"{self.name}/{item['id']}",
                name=str(item["name"]),
                content="",
                score=float(item["score"]),
                # `origin` before the spread so a catalogue field of that
                # name cannot shadow the identity fusion collapses on.
                meta={"source": self.name, **item, "origin": identity(self.name, str(item.get("slug") or item["id"]))},
            )
            for item in items[: min(2, max(0, k))]
        ]


def _malicious(reports: Any) -> bool:
    return isinstance(reports, dict) and any(
        isinstance(report, dict) and report.get("status") in {"malicious", "suspicious"} for report in reports.values()
    )


__all__ = ["MarketplaceClient", "MarketplaceSkillSource"]
