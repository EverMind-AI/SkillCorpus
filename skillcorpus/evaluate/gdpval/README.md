# gdpval-grader

> Part of the **[SkillCorpus](../../../README.md)** framework — the `evaluate` stage.

Standalone **LLM-judge grader** for [GDPval](https://huggingface.co/datasets/openai/gdpval)
deliverables. Point it at a directory of files an agent (or a human) produced for
a GDPval task, and it scores them against that task's official rubric with an LLM
judge — returning a continuous reward in `[0, 1]`.

This is **grading only**. It does not run agents or generate deliverables; you
bring the finished files. It is the scoring half of an internal GDPval evaluation
harness, extracted to run on its own.

---

## What it does

1. **Discovers deliverables** in your workspace directory (recursively), skipping
   reference/input files, dotfiles, dependency dirs, and non-artifact types.
2. **Reads each deliverable** into a form a judge can grade:
   - `.docx` → text (incl. nested tables) · `.xlsx/.xls/.xlsm` → text (formulas
     recalculated via LibreOffice) · `.pdf` → page images · `.pptx` → slide
     images · images → inlined · `.mp4/.wav/...` → ffprobe technical metadata ·
     text/code/csv/json/md → inline.
   - Unreadable or oversized files degrade to a "present but unreadable" note
     rather than failing the whole grade.
3. **Judges against the rubric.** Each rubric criterion carries a signed point
   value; the judge returns a 0/1 verdict per criterion and the score is

   ```
   reward = clamp( sum(score_i * met_i) / sum(positive scores), 0, 1 )
   ```

   (Continuous, not pass/fail. No payment cliff.)

The judge is any OpenAI-compatible chat model. Default is `openai/gpt-4o` via
OpenRouter; you can point it at the OpenAI API directly.

---

## Install

```bash
pip install -e ".[artifacts,hf]"     # from this directory
# or, minimal:  pip install openai   (add readers below for full coverage)
```

**System tools** (install with your OS package manager, *not* pip — each is
optional and the grader degrades gracefully without it, but coverage suffers):

| Tool | Package | Needed for |
|------|---------|-----------|
| `pdftoppm` | `poppler-utils` | rendering `.pdf` / `.pptx` pages to images |
| `soffice`  | `libreoffice`   | recalculating `.xlsx` formulas, `.pptx` → PDF |
| `ffprobe`  | `ffmpeg`        | audio/video technical metadata |

```bash
# Debian/Ubuntu
sudo apt-get install poppler-utils libreoffice ffmpeg
# macOS
brew install poppler libreoffice ffmpeg
```

## Configure the judge

Set an API key (checked in this order): `GDPVAL_API_KEY`, `OPENROUTER_API_KEY`,
`OPENAI_API_KEY`.

```bash
export OPENROUTER_API_KEY=sk-or-...        # default: OpenRouter + openai/gpt-4o
```

To use the OpenAI API directly instead:

```bash
export OPENAI_API_KEY=sk-...
export GDPVAL_BASE_URL=https://api.openai.com/v1
export GDPVAL_JUDGE_MODEL=gpt-4o
```

---

## Usage

### Command line

```bash
# Grade a workspace against a task loaded by id from HuggingFace openai/gdpval:
python -m gdpval_grader --workspace ./deliverables --task-id 0a1b2c3d

# ...or against a local task JSON (no HuggingFace needed):
python -m gdpval_grader --workspace ./deliverables --task-json task.json --out result.json

# Try the bundled offline example (needs an API key set):
python -m gdpval_grader \
    --task-json examples/example_task.json \
    --workspace examples/deliverables
```

### Python API

```python
from gdpval_grader import load_gdpval_task, grade_workspace

task = load_gdpval_task("0a1b2c3d")                    # or a hand-written dict
result = grade_workspace(task, "./deliverables", api_key="sk-...")

print(result["reward"])        # e.g. 0.8333  — continuous score in [0, 1]
print(result["files_found"])   # deliverables that were graded
print(result["feedback"])      # the judge's full evaluation text
```

Grading many tasks? Reuse one evaluator (caches the client + meta-prompts):

```python
from gdpval_grader import LLMEvaluator, grade_workspace

ev = LLMEvaluator(api_key="sk-...")                    # or model=/base_url=
for task, workspace in jobs:
    print(grade_workspace(task, workspace, evaluator=ev)["reward"])
```

---

## Task schema

A task dict needs at minimum:

```json
{
  "occupation": "Accountants and Auditors",
  "rubric_json": [
    {"criterion": "The memo reports revenue by product line.", "score": 2},
    {"criterion": "The memo contains an arithmetic error.",    "score": -2}
  ],
  "reference_files": ["q3_raw_figures.csv"]
}
```

- **`rubric_json`** — a list (or JSON string of a list) of `{criterion, score}`.
  Positive `score` = points earned if the criterion is true of the deliverable;
  negative = a penalty applied if its (undesirable) condition is true.
- **`reference_files`** — basenames of input files handed to the agent; excluded
  from the graded deliverable set so inputs aren't mistaken for outputs.
- **`occupation`** — only used to pick a fallback meta-prompt for the rare task
  with no `rubric_json`. Per-occupation prompts ship in `gdpval_grader/meta_prompts/`.

`load_gdpval_task(task_id)` / `load_gdpval_tasks()` build these from the public
`openai/gdpval` dataset; `load_task_from_json(path)` loads one you wrote yourself.

## Result

```jsonc
{
  "reward": 0.8333,              // continuous rubric score in [0, 1]
  "evaluation_score": 0.8333,
  "score_raw": 8.33,            // reward * 10
  "feedback": "...VERDICT[0]: 1 ...",  // full judge output
  "files_found": ["q3_revenue_memo.md"]
}
// If nothing gradable was found:
// { "reward": 0.0, "error": "no_artifacts_found", "files_found": [] }
```

---

## Notes & caveats

- **Determinism.** The judge runs at `temperature=0`, but LLM grading is not
  perfectly reproducible; expect small run-to-run variation.
- **Robustness over strictness.** Malformed office files, oversized files, and
  judge-API rejections (context-length / too-many-images) are handled by
  degrading the input and retrying rather than zeroing the grade. Missing
  per-criterion verdicts trigger a targeted re-ask before any criterion is
  scored 0. This mirrors the "grade whatever is there" behavior of the source
  harness.
- **Cost.** Each grade is one (occasionally two, on retry) judge call carrying
  the rubric plus the rendered deliverables; image-heavy PDFs/decks are the
  priciest. At most 16 images are inlined per grade.
- **Lineage.** The judge prompt and multimodal artifact rendering are adapted
  from [ClawWork](https://github.com/HKUDS/ClawWork)'s `LLMEvaluator`; the
  rubric-based VERDICT scoring, reader fallbacks, and degradation ladder are
  additions for GDPval grading.
