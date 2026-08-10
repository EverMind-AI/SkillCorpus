<!-- Placeholder links marked `#` — fill in: SkillHub endpoint, Corpus (HF dataset),
     Embedding model (HF), Code (repo). -->

<div align="center">

**English** | [简体中文](README.zh-CN.md)

# SkillCorpus

**Give your agent 96,401 vetted, permissively-licensed skills — and a retriever that picks the right ones for each task.**

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![SkillHub](https://img.shields.io/badge/SkillHub-live-2ea44f.svg)](#)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](#)
[![Models](https://img.shields.io/badge/%F0%9F%A4%97-Retriever%20%2B%20Reranker-yellow.svg)](#)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#license)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

<img src="docs/assets/pipeline.png" alt="SkillCorpus: building the corpus (aggregate + curate) and using it (match + evaluate)" width="100%">

</div>

## What is SkillCorpus

Agent skills — `SKILL.md` files packaging reusable procedural knowledge — are scattered across
thousands of public repositories, redundant, uneven in quality, and unclear on redistribution
rights. SkillCorpus turns that pool into something an agent can actually draw from, in four stages:

- **`aggregate`** — discover and clone skills from public `SKILL.md` repositories.
- **`curate`** — parse · safety · licence gate · dedup · 16-class classification · 3-facet quality scoring.
- **`match`** — SkillRouter: a fine-tuned bi-encoder + reranker + LLM selector that picks skills for a task.
- **`evaluate`** — three real-world agent benchmarks, two harnesses, open and frontier backbones.

~821,000 crawled files in, 96,401 skills out — every one carrying its upstream licence, and every
source repository licence-audited so the released set is commercially redistributable.

## 📰 News

<!-- TODO(@team): add the corpus / SkillHub / model release entries as they land. -->

- **2026-08-06** — Paper v5 on [arXiv](https://arxiv.org/abs/2607.15557).

## 📦 What we release

| | Artifact | What | Link |
|---|---|---|---|
| 🌐 | **SkillHub** | hosted retrieval endpoint over the corpus — no install | [endpoint](#) |
| 📚 | **Corpus** | `skills.parquet` + `attachments.tar.zst` + dataset card | [🤗 HuggingFace](#) |
| 🔡 | **Retrieval model** | `Qwen3-Embedding-0.6B` fine-tuned as the bi-encoder (2048-ctx) | [🤗 HuggingFace](#) |
| 🎯 | **Reranker model** | `Qwen3-Reranker-0.6B` fine-tuned with listwise CE (4096-ctx) | [🤗 HuggingFace](#) |
| 🛠️ | **Code** | this repo — `aggregate` · `curate` · `match` · `evaluate` · `export` | [GitHub](#) |

<div align="center">
<img src="docs/assets/taxonomy.png" alt="16-class distribution over the 96,401 active skills" width="58%">
</div>

96,401 skills organised by a 16-class taxonomy and three quality facets
(utility / robustness / safety), with 1024-dim retrieval embeddings. Column contract:
[`docs/corpus-schema.md`](docs/corpus-schema.md).

## 📊 Results

Pass rate with no skills → with SkillCorpus, same harness, same backbone
([paper, Table 1](https://arxiv.org/abs/2607.15557)):

| Harness × backbone | SkillsBench | GDPVal | QwenClawBench |
|---|---|---|---|
| OpenClaw × Qwen3.5-27B | 8.8 → **13.0** | 81.2 → **83.1** | 65.2 → **66.7** |
| OpenClaw × Qwen3.5-397B | 11.1 → **16.9** | 82.2 → **84.0** | 65.7 → **67.0** |
| Raven × Qwen3.5-27B | 10.0 → **16.5** | 82.6 → **83.8** | 66.9 → **70.8** |
| Raven × Qwen3.5-397B | 9.2 → **22.6** | 84.0 → **85.2** | 68.8 → **73.2** |
| **Pooled ∆** | **+7.5**±2.3 (z=3.2) | **+1.51**±0.49 (z=3.1) | **+2.79**±0.70 (z=4.0) |

The gain is largest where the task needs procedural knowledge the model does not already have
(SkillsBench), and smallest on open-ended economic tasks it can already do (GDPVal).

## 🚀 Quick Start

| You want | Go to | Needs |
|---|---|---|
| skills for a task, right now | [A. Query SkillHub](#a-query-skillhub) | nothing — one HTTP call |
| the data, to analyse or index yourself | [B. Load the corpus](#b-load-the-corpus) | `pip install datasets` |
| your agent to use skills automatically | [C. Plug it into your agent](#c-plug-it-into-your-agent) | a harness that can inject a system prompt |

Curating **your own** sources instead? See [Build your own corpus](#build-your-own).

### A. Query SkillHub

[SkillHub](https://skillhub.evermind.ai) serves the corpus over three tiers, cheapest first —
most skills are pure instructions, so tier 2 is usually where you stop:

| Tier | Endpoint | Returns | Download? |
|---|---|---|---|
| 1. discover | `GET /openapi/v1/skills?q=` (or `/skills/search` with filters) | metadata, **no body** | no |
| 2. read | `GET /openapi/v1/skills/{ref}` | `skill_md` + `subscores` + `files` | no |
| 3. execute | `GET /openapi/v1/skills/{ref}/download?source=` | zip with `scripts/` | yes |

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills/search?q=extract+tables+from+a+PDF&category=DOC-PROC&min_score=0.75&limit=2"
```

Every response is enveloped; `status == 0` means success:

```json
{"error": "success", "requestId": "…", "status": 0, "result": {
  "items": [{
    "id": "db400aae-c1b1-4cc1-903e-52776418c927",
    "skill_id": "NousResearch/hermes-agent/ocr-and-documents",
    "name": "ocr-and-documents",
    "description": "Extract text from PDFs/scans (pymupdf, marker-pdf).",
    "source": "NousResearch/hermes-agent", "category": "DOC-PROC",
    "quality_score": 0.808, "license": "MIT", "tags": ["ocr", "documents"],
    "github_star": 188943, "install_count": 3,
    "download_url": "https://skillhub.evermind.ai/openapi/v1/skills/db400aae-…/download"
  }], "total": 20}}
```

Then fetch the body with the `id` and inject it into your agent's prompt — that is the whole
loop. [`examples/skillhub_demo.py`](examples/skillhub_demo.py) runs all three tiers:

```bash
# search + read the bodies — stdlib only, no install, no API key
python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

# also fetch the bundled scripts of the top hit
python examples/skillhub_demo.py --install ./skills "convert a PDF to images"

# retrieve AND run the task with the bodies injected
export OPENAI_API_KEY=...
python examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"
```

```
task: extract tables from a scanned PDF invoice

[1/2] search  → 2 hit(s), metadata only
  1. ocr-and-documents   q=0.808  DOC-PROC  MIT
     Extract text from PDFs/scans (pymupdf, marker-pdf).
  2. document-workflows  q=0.86   DOC-PROC  MIT
     Build end-to-end document processing workflows and pipelines …

[2/2] detail  → fetching skill_md for 2 skill(s)
  ocr-and-documents: 4916 chars  u=8 r=7 s=9  files=4  flags=['no_steps']
  document-workflows: 31628 chars  u=9 r=9 s=9  files=7

→ built a prompt of 36,742 chars with the skill bodies injected
```

Rate limits are per IP: 120/min for discover and read, 30/min for download.
Full field list and error codes: [`docs/integrations.md`](docs/integrations.md).

### B. Load the corpus

```python
from datasets import load_dataset

skills = load_dataset("<org>/skillcorpus", split="train")   # 96,401 rows
skills.filter(lambda r: r["category"] == "DOC-PROC")
```

Attachments (`scripts/`, `references/`) ship as a sibling `attachments.tar.zst`.

### C. Plug it into your agent

<details>
<summary><b>Raven</b> — first-party SkillHub source</summary>

Raven fuses SkillHub with its local and Everos skill sources via weighted RRF
(`skillForge.router`):

```yaml
skillForge:
  enabled: true
  router:
    top_k: 5
    weights: { local: 1.0, everos: 0.9, hub: 0.85 }
    hub:
      endpoint: https://skillhub.evermind.ai
      api_key: null          # public skills need none
      timeout_s: 2.0
      min_safety: 0.7        # drop skills below this score_safety
      source: raven          # download tag for install stats
```
</details>

<details>
<summary><b>Any other harness</b> — OpenClaw, Hermes, Claude Code, …</summary>

There is no first-party plugin yet, but every harness that reads a skills
directory works with tier 3: download the bundle and drop it in.

```bash
python examples/skillhub_demo.py --install ~/.claude/skills "convert a PDF to images"
#                                          ~/.hermes/skills      (Hermes)
#                                          ~/.openclaw/skills    (OpenClaw)
```

For prompt-injection harnesses, skip the download: fetch `skill_md` from tier 2 and
prepend it to the system prompt — that is what `build_prompt()` in the demo does, in
six lines.
</details>

Full contract: [`docs/integrations.md`](docs/integrations.md).

## 🧩 How it works

```
skillcorpus/
├── core/       data models · SQLite/faiss store · LLM & embedding clients
├── aggregate/  source registry + multi-repo clone
├── curate/     parse · safety · licence · classify · quality · dedup + full-library passes
├── export/     corpus writer (parquet + attachments + dataset card)
├── match/      SkillRouter — retrieval stack (bi-encoder + reranker)   ← isolated deps
├── evaluate/   skillsbench · qwenclawbench · gdpval benchmarks          ← isolated deps
└── cli.py      build · stats · export
```

`cli build` runs the whole curation chain
(`ingest → quality_pass → dedup_pass → licence_audit → export.corpus`). LLM classification and
quality scoring degrade gracefully to rules when no model endpoint is reachable, so the pipeline
always runs end to end.

`match/` and `evaluate/` are standalone toolkits with their own `requirements.txt`
(torch / transformers, per benchmark); they are **not** pulled in by `pip install` of the producer.

- **Retrieval** — [`skillcorpus/match/`](skillcorpus/match): fine-tune the Qwen3 bi-encoder +
  reranker on synthetic queries, then rank skills for a query. Metrics (nDCG / MRR / Hit / Recall)
  via `eval_compare.py`.
- **Benchmarks** — [`skillcorpus/evaluate/`](skillcorpus/evaluate): `skillsbench`,
  `qwenclawbench`, `gdpval` — each self-contained with its own README and dependencies.

<a id="build-your-own"></a>

## 🛠️ Build your own corpus

Only needed if you want to curate **your own** sources. Requires an LLM endpoint for
classification / quality scoring and an embedding endpoint for dedup — see
[`docs/running.md`](docs/running.md).

```bash
git clone <repo-url> skillcorpus && cd skillcorpus
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip && pip install -e .

python -m skillcorpus.cli build     # 4 demo sources -> curate -> export
python -m skillcorpus.cli stats     # counts by source / category / licence
python -m skillcorpus.cli export --out ./corpus
```

Only skills from GREEN-licensed **sources** are exported (the demo trusts the whitelist in
`audit/license_safe_sources.json` wholesale; production gates per source-repo SPDX). The per-row
`license` is each skill's declared value, so a demo corpus can still carry non-GREEN `license`
strings. Use `--sources-config your.yaml` for your own registry, or `--source <name>` for one source.

```bash
pip install -e ".[dev]"
python -m pytest skillcorpus/tests -p no:cacheprovider --import-mode=importlib
```

## 🗺️ Roadmap

<!-- TODO(@team): this is a first pass from known gaps — edit to match your plan. -->

- [x] Curation pipeline: 16-class taxonomy, 3-facet quality, per-source licence audit
- [x] Fine-tuned retrieval stack + three-benchmark evaluation
- [ ] Public SkillHub endpoint
- [ ] Corpus, retrieval model and reranker on HuggingFace
- [ ] Inference entry point in `match/` (currently training scripts only)
- [ ] Hermes integration

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

## License

- **Code** — Apache-2.0 (the `match/` and `evaluate/` toolkits are each MIT — see their own `LICENSE`).
- **Corpus** — every skill keeps its **original upstream licence**; only GREEN
  (MIT / Apache-2.0 / BSD / ISC / …) skills are included, none relicensed. Each row carries
  `source`, `source_url`, and `license`, so downstream use must follow the per-skill terms.

Full GREEN/RED/YELLOW policy, licence data flow, and opt-out:
[`docs/licence-and-governance.md`](docs/licence-and-governance.md).
