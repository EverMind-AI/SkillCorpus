"""lobehub converter tests — distinct identifiers that slugify to the same name
must not silently overwrite each other."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from skillcorpus.aggregate.converters import lobehub


def _write_agent(src: Path, ident: str) -> None:
    (src / f"{ident}.json").write_text(
        json.dumps({
            "identifier": ident,
            "config": {"systemRole": f"You are the {ident} assistant."},
            "meta": {"title": ident, "description": f"desc for {ident}"},
        }),
        encoding="utf-8",
    )


def test_slug_collision_keeps_both(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        out = Path(tmp) / "out"
        # "foo_bar" and "foo.bar" are distinct agents that both slugify to "foo-bar"
        _write_agent(src, "foo_bar")
        _write_agent(src, "foo.bar")

        monkeypatch.setattr(sys, "argv", ["lobehub", "--src", str(src), "--out", str(out)])
        assert lobehub.main() == 0

        dirs = sorted(p.name for p in out.iterdir() if (p / "SKILL.md").exists())
        assert len(dirs) == 2, f"a collision was silently dropped: {dirs}"
        # the bare slug plus one disambiguated variant
        assert "foo-bar" in dirs
        assert any(d.startswith("foo-bar-") for d in dirs), dirs


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
