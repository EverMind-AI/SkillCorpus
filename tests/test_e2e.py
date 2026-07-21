"""端到端集成测试 — CRUD 流程 / quality rejection / export_bundle.

纯函数 (parse/safety/classify/dedup) 测试拆到各自文件:
    test_parse.py, test_safety.py, test_classify.py,
    test_dedup_round_a.py, test_quality_round_b.py
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from skill_library import SkillLibrary, IngestStatus, CATEGORIES


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


def test_e2e_crud():
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

        # 2. Dedup (re-add 同一内容)
        r2 = lib.add(skill_dir, source="custom")
        assert r2.status == IngestStatus.DUPLICATE

        # 3. Get
        got = lib.get(sid)
        assert got is not None
        assert got.name == "my-demo-skill"
        # 分类器产出合法分类即可 (LLM 不可用时走规则兜底; 具体命中由
        # test_classify.py 覆盖). DOC-PROC/DEV 都是合理结果.
        assert got.category in CATEGORIES
        assert got.has_scripts is False
        assert got.quality_score > 0

        # 4. List
        records = lib.list(source="custom")
        assert any(rec.skill_id == sid for rec in records)

        # 6. Update tags
        lib.retag(sid, ["pdf", "reportlab", "demo"])
        assert "reportlab" in lib.get(sid).tags

        # 7. Reclassify (用当前 16-class taxonomy 的合法类名)
        lib.reclassify(sid, "DOC-PROC")
        assert lib.get(sid).category == "DOC-PROC"

        # 8. Stats
        st = lib.stats()
        assert st["total"] == 1
        assert "overlong_description_count" in st
        assert st["overlong_description_count"] == 0

        # 9. Delete (soft)
        assert lib.delete(sid, soft=True)
        assert lib.get(sid) is None
        assert lib.stats()["total"] == 0

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


def test_export_bundle_roundtrip():
    """export → 解压 zip 检查 manifest.json + skill 目录."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        lib = SkillLibrary(tmp_p / "lib").open()
        # 测试里禁用近似去重, 避免两个内容相似的 sample 被合并
        lib.ingester._dedup_enabled = False
        lib.ingester.dup_judge = None

        # 加两个 skill
        s1 = _write_sample(tmp_p / "src1", "s1")
        r1 = lib.add(s1, source="custom")
        assert r1.status == IngestStatus.ADDED

        # 第二个用完全不同 body 避免 content 相似
        (tmp_p / "src2" / "s2").mkdir(parents=True)
        (tmp_p / "src2" / "s2" / "SKILL.md").write_text(
            "---\nname: other-demo\ndescription: Totally different skill about network monitoring and alerting.\n---\n\n"
            "# other-demo\n\n" + ("Monitor network traffic and send alerts. " * 40),
            encoding="utf-8",
        )
        r2 = lib.add(tmp_p / "src2" / "s2", source="anthropics")
        assert r2.status == IngestStatus.ADDED, f"got {r2.status}: {r2.reason}"

        # Export 全部
        out = tmp_p / "bundle.zip"
        stats = lib.export_bundle(out_path=out)
        assert stats["count"] == 2
        assert out.exists() and out.stat().st_size > 0

        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["count"] == 2
            assert len(manifest["skills"]) == 2
            # 每个 skill 至少一个文件被打包
            for s in manifest["skills"]:
                sp = s["stored_path"]
                has_file = any(n.startswith(sp + "/") for n in names)
                assert has_file, f"no files for {sp} in bundle"

        # 按 source 过滤导出
        out_anth = tmp_p / "anth.zip"
        stats_a = lib.export_bundle(out_path=out_anth, source="anthropics")
        assert stats_a["count"] == 1

        lib.close()


if __name__ == "__main__":
    test_e2e_crud()
    test_quality_rejection_short_body()
    test_export_bundle_roundtrip()
    print("ALL E2E TESTS PASSED")
