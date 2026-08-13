<!-- All release links are live: SkillHub, the code repo, both retrieval models, and
     the 1k demo corpus. The full 96,401-skill corpus is not published yet (see the
     Availability and roadmap sections). -->

<div align="center">

# SkillCorpus

**Curate scattered agent skills and retrieve task-relevant ones.**

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![SkillHub](https://img.shields.io/badge/SkillHub-live-2ea44f.svg)](https://evermind.ai/skillhub)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k)
[![Models](https://img.shields.io/badge/%F0%9F%A4%97-Retriever%20%2B%20Reranker-yellow.svg)](https://huggingface.co/EverMind-AI/models)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#license)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

**English** | [简体中文](README.zh-CN.md)

</div>

## From open-source foundation to live product

| Entry point | What it provides | Value to developers and enthusiasts |
|---|---|---|
| [**SkillCorpus GitHub Repo**](https://github.com/EverMind-AI/SkillCorpus) | Open code for corpus aggregation, governance, deduplication, classification and export, plus retrieval, evaluation, and integrations. | Learn the end-to-end stack, or fork its corpus pipeline and model-serving components to build your own skill library and retrieval workflow with your sources and policies. Each component's license applies, and third-party skills and data retain their upstream terms. |
| [**Hugging Face models & demo**](https://huggingface.co/EverMind-AI) | A [1K demo corpus](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k), an [embedding model](https://huggingface.co/EverMind-AI/skillcorpus-embedding-0.6b) that produces recall vectors, and a [re-ranker](https://huggingface.co/EverMind-AI/skillcorpus-reranker-0.6b) that scores task-to-candidate pairs. | Prototype, evaluate, or self-host retrieval without training the initial models from scratch. You still provide the corpus index, candidate-selection flow, and orchestration. |
| [**SkillHub**](https://evermind.ai/skillhub) | The live product built from this stack: a hosted catalog and API over the curated skill library. | Experience the system immediately. Search skills, inspect provenance, read `SKILL.md`, and download bundles for compatible agents without cloning the repo or running the models. |

> **Availability:** the public downloadable corpus is currently the 1K demo. **96,401 skills** refers to the paper's production snapshot, which is not yet published as a full download.

## Try SkillHub in 30 seconds

No install or API key required.

Ask the hosted SkillHub for a task-specific skill:

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills?q=extract+tables+from+a+PDF"
```

Or [open the same live query in your browser](https://skillhub.evermind.ai/openapi/v1/skills?q=extract%20tables%20from%20a%20PDF). An abridged result looks like this:

```yaml
name: extract-tables-from-pdf
category: DOC-PROC
source: mzlzyCA/html-markdown
source_url: https://github.com/mzlzyCA/html-markdown
license: MIT
quality_score: 0.708
```

Live rankings and metadata may change as the catalog evolves.

Or use the standard-library-only demo to search and read the matching `SKILL.md` bodies:

```bash
python3 examples/skillhub_demo.py "extract tables from a scanned PDF invoice"
```

The response starts with metadata, then lets you fetch `skill_md` to inject into an agent prompt. The complete three-tier flow (search, read, and optional download) is in [`examples/skillhub_demo.py`](examples/skillhub_demo.py); the API contract is in [`docs/integrations.md`](docs/integrations.md).

> **Before you run a downloaded skill:** its bundled scripts, assets, and references are third-party content. Review them and their upstream terms before executing anything.

## Why SkillCorpus

Agent skills (`SKILL.md` files that package reusable procedural knowledge) are distributed across public repositories. They can be redundant, uneven in quality, or unclear to redistribute. SkillCorpus turns that fragmented material into a retrieval-ready corpus through four stages:

- **`aggregate`:** discover and clone public `SKILL.md` repositories.
- **`curate`:** parse, apply text-based safety signals and source-level license policy, deduplicate, classify into 16 classes, and score three quality facets.
- **`match`:** retrieve candidates with a fine-tuned bi-encoder and rerank them for task relevance.
- **`evaluate`:** test skill use across three agent benchmarks, two harnesses, and open and frontier backbones.

Quality and safety facet scores support corpus curation and filtering; they are not a guarantee that a skill, its dependencies, or its bundled files are safe for your environment.

<div align="center">
<img src="docs/assets/pipeline.png" alt="SkillCorpus: building the corpus (aggregate plus curate) and using it (match plus evaluate)" width="100%">
</div>

## Concept: GitHub issue to agent handoff

**Paste a GitHub issue. Prepare a reviewable handoff for your agent.**

> **Not yet released.** SkillHub can already search, read, and download individual skills. Repository analysis and multi-skill handoffs are not yet available.

The handoff would combine the issue with a repository snapshot into one portable artifact:

```text
GitHub issue + repository snapshot
              -> task scope, cited files, declared checks, candidate skills
              -> review as Markdown, give to an agent, or paste into an Issue/PR
```

Maintainers could confirm the task boundary before code changes begin, contributors could start unfamiliar work with relevant context and checks, and teams could keep the handoff visible to every collaborator. Candidate skills would include rationale, provenance, and declared license for review; they would remain recommendations, not guarantees of correctness, safety, or license compliance.

## Use it with your agent

### SkillHub

[SkillHub](https://evermind.ai/skillhub) serves the corpus in three tiers: discover metadata, read `skill_md`, and download a zip with `scripts/`. Most skills are instructions only, so the read tier is often enough.

```bash
# search + read bodies; stdlib only, no install, no API key
python3 examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

# download the bundled files from the top hit into a local skills directory
python3 examples/skillhub_demo.py --install ./skills "convert a PDF to images"

# retrieve and run the task with an OpenAI-compatible LLM
export OPENAI_API_KEY=...
python3 examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"
```

For OpenRouter or local vLLM, also set `OPENAI_BASE_URL`; see the inline help in the demo. Endpoints, response envelopes, status codes, and rate limits are documented in [`docs/integrations.md`](docs/integrations.md).

### Raven and other harnesses

<details>
<summary><b>Raven</b>: first-party SkillHub source</summary>

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
<summary><b>Any other harness</b>: OpenClaw, Hermes, Claude Code, …</summary>

There is no first-party plugin yet. A harness that reads a skills directory can use the download tier:

```bash
python3 examples/skillhub_demo.py --install ~/.claude/skills "convert a PDF to images"
#                                          ~/.hermes/skills      (Hermes)
#                                ~/.openclaw/workspace/skills    (OpenClaw)
```

For prompt-injection harnesses, fetch `skill_md` from the read tier and prepend it to the system prompt, as `build_prompt()` in the demo does.
</details>

## Run the retrieval models yourself

Use the released retrieval checkpoints when you want selection to run in your own environment. Start with the public 1K demo corpus (the full 96,401-skill corpus is not published):

Choose a loader path and install its dependencies separately from the retrieval server dependencies:

```bash
pip install datasets
# Or, for the direct Parquet path:
pip install pandas pyarrow
```

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
EMBEDDING_MODEL=EverMind-AI/skillcorpus-embedding-0.6b \
RERANKER_MODEL=EverMind-AI/skillcorpus-reranker-0.6b \
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

Metric definitions: SkillsBench reports pass@1, GDPVal reports LLM-judge reward, and QwenClawBench reports its hybrid score; all values are shown ×100.

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

- **2026-08-12:** Retrieval models (bi-encoder + reranker) and a 1,000-skill demo corpus released on [Hugging Face](https://huggingface.co/EverMind-AI).
- **2026-08-06:** Paper v5 published on [arXiv](https://arxiv.org/abs/2607.15557).

## Roadmap

- [x] Curation pipeline: 16-class taxonomy, 3-facet quality, per-source license audit
- [x] Fine-tuned retrieval stack + three-benchmark evaluation
- [x] Public SkillHub endpoint
- [x] Retrieval models (bi-encoder + reranker) and a 1K demo corpus on Hugging Face
- [ ] GitHub issue to source-cited agent handoff and review manifest
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

- **Code:** Apache-2.0. The `match/` and `evaluate/` toolkits are each MIT; see their own `LICENSE`.
- **Corpus:** export is gated to skills from GREEN-licensed source repositories. Each row keeps its declared upstream `license`, which may differ from the source-repository license and still requires review; nothing is relicensed. Downstream use must follow the per-skill terms.

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
<td><a href="https://github.com/EverMind-AI/SkillCorpus">SkillCorpus</a> - open curation pipeline, SkillRouter retrieval models, public <a href="https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k">1K demo corpus</a>, <a href="https://evermind.ai/skillhub">SkillHub</a> integration, agent integrations, and benchmarks.</td>
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
