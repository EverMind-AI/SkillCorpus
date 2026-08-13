<!-- All release links are live: SkillHub, the code repo, both retrieval models, and
     the 1k demo corpus. The full 96,401-skill corpus is not published yet (see the
     Availability and roadmap sections). -->

<div align="center">

**English** | [简体中文](README.zh-CN.md)

# SkillCorpus

**Find a reusable skill for your agent in seconds — then read it, download it, or put it to work.**

Search the public [SkillHub](https://evermind.ai/skillhub), try the [1,000-skill demo corpus](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k), run the [retrieval models](https://huggingface.co/EverMind-AI/models), or read the [paper](https://arxiv.org/abs/2607.15557).

Part of the EverMind agent stack — [Raven](https://github.com/EverMind-AI/raven), the terminal-native agent harness · [EverOS](https://github.com/EverMind-AI/EverOS), the memory substrate it builds on · SkillCorpus, the community skill corpus they retrieve from.

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![SkillHub](https://img.shields.io/badge/SkillHub-live-2ea44f.svg)](https://evermind.ai/skillhub)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k)
[![Models](https://img.shields.io/badge/%F0%9F%A4%97-Retriever%20%2B%20Reranker-yellow.svg)](https://huggingface.co/EverMind-AI/models)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#license)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

</div>

## Try it in 30 seconds — no install or API key

Ask the hosted SkillHub for a task-specific skill:

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills?q=extract+tables+from+a+PDF"
```

Or use the standard-library-only demo to search and read the matching `SKILL.md` bodies:

```bash
python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"
```

The response starts with metadata, then lets you fetch `skill_md` to inject into an agent prompt. See the complete three-tier flow — search, read, and optional download — in [`examples/skillhub_demo.py`](examples/skillhub_demo.py) and the API contract in [`docs/integrations.md`](docs/integrations.md).

> **Before you run a downloaded skill:** its bundled scripts, assets, and references are third-party content. Review them and their upstream terms before executing anything.

## What you can do with it

| If you are… | SkillCorpus helps you… | Start here |
|---|---|---|
| Building an agent | find relevant procedural instructions without hand-curating a skills folder | [Search SkillHub](https://evermind.ai/skillhub) |
| Automating a task | retrieve a `SKILL.md`, add it to your prompt, or download its bundle for a compatible harness | [Run the demo](examples/skillhub_demo.py) |
| Evaluating retrieval | test the released embedding model and reranker with your own candidate set | [Get the models](https://huggingface.co/EverMind-AI/models) |
| Building a specialized library | aggregate, license-audit, curate, deduplicate, and export your own sources | [Build a corpus](#build-your-own-corpus) |

## Available today

| Artifact | Available now | What it is | Boundary |
|---|---|---|---|
| 🌐 **SkillHub** | [Hosted service](https://evermind.ai/skillhub) | public retrieval over the corpus; no install for search and read | hosted API, not a self-hosted package |
| 📚 **Corpus** | [1K demo on Hugging Face](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k) | `skills.parquet`, `attachments.tar.zst`, and dataset card | the public download is a 1,000-skill demo, not the full paper snapshot |
| 🔡 **SkillRouter** | [Bi-encoder](https://huggingface.co/EverMind-AI/skillcorpus-embedding-0.6b) + [reranker](https://huggingface.co/EverMind-AI/skillcorpus-reranker-0.6b) | fine-tuned retrieval checkpoints based on `Qwen3-Embedding-0.6B` and `Qwen3-Reranker-0.6B` | you provide the corpus, candidate retrieval flow, and serving environment |
| 🛠️ **Code** | [This repository](https://github.com/EverMind-AI/SkillCorpus) | the `aggregate`, `curate`, `match`, `evaluate`, and `export` toolkits | code does not include a local SkillHub-compatible corpus index or router |

The paper's production snapshot contains **96,401 skills** selected from roughly 821,000 crawled files. That full snapshot is not yet downloadable; the current public corpus is the 1K demo above.

## Why SkillCorpus

Agent skills — `SKILL.md` files that package reusable procedural knowledge — are distributed across public repositories. They can be redundant, uneven in quality, or unclear to redistribute. SkillCorpus turns that fragmented material into a retrieval-ready corpus through four stages:

- **`aggregate`** — discover and clone public `SKILL.md` repositories.
- **`curate`** — parse, safety and license gate, deduplicate, classify into 16 classes, and score three quality facets.
- **`match`** — SkillRouter combines a fine-tuned bi-encoder, reranker, and LLM selector to choose skills for a task.
- **`evaluate`** — test skill use across three agent benchmarks, two harnesses, and open and frontier backbones.

Quality and safety facet scores support corpus curation and filtering; they are not a guarantee that a skill, its dependencies, or its bundled files are safe for your environment.

<div align="center">
<img src="docs/assets/pipeline.png" alt="SkillCorpus: building the corpus (aggregate plus curate) and using it (match plus evaluate)" width="100%">
</div>

## Use it with your agent

### SkillHub

[SkillHub](https://evermind.ai/skillhub) serves the corpus in three tiers: discover metadata, read `skill_md`, and download a zip with `scripts/`. Most skills are instructions only, so the read tier is often enough.

```bash
# search + read bodies — stdlib only, no install, no API key
python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

# download the bundled files from the top hit into a local skills directory
python examples/skillhub_demo.py --install ./skills "convert a PDF to images"

# retrieve and run the task with an OpenAI-compatible LLM
export OPENAI_API_KEY=...
python examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"
```

For OpenRouter or local vLLM, also set `OPENAI_BASE_URL`; see the inline help in the demo. Endpoints, response envelopes, status codes, and rate limits are documented in [`docs/integrations.md`](docs/integrations.md).

### Raven and other harnesses

<details>
<summary><b>Raven</b> — first-party SkillHub source</summary>

Raven combines SkillHub with local and EverOS skill sources through weighted RRF (`skillForge.router`):

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
      min_safety: 0.7        # filters by the curation safety facet; not a security guarantee
      source: raven          # download tag for install stats
```
</details>

<details>
<summary><b>Any other harness</b> — OpenClaw, Hermes, Claude Code, …</summary>

There is no first-party plugin yet. A harness that reads a skills directory can use the download tier:

```bash
python examples/skillhub_demo.py --install ~/.claude/skills "convert a PDF to images"
#                                          ~/.hermes/skills      (Hermes)
#                                ~/.openclaw/workspace/skills    (OpenClaw)
```

For prompt-injection harnesses, fetch `skill_md` from the read tier and prepend it to the system prompt, as `build_prompt()` in the demo does.
</details>

## Run the retrieval models yourself

Use the released retrieval checkpoints when you want selection to run in your own environment. Start with the public 1K demo corpus (the full 96,401-skill corpus is not published):

```python
from datasets import load_dataset
skills = load_dataset("EverMind-AI/skillcorpus-demo-1k", split="train")

# Or read the downloaded parquet directly.
import pandas as pd
skills = pd.read_parquet("skills.parquet")
```

Attachments (`scripts/`, `references/`) ship in the sibling `attachments.tar.zst`. Serve the two checkpoints behind the provided endpoint:

```bash
pip install -r skillcorpus/match/requirements.txt
EMBEDDING_MODEL=<embedding checkpoint dir> RERANKER_MODEL=<reranker checkpoint dir> \
  bash skillcorpus/match/scripts/run_server.sh
```

The self-hosted server exposes **`/embed`** and **`/score`** ([Serving](skillcorpus/match/README.md#serving)). It does **not** provide a local SkillHub-compatible `/openapi/v1/skills` corpus index or end-to-end router: load a corpus and compose candidate retrieval, top-k selection, and reranking in your application. The same embedding endpoint can support producer deduplication with `embedding.provider: skillrouter_remote`.

## Paper results

These are results reported in the [paper, Table 1](https://arxiv.org/abs/2607.15557), not a promise of the results you will get from a fresh clone, the public 1K demo, or a different agent configuration.

| Harness × backbone | SkillsBench | GDPVal | QwenClawBench |
|---|---:|---:|---:|
| OpenClaw × Qwen3.5-27B | 8.8 → **13.0** | 81.2 → **83.1** | 65.2 → **66.7** |
| OpenClaw × Qwen3.5-397B | 11.1 → **16.9** | 82.2 → **84.0** | 65.7 → **67.0** |
| Raven × Qwen3.5-27B | 10.0 → **16.5** | 82.6 → **83.8** | 66.9 → **70.8** |
| Raven × Qwen3.5-397B | 9.2 → **22.6** | 84.0 → **85.2** | 68.8 → **73.2** |
| **Pooled ∆** | **+7.5**±2.3 (z=3.2) | **+1.51**±0.49 (z=3.1) | **+2.79**±0.70 (z=4.0) |

The reported gain is largest on SkillsBench, where tasks need procedural knowledge the model may not already contain, and smallest on the more open-ended GDPVal tasks.

## Build your own corpus

This path is for curating **your own** sources. It needs an LLM endpoint for classification and quality scoring plus an embedding endpoint for deduplication; see [`docs/running.md`](docs/running.md).

```bash
git clone https://github.com/EverMind-AI/SkillCorpus.git skillcorpus && cd skillcorpus
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip && pip install -e .

python -m skillcorpus.cli build     # 4 demo sources -> curate -> export
python -m skillcorpus.cli stats     # counts by source / category / license
python -m skillcorpus.cli export --out ./corpus
```

`cli build` runs `ingest → quality_pass → dedup_pass → license_audit → export.corpus`. The producer's LLM classification and quality scoring fall back to rules if no model endpoint is reachable, so its pipeline can run end to end. `match/` and `evaluate/` are standalone toolkits with their own dependencies and are not installed by the producer package.

Only skills from GREEN-licensed **sources** are exported. The demo trusts the whitelist in `audit/license_safe_sources.json` wholesale; production gates per source-repository SPDX. Each row keeps its declared `license`, so a demo corpus can still contain non-GREEN license strings. Use `--sources-config your.yaml` for your own registry or `--source <name>` for one source.

## Trust, provenance, and availability

- The public corpus is the [1K demo](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k); **96,401** refers to the paper's production snapshot and is not currently a downloadable corpus.
- Every released corpus row retains `source`, `source_url`, and its original upstream `license`. You are responsible for following the per-skill terms and reviewing third-party scripts, assets, references, dependencies, and instructions before use.
- Curation quality and safety scores can help rank or filter material, but they do not constitute a safety, compatibility, or security guarantee.
- The hosted SkillHub and its availability may differ from a self-hosted retrieval deployment. The self-hosted server only provides `/embed` and `/score`; it is not a local replacement for SkillHub's corpus API.

For the GREEN/RED/YELLOW policy, license data flow, and opt-out process, see [`docs/licence-and-governance.md`](docs/licence-and-governance.md).

## News

- **2026-08-12** — Retrieval models (bi-encoder + reranker) and a 1,000-skill demo corpus released on [Hugging Face](https://huggingface.co/EverMind-AI).
- **2026-08-06** — Paper v5 published on [arXiv](https://arxiv.org/abs/2607.15557).

## Roadmap

- [x] Curation pipeline: 16-class taxonomy, 3-facet quality, per-source license audit
- [x] Fine-tuned retrieval stack + three-benchmark evaluation
- [x] Public SkillHub endpoint
- [x] Retrieval models (bi-encoder + reranker) and a 1K demo corpus on Hugging Face
- [ ] Full 96,401-skill corpus on Hugging Face
- [x] Deployment script for the two retrieval models (`match/`)
- [ ] Hermes integration

## Contributing

Contributions to the corpus pipeline, retrieval tooling, evaluations, integrations, and documentation are welcome. See the repository issues and the component documentation before opening a pull request.

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
- **Corpus** — every skill keeps its **original upstream license**; only GREEN (MIT / Apache-2.0 / BSD / ISC / …) skills are included, none relicensed. Downstream use must follow the per-skill terms.

## EverMind Ecosystem

EverMind is an open-source ecosystem for long-term memory, self-evolving agents, AI-native interfaces, and memory evaluation.

<table>
<tr>
<th colspan="2">EverMind Open-Source Ecosystem</th>
</tr>
<tr>
<td><strong>Memory Runtime</strong></td>
<td><a href="https://github.com/EverMind-AI/EverOS">EverOS</a> - the local memory operating system and research-backed runtime for agent and user memory.</td>
</tr>
<tr>
<td><strong>Self-Improving Agent Harness</strong></td>
<td><a href="https://github.com/EverMind-AI/Raven">Raven</a> - the self-improving agent harness that brings memory, proactivity, context control, and skill evolution into terminal-native agents.</td>
</tr>
<tr>
<td><strong>Agent Skills &amp; Retrieval</strong></td>
<td><a href="https://github.com/EverMind-AI/SkillCorpus">SkillCorpus</a> - open curation pipeline, SkillRouter retrieval models, public <a href="https://evermind.ai/skillhub">SkillHub</a> demo, agent integrations, and benchmarks.</td>
</tr>
<tr>
<td><strong>Algorithm Engine</strong></td>
<td><a href="https://github.com/EverMind-AI/EverAlgo">EverAlgo</a> - stateless extraction, ranking, parsing, and memory operators that power EverOS.</td>
</tr>
<tr>
<td><strong>Hypergraph Memory</strong></td>
<td><a href="https://github.com/EverMind-AI/HyperMem">HyperMem</a> - hypergraph memory for long-term conversations, with its own benchmark-backed topic -&gt; episode -&gt; fact retrieval method.</td>
</tr>
<tr>
<td><strong>Benchmarks</strong></td>
<td><a href="https://github.com/EverMind-AI/EverMemBench">EverMemBench</a> · <a href="https://github.com/EverMind-AI/EvoAgentBench">EvoAgentBench</a> - evaluation suites for conversational memory and agent self-evolution.</td>
</tr>
<tr>
<td><strong>Long-Context Research</strong></td>
<td><a href="https://github.com/EverMind-AI/MSA">MSA</a> - Memory Sparse Attention for scalable latent memory and 100M-token contexts.</td>
</tr>
<tr>
<td><strong>Personal Memory Layer</strong></td>
<td><a href="https://github.com/EverMind-AI/EverMe">EverMe</a> - CLI and agent plugin suite for cross-device, cross-agent personal memory.</td>
</tr>
<tr>
<td><strong>Developer Integrations</strong></td>
<td><a href="https://github.com/EverMind-AI/evermem-claude-code">evermem-claude-code</a> · <a href="https://github.com/EverMind-AI/everos-plugins">everos-plugins</a> - plugins, skills, and migration tooling for AI coding agents.</td>
</tr>
</table>

Together, these repositories form EverMind's research-to-runtime stack: new memory methods, reusable algorithms, benchmark evidence, and practical agent integrations.
