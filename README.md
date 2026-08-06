<!-- Placeholder links marked `#` — fill in: Corpus (HF dataset),
     Embedding model (HF), Code (repo). -->

# SkillCorpus

**SkillCorpus is a framework that aggregates, curates, matches, and evaluates the open agent-skill ecosystem at scale** — consolidating ~821,000 crawled skills into a 96,000-skill, permissively-licensed corpus, and releasing the retrieval stack and evaluation suite built around it.

Its four stages are the package's subpackages:

1. **`aggregate`** — discover + clone skills from public `SKILL.md` repositories.
2. **`curate`** — parse · safety · license-gate · dedup · 16-class classify · quality scoring (utility / robustness / safety).
3. **`match`** — SkillRouter: a fine-tuned bi-encoder + reranker that retrieves skills for a task.
4. **`evaluate`** — three benchmarks: `skillsbench` · `qwenclawbench` · `gdpval`.

[Paper](https://arxiv.org/abs/2607.15557) · [Corpus](#) · [Embedding model](#) · [Code](#)

The framework **releases** three artifacts: the **corpus** (parquet + attachments + dataset card), the fine-tuned **embedding model**, and this **code**. Every skill keeps its **original upstream license** — only permissively (GREEN: MIT / Apache-2.0 / BSD / ISC / …) licensed skills are included, none relicensed, and each row carries its `source` / `source_url` / `license`; full terms under [License](#license).

## Released artifacts

| | Artifact | What | Link |
|---|---|---|---|
| 📚 | **Corpus** | `skills.parquet` + `attachments.tar.zst` + dataset card | [🤗 HuggingFace](#) |
| 🔡 | **Embedding model** | `Qwen3-Embedding-0.6B` fine-tuned for skill retrieval (2048-ctx) | [🤗 HuggingFace](#) |
| 🛠️ | **Code** | the framework — `aggregate` · `curate` · `match` · `evaluate` · `export` | [GitHub](#) |

## License

- **Code** — Apache-2.0 (the `match/` and `evaluate/` toolkits are each MIT — see their own `LICENSE`).
- **Corpus** — every skill keeps its **original upstream license**; only GREEN (MIT / Apache-2.0 / BSD / ISC / …) skills are included, none relicensed. Each row carries `source`, `source_url`, and `license`, so downstream use must follow the per-skill terms.

Full GREEN/RED/YELLOW policy, license data flow, and opt-out: [`docs/licence-and-governance.md`](docs/licence-and-governance.md).

## Quickstart

```bash
# install — Python >= 3.10
git clone <repo-url> skillcorpus && cd skillcorpus
pip install -e .

# build the demo corpus: clone 4 public skill repos -> curate -> export
python -m skillcorpus.cli build              # -> <lib>/corpus/{skills.parquet, attachments.tar.zst, README.md}
python -m skillcorpus.cli stats              # counts by source / category / license

# re-export the corpus from an existing library, without rebuilding
python -m skillcorpus.cli export --out ./corpus
```

Only GREEN-licensed skills are exported. The demo ships the 4-source `configs/sources.demo.yaml`; use `--sources-config your.yaml` for your own registry, or `--source <name>` for a single source. Full config / endpoints / reproducibility: [`docs/running.md`](docs/running.md). Output contract: [`docs/corpus-schema.md`](docs/corpus-schema.md).

## How it works

```
aggregate ─────────────► curate ──────────────────────────────────► export
 fetch public repos       parse · safety · license                   skills.parquet
                          classify · quality · dedup · license-gate   + attachments.tar.zst + card
```

`cli build` runs the whole chain (`ingest → quality_pass → dedup_pass → license_audit → export.corpus`). LLM classification and quality scoring degrade gracefully to rules when no model endpoint is reachable, so the pipeline always runs end to end.

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

`match/` and `evaluate/` are standalone toolkits with their own `requirements.txt` (torch / transformers, per benchmark); they are **not** pulled in by `pip install` of the producer.

## Retrieval & evaluation

- **Retrieval** — [`skillcorpus/match/`](skillcorpus/match): fine-tune the Qwen3 bi-encoder + reranker on synthetic queries, then rank skills for a query. Retrieval metrics (nDCG / MRR / Hit / Recall) via `eval_compare.py`.
- **Benchmarks** — [`skillcorpus/evaluate/`](skillcorpus/evaluate): `skillsbench`, `qwenclawbench`, `gdpval` — each self-contained with its own README and dependencies.

## Testing

```bash
pip install -e ".[dev]"
python -m pytest skillcorpus/tests -p no:cacheprovider --import-mode=importlib
```

## Citation

```bibtex
@article{wang2026skillcorpus,
  title         = {SkillCorpus: Consolidating and Evaluating the Open Skill Ecosystem for Real-World LLM Agents},
  author        = {Wang, Yanze and Yao, Pengfei and Sun, Tianyi and Hu, Chuanrui and Xiao, Yan and Han, Yunyun and Chen, Yifan and Sun, Jun and Deng, Yafeng},
  year          = {2026},
  eprint        = {2607.15557},
  archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2607.15557}
}
```
