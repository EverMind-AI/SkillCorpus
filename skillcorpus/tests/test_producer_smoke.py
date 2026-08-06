"""Producer smoke — the retained end-to-end path: ingest a local skill through
the curate pipeline (parse/safety/classify/quality/store), then export the
corpus. No network / LLM: the classifier and quality judge fall back to rules
when no LLM is configured.

This is the new architecture's safety net (build -> corpus), replacing the old
CRUD / consumer-export tests.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

from skillcorpus import SkillLibrary
from skillcorpus.export.corpus import CORPUS_SCHEMA, write_corpus

SKILL = """---
name: pdf-generator
description: Generate PDF documents using Python and reportlab, with error handling and output validation.
license: MIT
tags:
  - pdf
  - reportlab
---

# PDF Generator

This skill generates PDF documents using Python and the reportlab library.

## Steps

1. Import reportlab's canvas module.
2. Create a canvas bound to an output path.
3. Draw the text, tables, and images the caller asked for.
4. Save the canvas and confirm the file exists on disk.

## Example

```python
from reportlab.pdfgen import canvas

c = canvas.Canvas("demo.pdf")
c.drawString(100, 750, "Hello, World!")
c.save()
```

Always validate that the output file exists before returning success.
"""


def _tar_names(path):
    import tarfile
    import zstandard
    with open(path, "rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as z, \
            tarfile.open(fileobj=z, mode="r|") as tar:
        return [m.name for m in tar]


def _make_src(root: Path) -> Path:
    d = root / "src" / "pdf-generator"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (d / "scripts" / "run.py").write_text("print('generate pdf')\n", encoding="utf-8")
    return root / "src"


def test_producer_smoke():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        src = _make_src(tmp_p)

        lib = SkillLibrary(tmp_p / "lib").open()
        res = lib.add_batch(src, source="custom")
        assert res.get("added", 0) >= 1, res
        db_path = Path(lib.store.db_path)

        # No GREEN whitelist in a test, so nothing is auto-activated; use the
        # documented operator SQL escape hatch to activate for export.
        con = sqlite3.connect(db_path)
        con.execute("UPDATE skills SET active=1 WHERE deleted=0")
        con.commit()
        con.close()
        lib.close()

        out = tmp_p / "corpus"
        stats = write_corpus(db_path, tmp_p / "lib", out)
        assert stats["rows"] >= 1, stats

        table = pq.read_table(out / "skills.parquet")
        assert table.schema.equals(CORPUS_SCHEMA)
        rec = table.to_pylist()[0]
        assert rec["name"] == "pdf-generator"
        assert rec["source"] == "custom"
        assert rec["category"], "a category should be assigned"
        assert rec["quality_score"] > 0

        # scripts/ becomes an attachment; SKILL.md is not duplicated
        assert rec["has_scripts"] is True
        assert rec["attachment_path"] == rec["skill_id"].replace("/", "__")
        names = _tar_names(out / "attachments.tar.zst")
        assert f"{rec['skill_id']}/scripts/run.py" in names
        assert f"{rec['skill_id']}/SKILL.md" not in names

        assert (out / "README.md").read_text(encoding="utf-8").startswith("# SkillCorpus")


if __name__ == "__main__":
    test_producer_smoke()
    print("PRODUCER SMOKE TEST PASSED")
