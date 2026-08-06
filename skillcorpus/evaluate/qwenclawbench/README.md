# QwenClawBench — minimal evaluator

> Part of the **[SkillCorpus](../../../README.md)** framework — the `evaluate` stage.

A self-contained harness that **scores agent results** on QwenClawBench (QCB), a
100-task benchmark of agentic skill-creation / tool-use tasks. Each task ships its
own automated checks and an LLM-judge rubric; the final score is a hybrid of the two.

This package is **evaluation only** — it does not run agents. You produce results
with whatever agent you want, then grade them here. See **[RESULTS_FORMAT.md](RESULTS_FORMAT.md)**.

## Layout
```
qwenclawbench/
├── data/qwenclawbench-v1.1-100/tasks/*.md   # the benchmark (100 tasks)
├── qcb_eval/
│   ├── tasks.py       # TaskLoader — parses the task .md files
│   ├── grading.py     # automated checks + LLM judge + hybrid combine
│   └── evaluate.py    # CLI: results dir → per-task scores → aggregate
├── requirements.txt
├── RESULTS_FORMAT.md  # the input contract (what a result dir looks like)
└── README.md
```

## Install
```bash
pip install -r requirements.txt      # just pyyaml
```

## Run
```bash
# 1) automated checks only — no API key, no network
python -m qcb_eval.evaluate \
    --tasks-dir data/qwenclawbench-v1.1-100/tasks \
    --results-dir path/to/your_results \
    --no-judge \
    --output scores.json

# 2) full hybrid (automated + LLM judge)
export JUDGE_API_KEY=...                       # OpenAI-compatible key
export JUDGE_BASE_URL=https://openrouter.ai/api/v1
python -m qcb_eval.evaluate \
    --tasks-dir data/qwenclawbench-v1.1-100/tasks \
    --results-dir path/to/your_results \
    --judge-model anthropic/claude-opus-4.5 \
    --output scores.json
```
`scores.json` holds `mean_score`, per-task score, grading type, and breakdown.

## How a task is scored
Each task's front-matter declares `grading_type` (`automated` | `llm_judge` |
`hybrid`) and `grading_weights` (e.g. `automated: 0.4`, `llm_judge: 0.6`).

- **Automated** — the task's embedded `def grade(transcript, workspace_path) -> dict`
  runs against your `workspace/` and returns named sub-scores in `[0,1]`; their mean
  is the automated score.
- **LLM judge** — the transcript is summarized and scored by `--judge-model` against
  the task's `## LLM Judge Rubric` (one OpenAI-compatible chat call).
- **Hybrid** — `score = (auto·w_auto + llm_adj·w_llm) / (w_auto + w_llm)`, where by
  default `llm_adj = 0` if `auto < 0.75` (a penalty that stops a strong judge score
  from rescuing an output that failed the objective checks). Tune the threshold via
  `AUTO_PENALTY_THRESHOLD` in `grading.py`; `score_simple` (unpenalized) is also
  computed.

## Judge configuration
| var | meaning | default |
|---|---|---|
| `JUDGE_API_KEY` | bearer key for the judge endpoint | required (unless `--no-judge`) |
| `JUDGE_BASE_URL` | OpenAI-compatible base url | `https://openrouter.ai/api/v1` |
| `JUDGE_MODEL` | default judge model | `anthropic/claude-opus-4.5` |
| `QCB_ENV_FILE` | optional `KEY=VALUE` file for the two vars above | `<pkg>/.env` |

The judge caller is provider-agnostic — any endpoint that speaks
`POST /chat/completions` (OpenRouter, vLLM, OpenAI, …) works.
