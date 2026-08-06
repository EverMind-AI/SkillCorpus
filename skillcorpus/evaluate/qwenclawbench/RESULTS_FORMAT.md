# Results format

The evaluator grades **pre-produced agent results**. It does not run any agent —
you run your agent however you like, then lay its output in this directory shape:

```
<results_dir>/
  <task_id>/
    workspace/          # REQUIRED: the agent's final working directory
    transcript.json     # the conversation transcript (see below)
  <task_id>/
    ...
```

- `<task_id>` must match the benchmark task id, e.g.
  `task_00100_house_robber_algorithm_deep_dive_explanation`
  (the `.md` filename in `data/qwenclawbench-v1.1-100/tasks/`, without `.md`).

## `workspace/`
The exact directory the agent worked in, containing whatever files it created or
modified. The task's **automated checks** (`def grade(transcript, workspace_path)`)
inspect these files, so their paths must be relative to `workspace/` just as the
task prompt specified.

## `transcript.json`
A JSON array of message objects, oldest first:

```json
[
  {"role": "user", "content": "…the task prompt…"},
  {"role": "assistant", "content": "…", "tool_calls": [
      {"function": {"name": "write_file", "arguments": "{\"path\": \"...\"}"}}
  ]},
  {"role": "tool", "content": "…tool result…"},
  {"role": "assistant", "content": "…final answer…"}
]
```

- Both the automated `grade()` and the LLM judge receive this list.
- `tool_calls` / `role: "tool"` entries are optional but recommended — some tasks'
  automated checks and the judge inspect the tool trace, not just the final text.
- **Alternative:** if you already have a `grading.json` with a `"transcript"` field
  in the task dir, the evaluator will read the transcript from there too, so you can
  point it straight at many existing harness outputs.

## Minimal example
```
my_results/
  task_00100_house_robber_algorithm_deep_dive_explanation/
    workspace/
      house_robber.py
      README.md
    transcript.json
```
```bash
python -m qcb_eval.evaluate \
  --tasks-dir data/qwenclawbench-v1.1-100/tasks \
  --results-dir my_results \
  --no-judge          # or drop this and set JUDGE_API_KEY for full hybrid
```
