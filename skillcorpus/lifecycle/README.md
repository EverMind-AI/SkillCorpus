# Skill lifecycle — retire on the unaided level

> Part of the **[SkillCorpus](../../README.md)** framework — the `lifecycle` stage, after
> aggregate → curate → match → evaluate.
>
> Code for the paper *Beyond Gain: Measuring Skill Internalization for Agent Skill Retirement*.

**English** · [简体中文](README.zh-CN.md)

A public skill hub serves many models, and as models improve some of them no longer need some skills. This stage
decides, for one deployment model at a time, which skills that model has **internalized**, meaning it can do the
skill's job without the skill, and retires them **for that model** while keeping them in the hub.

Rules that compare a run with the skill and a run without it read only the **gain** (aided minus unaided score). The
gain is near zero both for a skill the model has internalized and for one whose capability neither the model nor
the skill delivers (**uncovered**), so a gain-based rule retires both. This stage reads the **unaided level**
instead: it withholds the skill, scores the model on a task synthesized from the skill, and trusts a high score only
when another model gains from the skill on the same task.

## Pipeline

| Step | Module | What it does |
|---|---|---|
| 1 | `synthesize` | Synthesizes one executable task and two hidden rubrics per skill. A generator from another model family writes the task, a critic rejects leaky or unperformable tasks, and a difficulty gate regenerates tasks the target model already solves unaided. |
| 2 | `run_arms` | Runs the agent on each task in the **unaided** arm (task only) or the **aided** arm (task plus the skill), then judges the workspace. |
| 3 | `judge` | LLM judge that scores a finished workspace, its trajectory and the final reply against both rubrics. Usable on workspaces from any agent framework. |
| 4 | `capability_items` | Marks the task-completion items whose check needs information stated in the skill (capability items). |
| 5 | `labels` | Computes U and A (mean capability-item score without and with the skill), the validity gate and the three states. |
| 6 | `retire` | Level-rule verdict per skill and deployment model (Algorithm 1 of the paper). |
| — | `audit` | Applies gain-based rules and the level rule to the gated skills and reports which states each rule removes. |
| — | `reproduce/` | Recomputes the paper's tables from the paper's dataset, with the Python standard library only. |

Every module runs with `python -m skillcorpus.lifecycle.<module> --help`.

## Requirements

```bash
pip install -e .                                   # from the repository root
pip install -r skillcorpus/lifecycle/requirements.txt
```

Steps 1–4 call OpenAI-compatible chat-completion endpoints. `run_arms` drives the **Raven** agent, which is a
separate project: pass a checkout with `--raven-root` (or `RAVEN_ROOT`), as for the
[SkillsBench runner](../evaluate/skillsbench/README.md). Steps 5–6, `audit` and `reproduce/` need no endpoint.

> **Run the agent in isolation.** `run_arms` lets the agent execute shell commands as the calling user, and it is not
> confined to its workspace. Tasks may install packages or edit files outside the workspace, so use a container or
> a virtual machine, or at least a separate `HOME`.

## Run

The task layout is `tasks/<task_id>/{task.md, inputs/, rubrics/task-completion.json,
rubrics/instruction-following.json}` and `skills/<skill>/SKILL.md` (plus bundled files). Task ids have the form
`tessl-single-<skill>_<idx>`; the prefix names the single-skill task format.

```bash
# 1. tasks from skills (generator, critic and difficulty-gate endpoints)
export GEN_BASE_URL=... GEN_API_KEY=... GEN_MODEL=deepseek/deepseek-v4-flash
export CRITIC_BASE_URL=... CRITIC_API_KEY=... CRITIC_MODEL=Qwen3.6-27B
python -m skillcorpus.lifecycle.synthesize --skills-dir my_skills --sample 100 --seed 42 --out data

# 2-3. both arms, three runs each, for the target model and for each witness model
#      (one serving endpoint per model; SOLVER_API_KEY is the key of that endpoint)
export JUDGE_BASE_URL=... JUDGE_API_KEY=... JUDGE_MODEL=Qwen3.6-27B SOLVER_API_KEY=...
for model in Qwen3.6-27B Qwen3.5-397B; do for arm in unaided aided; do for r in 1 2 3; do
  python -m skillcorpus.lifecycle.run_arms --tasks-root data/tasks --skills-root data/skills \
      --arm $arm --model $model --api-base http://host:8000/v1 --raven-root ../raven \
      -o runs/$model/$arm/r$r
done; done; done

# 4. capability items
export ADJ_BASE_URL=... ADJ_API_KEY=... ADJ_MODEL=Qwen3.5-397B
python -m skillcorpus.lifecycle.capability_items --tasks-root data/tasks --skills-root data/skills \
    -o capability_items.jsonl

# 5. labels (one --runs triple per judged run directory, for every model)
python -m skillcorpus.lifecycle.labels --capability capability_items.jsonl \
    --runs Qwen3.6-27B unaided runs/Qwen3.6-27B/unaided/r1 ... -o labels.csv

# 6. verdicts for one deployment model; the other models in labels.csv are the witnesses
python -m skillcorpus.lifecycle.retire --labels labels.csv --target Qwen3.6-27B -o decisions.csv
python -m skillcorpus.lifecycle.audit --labels labels.csv --target Qwen3.6-27B
```

The environment variables of each step are listed in the module docstrings.

### Verdicts

| Verdict | Meaning and next step |
|---|---|
| `retire` | U ≥ τ = 0.8 and a witness model gains more than γ = 0.1 from the skill. Soft-retire the skill for this model: hide it from this model's retrieval, keep it in the hub and restorable. |
| `extra_tasks` | U lies within 0.2 of τ on fewer than three tasks. Add two tasks from the same generation batch, run each once unaided, and decide again. |
| `regenerate` | U ≥ τ but no witness gains, so the task may not test the skill. Regenerate the task once. |
| `keep_no_verdict` | The regenerated task also fails the validity gate. Keep the skill. |
| `measure_aided` | U < 0.4. Run the aided arm to check whether the skill itself helps. |
| `keep_repair` | U and A are both below 0.4: the capability is uncovered. Keep the skill and flag it for repair. |
| `keep` | Every other case. |

A verdict holds only for the model it was read for; re-read it for each model the hub serves. A new model reuses
the tasks, the capability items and the witness evidence and pays only for its own unaided arm.

## Reproduce the paper

The paper's dataset holds the 1,251 tasks with their hidden rubrics, the capability-item labels, the item-level
scores of four models (Qwen3.6-27B as target; Qwen3.5-397B, DeepSeek-V4-Flash and Qwen3.8-27B as witnesses), the
135 second tasks, the SkillsBench validation and the judge audit, with a datasheet and per-skill licences. The
scripts read a local copy of it.

```bash
python skillcorpus/lifecycle/reproduce/reproduce_labels.py path/to/dataset/scores   # Table 3
python skillcorpus/lifecycle/reproduce/reproduce_audits.py path/to/dataset          # Tables 1 and 4, Fig. 3, App. C
```

In the paper the generator is DeepSeek-V4-Flash, the critic, the difficulty gate and the judge are Qwen3.6-27B with
thinking disabled, the capability-item adjudicator is Qwen3.5-397B, and the agent runs at most 40 turns.

## License

The code in this folder is MIT licensed (see [LICENSE](LICENSE)). The dataset carries its own licences: tasks,
rubrics, labels and scores under CC BY 4.0, and each skill text under the licence of its source repository.
