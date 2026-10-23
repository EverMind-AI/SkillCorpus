"""Lifecycle tools: labels, the validity gate, the level-rule decision, the rule audit and the run workspace.

Offline only: no LLM endpoint and no agent run.
"""

from __future__ import annotations

import json

import pytest

from skillcorpus.lifecycle import _layout, audit, capability_items, judge, labels, retire, run_arms


def test_skill_of_strips_prefix_and_index():
    assert _layout.skill_of("tessl-single-owner_repo_my-skill_0") == "owner_repo_my-skill"
    assert _layout.skill_of("tessl-single-owner_repo_my-skill_12") == "owner_repo_my-skill"


@pytest.mark.parametrize(
    "u,a,expected",
    [
        (0.9, 0.95, "internalized"),
        (0.1, 0.9, "skill works"),
        (0.0, 0.0, "uncovered"),
        (0.8, 0.1, "skill hurts"),
        (0.5, 0.6, "between thresholds"),
    ],
)
def test_state(u, a, expected):
    assert labels.state(u, a) == expected


def test_scores_use_capability_items_only():
    cap = {"crux-a", "crux-b"}
    run1 = {"crux-a": 1.0, "crux-b": 0.0, "generic": 1.0}
    run2 = {"crux-a": 1.0, "crux-b": 1.0, "generic": 0.0}
    assert labels.run_score(run1, cap) == 0.5
    assert labels.arm_score([run1, run2], cap) == 0.75
    assert labels.run_score({"generic": 1.0}, cap) is None


def test_validity_gate():
    assert labels.passes_gate([(0.2, 0.5), (0.9, 0.9)])
    assert not labels.passes_gate([(0.9, 0.95), (1.0, 1.0)])
    assert not labels.passes_gate([(0.5, 0.6)])  # a gain of exactly 0.1 does not pass


@pytest.mark.parametrize(
    "kwargs,verdict",
    [
        (dict(u=0.95, witness_gains=[0.3], n_tasks=3), "retire"),
        (dict(u=0.95, witness_gains=[0.05], n_tasks=3), "regenerate"),
        (dict(u=0.95, witness_gains=[0.05], n_tasks=3, regenerated=True), "keep_no_verdict"),
        (dict(u=0.85, witness_gains=[0.3], n_tasks=1), "extra_tasks"),
        (dict(u=0.6, witness_gains=[0.3], n_tasks=1), "extra_tasks"),  # the margin edge counts despite float error
        (dict(u=0.59, witness_gains=[0.3], n_tasks=1), "keep"),
        (dict(u=0.1, witness_gains=[0.3]), "measure_aided"),
        (dict(u=0.1, witness_gains=[0.3], a=0.2), "keep_repair"),
        (dict(u=0.1, witness_gains=[0.3], a=0.9), "keep"),
        (dict(u=0.5, witness_gains=[], n_tasks=1), "keep"),
    ],
)
def test_decide(kwargs, verdict):
    assert retire.decide(**kwargs).verdict == verdict


def test_level_rule_never_retires_uncovered():
    for u in (0.0, 0.2, 0.39):
        assert retire.decide(u, [0.9], a=0.0, n_tasks=3).verdict != "retire"


def test_decide_from_labels_groups_tasks_and_uses_other_models_as_witnesses():
    lab = {
        "tessl-single-s1_0": {"T": (0.9, 1.0), "W": (0.2, 0.8)},
        "tessl-single-s1_1": {"T": (1.0, 1.0), "W": (0.3, 0.9)},
        "tessl-single-s1_2": {"T": (0.95, 1.0), "W": (0.1, 0.9)},
        "tessl-single-s2_0": {"T": (0.0, 0.1), "W": (0.0, 0.8)},
    }
    rows = {r["skill"]: r for r in retire.decide_from_labels(lab, "T")}
    assert rows["s1"]["verdict"] == "retire"
    assert rows["s2"]["verdict"] == "keep_repair"


def test_audit_gain_band_removes_uncovered_and_level_rule_does_not():
    pairs = [(0.0, 0.0), (0.05, 0.1), (1.0, 1.0), (0.9, 0.95), (0.1, 0.9)]
    res = audit.audit(pairs)
    band = res["table"]["|gain| < 0.10"]
    level = res["table"]["U >= 0.8"]
    assert band["uncovered_removed"] == 1.0
    assert level["uncovered_removed"] == 0.0
    assert level["internalized_removed"] == 1.0
    assert res["state_sizes"]["uncovered"] == 2


