"""Build-chain wiring — _post_actions runs the fixed curate -> export tail in
the right order with the right args, without touching real LLM / network.

Verifies only the wiring (order + threaded paths); the passes' real behavior is
an integration concern.
"""

from __future__ import annotations

from types import SimpleNamespace

import skillcorpus.export.corpus as corpus_mod
import skillcorpus.curate.safety_gate as safety_gate_mod
import skillcorpus.cli as rl


def _fake_lib(tmp_path):
    lib_root = tmp_path / "lib"
    return SimpleNamespace(
        lib_root=lib_root,
        store=SimpleNamespace(db_path=lib_root / "index.db"),
    )


def test_post_actions_chain_order(tmp_path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        rl, "_run_module",
        lambda module, argv: calls.append(("run", module, argv)) or 0,
    )
    monkeypatch.setattr(
        corpus_mod, "write_corpus",
        lambda db, lib_root, out: calls.append(("corpus", str(db), str(out)))
        or {"rows": 0, "with_attachments": 0, "out": str(out)},
    )
    monkeypatch.setattr(
        safety_gate_mod, "run_safety_gate",
        lambda db: calls.append(("safety", str(db))) or 0,
    )

    lib = _fake_lib(tmp_path)
    rl._post_actions(lib, {}, dry=False)

    # exactly the five steps, in pipeline order (safety_gate after license activate)
    assert [c[1] if c[0] == "run" else c[0] for c in calls] == [
        "skillcorpus.curate.quality_pass",
        "skillcorpus.curate.dedup_pass",
        "skillcorpus.curate.license_audit",
        "safety",
        "corpus",
    ], calls

    quality, dedup, license_audit, safety, corpus = calls
    lib_root = str(lib.lib_root)
    db_path = str(lib.store.db_path)
    assert quality[2] == ["--lib", lib_root, "--workers", "16"]
    assert dedup[2] == ["--lib", lib_root]
    assert license_audit[2] == ["activate", "--db", db_path]
    assert safety[1] == db_path
    # corpus is written from the producer DB into <lib_root>/corpus by default
    assert corpus[1] == db_path
    assert corpus[2] == str(lib.lib_root / "corpus")


def test_post_actions_respects_corpus_out(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(rl, "_run_module", lambda module, argv: 0)
    monkeypatch.setattr(safety_gate_mod, "run_safety_gate", lambda db: 0)
    monkeypatch.setattr(
        corpus_mod, "write_corpus",
        lambda db, lib_root, out: seen.update(out=str(out))
        or {"rows": 0, "with_attachments": 0, "out": str(out)},
    )
    rl._post_actions(_fake_lib(tmp_path), {"corpus_out": str(tmp_path / "custom")}, dry=False)
    assert seen["out"] == str(tmp_path / "custom")


def test_post_actions_dry_run_runs_nothing(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(rl, "_run_module", lambda module, argv: calls.append(module) or 0)
    monkeypatch.setattr(
        corpus_mod, "write_corpus",
        lambda *a, **k: calls.append("corpus") or {"rows": 0, "with_attachments": 0, "out": ""},
    )
    rl._post_actions(_fake_lib(tmp_path), {}, dry=True)
    assert calls == []


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    with tempfile.TemporaryDirectory() as d:
        test_post_actions_chain_order(Path(d), _MP())
    print("BUILD CHAIN TESTS PASSED (order)")
