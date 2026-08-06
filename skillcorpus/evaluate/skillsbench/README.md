# skillsbench raven runner

Runs the raven agent on the 88 SkillsBench tasks against any OpenAI-compatible
LLM endpoint (with-skill mode: injects pre-retrieved skills, no live retrieval).

## Files

```
run_raven_batch.sh        # Batch entry: runs all tasks concurrently with resume,
                          # per-task retry, and summary.json aggregation
run_raven.py              # Single-task runner (raven agent + docker container + verifier)
skill_attachments.py      # Skill attachment injection (docker-cp skill-bundled scripts/*.py into the container)
docker_scripts/           # tmux helper scripts used inside containers
```

## Requirements

- **raven framework**: resolved via the relative `../raven` layout — keep this
  package next to a `raven` checkout
- **Task set** (`TASKS_DIR`, default `./tasks`): the 88 SkillsBench task directories
- **Pre-retrieved skill outputs** (`SKILL_OUTPUTS_DIR`, default `./skill_outputs`):
  per-task `after_gate_<gate-model>/*.json` files produced by the offline
  retrieval + gate pipeline (`GATE_SUBDIR_MODEL` selects the subdir, default
  `qwen3.5-397b`)
- **Skill attachment DB** (`MASS_LIBRARY_DB`, default `./mass_library.db`):
  SQLite DB mapping skill name -> source dir/body, used to copy skill-bundled
  attachment files into task containers
- **uv_bin/ (optional)**: place a `uv_bin/` dir with the `uv` and `uvx` binaries
  next to run_raven.py (download from https://github.com/astral-sh/uv). The
  runner docker-cp's them into containers so tasks can install Python deps
  offline; without it, tasks whose Dockerfile installs uv via curl will download
  it from the internet instead (slower, needs network)
- **Python environment** (`PYTHON`, default `python3`): needs the raven
  framework's dependencies (litellm etc.)
- **docker** on the local machine
- **LLM endpoint** (`LLM_URL` / `LLM_MODEL`, both required; `LLM_KEY` optional):
  any OpenAI-compatible endpoint

Task images are built locally with `docker build` from each task directory's
Dockerfile on first run (a per-task prebuilt image cache is supported via a
`prebuilt_images/<task>.tar` dir but not required).

### Restricted-network environments (optional)

For hosts behind a proxy or without direct access to the official package
indexes, `run_raven.py` honors these env vars (all off by default):

- `BUILD_PROXY` — http(s) proxy URL passed to `docker build` and set inside containers
- `PIP_MIRROR` — PyPI index URL configured inside containers (pip + uv)
- `APT_MIRROR` — mirror host substituted for `archive/security.ubuntu.com`
- `MAVEN_MIRROR` — Maven mirror URL written to `~/.m2/settings.xml`

## Run

```bash
LLM_URL=https://your-endpoint/v1 LLM_MODEL=your-model LLM_KEY=your-key \
TASKS_DIR=/path/to/tasks SKILL_OUTPUTS_DIR=/path/to/skill_outputs \
MASS_LIBRARY_DB=/path/to/mass_library.db \
bash run_raven_batch.sh [parallel=8] [start_run=1] [num_runs=1]
```

- Outputs land in `jobs_raven_skill_run<N>/`: one `raven-<task>/` per task
  (run.log + result.json); a `summary.json` (avg_reward, token usage) is
  aggregated at the end
- Resume: tasks with a valid result.json are skipped; empty-reward tasks are
  cleaned and retried; each task retries at most twice