def test_gated_pairs_excludes_tasks_no_witness_gains_from():
    lab = {
        "t1": {"T": (0.9, 1.0), "W": (0.2, 0.9)},
        "t2": {"T": (0.9, 1.0), "W": (0.9, 0.95)},
    }
    assert audit.gated_pairs(lab, "T") == [(0.9, 1.0)]


def test_gated_pairs_counts_each_skill_once():
    lab = {
        "tessl-single-s_0": {"T": (0.8, 1.0), "W": (0.1, 0.9)},
        "tessl-single-s_1": {"T": (1.0, 1.0), "W": (0.9, 0.9)},
    }
    assert audit.gated_pairs(lab, "T") == [(0.9, 1.0)]


def _task(tmp_path, name="tessl-single-owner_repo_sk_0"):
    td = tmp_path / "tasks" / name
    (td / "inputs").mkdir(parents=True)
    (td / "inputs" / "data.csv").write_text("a,b\n1,2\n")
    (td / "task.md").write_text("Convert the data.")
    (td / "rubrics").mkdir()
    (td / "rubrics" / "task-completion.json").write_text(
        json.dumps({"checklist": [{"name": "crux", "max_score": 10}]})
    )
    sk = tmp_path / "skills" / "owner_repo_sk"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("Use the mapping X.")
    (sk / "helper.py").write_text("print(1)")
    return td


def test_prepare_workspace_arms(tmp_path):
    td = _task(tmp_path)
    prompt_u, skills_u = run_arms.prepare_workspace(td, tmp_path / "wu", "unaided", tmp_path / "skills")
    assert skills_u == [] and "Active Skills" not in prompt_u
    assert (tmp_path / "wu" / "inputs" / "data.csv").is_file()
    assert not (tmp_path / "wu" / "skills").exists()
    prompt_a, skills_a = run_arms.prepare_workspace(td, tmp_path / "wa", "aided", tmp_path / "skills")
    assert [n for n, _ in skills_a] == ["owner_repo_sk"]
    assert "Use the mapping X." in prompt_a
    assert (tmp_path / "wa" / "skills" / "owner_repo_sk" / "helper.py").is_file()
    with pytest.raises(ValueError):
        run_arms.prepare_workspace(td, tmp_path / "wx", "both", tmp_path / "skills")


def test_build_labels_from_judged_runs(tmp_path):
    task = "tessl-single-owner_repo_sk_0"
    cap = tmp_path / "cap.jsonl"
    cap.write_text(json.dumps({"task_id": task, "skill": "owner_repo_sk",
                               "capability": {"crux": True, "generic": False}}) + "\n")

    def judged(arm, score):
        d = tmp_path / arm / task
        d.mkdir(parents=True)
        detail = [{"name": "crux", "score": score, "max": 10}, {"name": "generic", "score": 10, "max": 10}]
        (d / "result.json").write_text(json.dumps({"task_completion": {"detail": detail}}))
        return tmp_path / arm

    rows = labels.build_labels(
        labels.load_capability_items(cap),
        [("T", "unaided", judged("u", 2)), ("T", "aided", judged("a", 9))],
    )
    assert rows == [
        {"task_id": task, "model": "T", "U": 0.2, "A": 0.9, "n_unaided": 1, "n_aided": 1, "n_capability_items": 1}
    ]


def test_unscored_runs_are_not_counted(tmp_path):
    d = tmp_path / "run" / "tessl-single-owner_repo_sk_0"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"task_completion": {"score": None, "detail": []}}))
    assert labels.collect_runs(tmp_path / "run") == {}


def test_aided_arm_requires_skill_text(tmp_path):
    td = _task(tmp_path)
    (tmp_path / "skills" / "owner_repo_sk" / "SKILL.md").unlink()
    with pytest.raises(FileNotFoundError):
        run_arms.prepare_workspace(td, tmp_path / "wa", "aided", tmp_path / "skills")


def test_find_skill_md_prefers_top_level(tmp_path):
    sk = tmp_path / "sk"
    (sk / "A_examples").mkdir(parents=True)
    (sk / "A_examples" / "SKILL.md").write_text("nested")
    (sk / "skill.md").write_text("top")
    assert _layout.skill_md(sk) == "top"


