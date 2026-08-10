<!-- Placeholder links marked `#` — fill in: Corpus (HF dataset),
     Embedding model (HF), Code (repo), SkillHub endpoint. -->

**English** | [简体中文](README.zh-CN.md)

# SkillCorpus

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](#)
[![Embedding model](https://img.shields.io/badge/%F0%9F%A4%97-Embedding%20model-yellow.svg)](#)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#license)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

**Give your agent 96,401 vetted, permissively-licensed skills — and a retriever that picks the right ones for each task.**

SkillCorpus consolidates ~821,000 crawled `SKILL.md` files into a curated, licence-audited corpus,
ships the fine-tuned retrieval stack that selects from it, and evaluates the whole thing end-to-end
on three real-world agent benchmarks.

<p align="center">
  <img src="docs/assets/pipeline.png" alt="SkillCorpus: building the corpus (aggregate + curate) and using it (match + evaluate)" width="100%">
</p>

## What it does for you

| You want to… | Use |
|---|---|
| **Give an agent skills at runtime** | [SkillHub](#1-query-skillhub-hosted) — query the hosted index, get back the skills for a task |
| **Get the data** | [The corpus](#2-load-the-corpus) — 96,401 rows on HuggingFace, one `load_dataset` call |
| **Wire it into your harness** | [Integrations](#3-plug-it-into-your-agent) — OpenClaw / Raven / Hermes |
| **Build a corpus from your own sources** | [Rebuild](#rebuild-the-corpus-yourself) — the full six-stage pipeline |
| **Reproduce the paper** | [`skillcorpus/evaluate/`](skillcorpus/evaluate) — SkillsBench · GDPVal · QwenClawBench |

### What changes when you plug it in

Pass rate with no skills → with SkillCorpus, same harness, same backbone ([Table 1](https://arxiv.org/abs/2607.15557)):

| Harness × backbone | SkillsBench | GDPVal | QwenClawBench |
|---|---|---|---|
| OpenClaw × Qwen3.5-27B | 8.8 → **13.0** | 81.2 → **83.1** | 65.2 → **66.7** |
| OpenClaw × Qwen3.5-397B | 11.1 → **16.9** | 82.2 → **84.0** | 65.7 → **67.0** |
| Raven × Qwen3.5-27B | 10.0 → **16.5** | 82.6 → **83.8** | 66.9 → **70.8** |
| Raven × Qwen3.5-397B | 9.2 → **22.6** | 84.0 → **85.2** | 68.8 → **73.2** |
| **Pooled ∆** | **+7.5**±2.3 (z=3.2) | **+1.51**±0.49 (z=3.1) | **+2.79**±0.70 (z=4.0) |

The gain is largest where the task needs procedural knowledge the model does not already have
(SkillsBench), and smallest on open-ended economic tasks it can already do (GDPVal).

---

## Quickstart

### 1. Query SkillHub (hosted)

<!-- TODO(@team): replace SKILLHUB_URL with the real endpoint, and confirm the
     request/response shape below matches the deployed service. -->

No install, no model download — ask for skills by task description:

```bash
curl -X POST https://<SKILLHUB_URL>/v1/skills/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "extract tables from a scanned PDF invoice", "top_k": 3}'
```

```json
{
  "skills": [
    {
      "name": "pdf-table-extraction",
      "description": "Extract tables from scanned PDFs into structured rows …",
      "category": "DOC-PROC",
      "score": 0.91,
      "license": "MIT",
      "source_url": "https://github.com/…",
      "body": "# PDF Table Extraction\n## Steps\n…"
    }
  ]
}
```

Inject `body` into your agent's prompt and it can do the task. That is the whole loop —
[`examples/skillhub_demo.py`](examples/skillhub_demo.py) runs it end to end:

```bash
export SKILLHUB_URL=https://<SKILLHUB_URL>

# retrieve only — stdlib, no install, no API key
python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

# retrieve AND run the task with the skills injected into the prompt
export OPENAI_API_KEY=...
python examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"
```

```
task: extract tables from a scanned PDF invoice

SkillHub returned 3 skill(s):

  1. pdf-table-extraction   (score 0.912)
     Extract tables from scanned PDFs into structured rows using OCR + layout analysis.
     MIT · https://github.com/…

  2. invoice-field-parser   (score 0.864)
     …

→ built a prompt of 14,203 chars with the skills injected
  (re-run with --ask to actually execute the task)
```

### 2. Load the corpus

```python
from datasets import load_dataset

skills = load_dataset("<org>/skillcorpus", split="train")   # 96,401 rows
skills.filter(lambda r: r["category"] == "DOC-PROC")
```

Column contract: [`docs/corpus-schema.md`](docs/corpus-schema.md). Attachments (`scripts/`,
`references/`) ship as a sibling `attachments.tar.zst`.

### 3. Plug it into your agent

<!-- TODO(@team): fill in the real config keys / file paths for each harness once
     the SkillHub client is published. The three below are the harnesses evaluated
     in the paper. -->

<details>
<summary><b>OpenClaw</b></summary>

```yaml
# ~/.openclaw/config.yaml
skills:
  provider: skillhub
  endpoint: https://<SKILLHUB_URL>
  top_k: 3
```
</details>

<details>
<summary><b>Raven</b></summary>

```yaml
# raven config
skill_forge:
  provider: skillhub
  endpoint: https://<SKILLHUB_URL>
  top_k: 3
```
</details>

<details>
<summary><b>Hermes</b></summary>

```yaml
# TODO: Hermes integration
```
</details>

Any harness that can inject text into a system prompt works — call the search endpoint,
paste the returned `body`. See [`docs/integrations.md`](docs/integrations.md).

### Rebuild the corpus yourself

Only needed if you want to curate **your own** sources. Requires an LLM endpoint for
classification/quality scoring and an embedding endpoint for dedup — see [`docs/running.md`](docs/running.md).

```bash
git clone <repo-url> skillcorpus && cd skillcorpus
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip && pip install -e .

python -m skillcorpus.cli build     # 4 demo sources -> curate -> export
python -m skillcorpus.cli stats     # counts by source / category / license
python -m skillcorpus.cli export --out ./corpus
```

Only skills from GREEN-licensed **sources** are exported (the demo trusts the whitelist in
`audit/license_safe_sources.json` wholesale; production gates per source-repo SPDX). The per-row
`license` is each skill's declared value, so a demo corpus can still carry non-GREEN `license`
strings. Use `--sources-config your.yaml` for your own registry, or `--source <name>` for one source.

---

## Released artifacts

| | Artifact | What | Link |
|---|---|---|---|
| 🌐 | **SkillHub** | hosted retrieval endpoint over the corpus | [endpoint](#) |
| 📚 | **Corpus** | `skills.parquet` + `attachments.tar.zst` + dataset card | [🤗 HuggingFace](#) |
| 🔡 | **Embedding model** | `Qwen3-Embedding-0.6B` fine-tuned for skill retrieval (2048-ctx) | [🤗 HuggingFace](#) |
| 🛠️ | **Code** | the framework — `aggregate` · `curate` · `match` · `evaluate` · `export` | [GitHub](#) |

## The corpus

<p align="center">
  <img src="docs/assets/taxonomy.png" alt="16-class distribution over the 96,401 active skills" width="60%">
</p>

96,401 skills from ~821,000 crawled files, organised by a 16-class taxonomy and three quality
facets (utility / robustness / safety), with 1024-dim retrieval embeddings.

## How it works

```
aggregate ─────────────► curate ──────────────────────────────────► export
 fetch public repos       parse · safety · license                   skills.parquet
                          classify · quality · dedup · license-gate   + attachments.tar.zst + card
```

The four stages are the package's subpackages:

1. **`aggregate`** — discover + clone skills from public `SKILL.md` repositories.
2. **`curate`** — parse · safety · license-gate · dedup · 16-class classify · quality scoring.
3. **`match`** — SkillRouter: a fine-tuned bi-encoder + reranker that retrieves skills for a task.
4. **`evaluate`** — three benchmarks: `skillsbench` · `qwenclawbench` · `gdpval`.

`cli build` runs the whole chain (`ingest → quality_pass → dedup_pass → license_audit → export.corpus`).
LLM classification and quality scoring degrade gracefully to rules when no model endpoint is
reachable, so the pipeline always runs end to end.

## Repository layout

```
skillcorpus/
├── core/       data models · SQLite/faiss store · LLM & embedding clients
├── aggregate/  source registry + multi-repo clone
├── curate/     parse · safety · license · classify · quality · dedup + full-library passes
├── export/     corpus writer (parquet + attachments + dataset card)
├── match/      SkillRouter — retrieval stack (bi-encoder + reranker)   ← isolated deps
├── evaluate/   skillsbench · qwenclawbench · gdpval benchmarks          ← isolated deps
└── cli.py      build · stats · export
```

`match/` and `evaluate/` are standalone toolkits with their own `requirements.txt`
(torch / transformers, per benchmark); they are **not** pulled in by `pip install` of the producer.

- **Retrieval** — [`skillcorpus/match/`](skillcorpus/match): fine-tune the Qwen3 bi-encoder +
  reranker on synthetic queries, then rank skills for a query. Retrieval metrics
  (nDCG / MRR / Hit / Recall) via `eval_compare.py`.
- **Benchmarks** — [`skillcorpus/evaluate/`](skillcorpus/evaluate): `skillsbench`,
  `qwenclawbench`, `gdpval` — each self-contained with its own README and dependencies.

## License

- **Code** — Apache-2.0 (the `match/` and `evaluate/` toolkits are each MIT — see their own `LICENSE`).
- **Corpus** — every skill keeps its **original upstream license**; only GREEN
  (MIT / Apache-2.0 / BSD / ISC / …) skills are included, none relicensed. Each row carries
  `source`, `source_url`, and `license`, so downstream use must follow the per-skill terms.

Full GREEN/RED/YELLOW policy, license data flow, and opt-out:
[`docs/licence-and-governance.md`](docs/licence-and-governance.md).

## Testing

```bash
pip install -e ".[dev]"
python -m pytest skillcorpus/tests -p no:cacheprovider --import-mode=importlib
```

## Citation

```bibtex
@article{wang2026skillcorpus,
  title         = {SkillCorpus: Consolidating and Evaluating the Open Skill Ecosystem for Real-World LLM Agents},
  author        = {Wang, Yanze and Yao, Pengfei and Sun, Tianyi and Hu, Chuanrui and Xiao, Yan and Luo, Xiaotian and Han, Yunyun and Chen, Yifan and Sun, Jun and Deng, Yafeng},
  year          = {2026},
  eprint        = {2607.15557},
  archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2607.15557}
}
```
