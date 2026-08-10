"""End-to-end tests — ingest + read (add / dedup / get / list / stats) and
quality rejection.

Pure-function (parse/safety/classify/dedup) tests are split into their own files:
    test_parse.py, test_safety.py, test_classify.py,
    test_dedup_round_a.py, test_quality_round_b.py
The ingest -> corpus path is covered by test_producer_smoke.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from skillcorpus import SkillLibrary, IngestStatus, CATEGORIES


SAMPLE_SKILL = """---
name: my-demo-skill
description: A demo skill that tests PDF generation using Python and reportlab with error handling.
license: MIT
---

# My Demo Skill

This skill demonstrates how to generate PDF documents using Python.

## Steps

1. Import reportlab
2. Create canvas
3. Draw text + tables
4. Save to file

## Example

```python
from reportlab.pdfgen import canvas
c = canvas.Canvas("demo.pdf")
c.drawString(100, 750, "Hello, World!")
c.save()
```

Always validate the output file exists before returning.
"""


def _write_sample(dir_: Path, name: str = "demo") -> Path:
    d = dir_ / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")
    return d


def test_e2e_ingest_and_read():
    with tempfile.TemporaryDirectory() as tmp:
        src_dir = Path(tmp) / "src"
        skill_dir = _write_sample(src_dir, "demo-skill")

        lib = SkillLibrary(Path(tmp) / "lib").open()

        # 1. Add
        r = lib.add(skill_dir, source="custom")
        assert r.status == IngestStatus.ADDED
        assert r.record is not None
        sid = r.record.skill_id
        assert sid.startswith("custom__")

        # 2. Dedup (re-add the same content)
        r2 = lib.add(skill_dir, source="custom")
        assert r2.status == IngestStatus.DUPLICATE

        # 3. Get — any valid category is fine (rule fallback when no LLM)
        got = lib.get(sid)
        assert got is not None
        assert got.name == "my-demo-skill"
        assert got.category in CATEGORIES
        assert got.has_scripts is False
        assert got.quality_score > 0

        # 4. List
        records = lib.list(source="custom")
        assert any(rec.skill_id == sid for rec in records)

        # 5. Stats
        st = lib.stats()
        assert st["total"] == 1
        assert "overlong_description_count" in st
        assert st["overlong_description_count"] == 0
        # README promises counts by source / category / license
        assert {"by_source", "by_category", "by_license"} <= st.keys()

        lib.close()


def test_quality_rejection_short_body():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "short"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: short\ndescription: A description long enough to pass.\n---\n# short\n",
            encoding="utf-8",
        )
        lib = SkillLibrary(Path(tmp) / "lib").open()
        r = lib.add(d, source="custom")
        assert r.status == IngestStatus.REJECTED_QUALITY
        lib.close()


if __name__ == "__main__":
    test_e2e_ingest_and_read()
    test_quality_rejection_short_body()
    print("ALL E2E TESTS PASSED")