def test_capability_resume_retries_failed_tasks(tmp_path):
    out = tmp_path / "cap.jsonl"
    out.write_text(json.dumps({"task_id": "ok", "skill": "s", "capability": {"a": True}}) + "\n"
                   + json.dumps({"task_id": "failed", "skill": "s", "error": "no usable adjudicator reply"}) + "\n")
    assert capability_items.read_done(out) == {"ok"}


def test_judge_shows_utf8_text_cut_at_the_check_boundary(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "report.md").write_bytes(b"a" * 8191 + "技能".encode("utf-8"))
    (ws / "image.bin").write_bytes(bytes([0xFF, 0xFE, 0x00]) * 10)
    blob = judge.collect_workspace(ws)
    assert "技能" in blob
    assert "(binary, 30 bytes)" in blob


def test_is_scored(tmp_path):
    assert not judge.is_scored(tmp_path)
    (tmp_path / "result.json").write_text(json.dumps({"task_completion": {"score": None}}))
    assert not judge.is_scored(tmp_path)
    (tmp_path / "result.json").write_text(json.dumps({"task_completion": {"score": 7.0}}))
    assert judge.is_scored(tmp_path)


def test_agent_that_cannot_be_built_is_not_scored(tmp_path, monkeypatch):
    import argparse
    import asyncio

    td = _task(tmp_path)

    def broken(*a, **kw):
        raise TypeError("incompatible agent framework")

    monkeypatch.setattr(run_arms, "build_raven_agent", broken)
    args = argparse.Namespace(force=False, no_judge=False, arm="unaided", skills_root=None, raven_root="raven",
                              model="m", api_base="", api_key="k", max_turns=1, timeout=1, judge_model="m")
    with pytest.raises(TypeError):
        asyncio.run(run_arms.run_one(td, args, tmp_path / "out", judge_client=None))
    case = tmp_path / "out" / td.name
    assert not (case / "run.json").exists() and not (case / "result.json").exists()


@pytest.mark.parametrize(
    "content,reasoning,expected",
    [
        ('{"score": 3}', "the rubric item {A} requires ...", {"score": 3}),  # braces in the reasoning
        ('Here it is: {"score": 3} and a note {x}', None, {"score": 3}),      # prose around the answer
        ("", 'draft {"score": 1} ... final {"score": 2}', {"score": 2}),       # answer only in the reasoning
        ('```json\n{"v": [{"check": "a", "needs_doc": true}]}\n```', None, {"v": [{"check": "a", "needs_doc": True}]}),
    ],
)
def test_reply_json(content, reasoning, expected):
    import types

    m = types.SimpleNamespace(content=content, reasoning_content=reasoning)
    key = next(iter(expected))
    assert _layout.reply_json(m, _layout.is_dict_with(key)) == expected


def test_reply_json_judge_array():
    import types

    m = types.SimpleNamespace(content="", reasoning_content='[turn 3] ok\n[{"name": "a", "score": 7}]')
    assert _layout.reply_json(m, judge._is_score_list, opener="[") == [{"name": "a", "score": 7}]
    with pytest.raises(ValueError):
        _layout.reply_json(types.SimpleNamespace(content="no answer", reasoning_content=None), judge._is_score_list, "[")


def test_reply_json_reads_the_vllm_reasoning_field():
    import types

    m = types.SimpleNamespace(content=None, reasoning='... so the answer is {"passed": ["a"]}')
    assert _layout.reply_json(m, _layout.is_dict_with("passed")) == {"passed": ["a"]}


@pytest.mark.parametrize("paper_critic", [False, True])
def test_critic_input_and_unparseable_reply(monkeypatch, paper_critic):
    import asyncio
    import types

    from skillcorpus.lifecycle import synthesize

    sent = []

    class Completions:
        async def create(self, **kw):
            sent.append(kw["messages"][1]["content"])
            msg = types.SimpleNamespace(content="no verdict here", reasoning_content=None)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    cli = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(synthesize, "PAPER_CRITIC", paper_critic)
    spec = {"task_description": "t", "inputs": [], "instruction_following_rubric": [{"name": "if-item"}],
            "task_completion_rubric": [{"name": "tc-item"}]}
    ok, crit, _ = asyncio.run(synthesize.validate(cli, {"body": "skill"}, spec))
    assert ("tc-item" in sent[0]) is (not paper_critic)
    assert "if-item" in sent[0]
    assert ok is paper_critic and bool(crit) is (not paper_critic)
