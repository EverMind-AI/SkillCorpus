<!-- 占位链接标为 `#`，待补：SkillHub endpoint、Corpus (HF dataset)、Embedding model (HF)、Code (repo)。 -->

<div align="center">

[English](README.md) | **简体中文**

# SkillCorpus

**给你的 agent 96,401 个经过筛选、许可合规的技能——外加一个能为每个任务挑出正确技能的检索器。**

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![SkillHub](https://img.shields.io/badge/SkillHub-live-2ea44f.svg)](https://skillhub.evermind.ai)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](#)
[![Models](https://img.shields.io/badge/%F0%9F%A4%97-Retriever%20%2B%20Reranker-yellow.svg)](#)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#许可)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

<img src="docs/assets/pipeline.png" alt="SkillCorpus：构建语料（aggregate + curate）与使用语料（match + evaluate）" width="100%">

</div>

## SkillCorpus 是什么

Agent 技能——也就是把可复用的过程性知识打包起来的 `SKILL.md` 文件——散落在成千上万个公开
仓库里，彼此重复、质量参差，再分发权也说不清。SkillCorpus 把这个池子变成 agent 真正能取用的
东西，分四个阶段：

- **`aggregate`** —— 从公开的 `SKILL.md` 仓库发现并克隆技能。
- **`curate`** —— 解析 · 安全 · 许可门禁 · 去重 · 16 类分类 · 三维质量打分。
- **`match`** —— SkillRouter：微调的 bi-encoder + reranker + LLM selector，为任务挑技能。
- **`evaluate`** —— 三个真实 agent benchmark、两个 harness、开源与前沿 backbone。

约 821,000 个爬取文件进，96,401 个技能出——每个都带着上游原始许可，每个源仓库都做过许可
审计，所以发布出来的这一套是可商用再分发的。

## 📰 动态

<!-- TODO(@team)：语料 / SkillHub / 模型发布后在此追加。 -->

- **2026-08-06** —— 论文 v5 上 [arXiv](https://arxiv.org/abs/2607.15557)。

## 📦 我们发布了什么

| | 产物 | 内容 | 链接 |
|---|---|---|---|
| 🌐 | **SkillHub** | 基于该语料的托管检索端点——无需安装 | [skillhub.evermind.ai](https://skillhub.evermind.ai) |
| 📚 | **语料** | `skills.parquet` + `attachments.tar.zst` + dataset card | [🤗 HuggingFace](#) |
| 🔡 | **检索模型** | 作为 bi-encoder 微调的 `Qwen3-Embedding-0.6B`（2048 上下文） | [🤗 HuggingFace](#) |
| 🎯 | **重排模型** | 用 listwise CE 微调的 `Qwen3-Reranker-0.6B`（4096 上下文） | [🤗 HuggingFace](#) |
| 🛠️ | **代码** | 本仓库 —— `aggregate` · `curate` · `match` · `evaluate` · `export` | [GitHub](#) |

<div align="center">
<img src="docs/assets/taxonomy.png" alt="96,401 个有效技能的 16 类分布" width="58%">
</div>

96,401 个技能，按 16 类体系和三个质量维度（utility / robustness / safety）组织，并带 1024 维
检索向量。字段约定见 [`docs/corpus-schema.md`](docs/corpus-schema.md)。

## 📊 效果

同一个 harness、同一个 backbone，无技能 → 接入 SkillCorpus
（[论文 Table 1](https://arxiv.org/abs/2607.15557)）：

| Harness × backbone | SkillsBench | GDPVal | QwenClawBench |
|---|---|---|---|
| OpenClaw × Qwen3.5-27B | 8.8 → **13.0** | 81.2 → **83.1** | 65.2 → **66.7** |
| OpenClaw × Qwen3.5-397B | 11.1 → **16.9** | 82.2 → **84.0** | 65.7 → **67.0** |
| Raven × Qwen3.5-27B | 10.0 → **16.5** | 82.6 → **83.8** | 66.9 → **70.8** |
| Raven × Qwen3.5-397B | 9.2 → **22.6** | 84.0 → **85.2** | 68.8 → **73.2** |
| **合并 ∆** | **+7.5**±2.3 (z=3.2) | **+1.51**±0.49 (z=3.1) | **+2.79**±0.70 (z=4.0) |

任务越依赖模型本身不具备的过程性知识，收益越大（SkillsBench）；模型本来就能做的开放式
经济类任务收益最小（GDPVal）。

## 🚀 快速开始

| 你想要 | 去看 | 需要什么 |
|---|---|---|
| 马上拿到某个任务对应的技能 | [A. 调用 SkillHub](#a-调用-skillhub) | 什么都不用——一个 HTTP 请求 |
| 拿到数据，自己分析或建索引 | [B. 加载语料](#b-加载语料) | `pip install datasets` |
| 让 agent 自动用上技能 | [C. 接进你的 agent](#c-接进你的-agent) | 一个能注入 system prompt 的 harness |

想策展**自己的**源？见 [构建自己的语料](#build-your-own)。

### A. 调用 SkillHub

[SkillHub](https://skillhub.evermind.ai) 把语料分三级提供——发现（元数据）、读正文
（`skill_md`）、下载（含 `scripts/` 的 zip）。大多数技能是纯指令，读到正文就够了。

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills/search?q=extract+tables+from+a+PDF&category=DOC-PROC&min_score=0.75&limit=2"
```

从结果里取一个 `id`，拉它的 `skill_md`，注入 agent 的 prompt——整个闭环就这么简单。
[`examples/skillhub_demo.py`](examples/skillhub_demo.py) 把三级都跑通了：

```bash
# 检索 + 读正文 —— 纯标准库，不用安装，不用 API key
python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

# 顺带把命中的第一个技能的脚本包拉下来
python examples/skillhub_demo.py --install ./skills "convert a PDF to images"

# 检索并把正文注入 prompt 后真正执行任务
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

端点、响应信封、状态码和限速见 [`docs/integrations.md`](docs/integrations.md)。

### B. 加载语料

```python
from datasets import load_dataset

skills = load_dataset("<org>/skillcorpus", split="train")   # 96,401 行
skills.filter(lambda r: r["category"] == "DOC-PROC")
```

附件（`scripts/`、`references/`）以同级的 `attachments.tar.zst` 形式发布。

### C. 接进你的 agent

<details>
<summary><b>Raven</b> —— 一方 SkillHub 源</summary>

Raven 用带权 RRF 把 SkillHub 与本地、Everos 三个技能源融合（`skillForge.router`）：

```yaml
skillForge:
  enabled: true
  router:
    top_k: 5
    weights: { local: 1.0, everos: 0.9, hub: 0.85 }
    hub:
      endpoint: https://skillhub.evermind.ai
      api_key: null          # 公开技能无需鉴权
      timeout_s: 2.0
      min_safety: 0.7        # 低于此 score_safety 的技能被过滤
      source: raven          # 安装统计用的下载标签
```
</details>

<details>
<summary><b>其他 harness</b> —— OpenClaw、Hermes、Claude Code…</summary>

目前还没有一方插件，但任何会读技能目录的 harness 都能用第三级：把包下下来放进去。

```bash
python examples/skillhub_demo.py --install ~/.claude/skills "convert a PDF to images"
#                                          ~/.hermes/skills      (Hermes)
#                                          ~/.openclaw/skills    (OpenClaw)
```

如果 harness 是靠注入 prompt 的，连下载都不用：从第二级取 `skill_md` 拼到 system prompt
前面即可——demo 里的 `build_prompt()` 就是这么做的，六行。
</details>

完整契约见 [`docs/integrations.md`](docs/integrations.md)。

## 🧩 工作原理

```
skillcorpus/
├── core/       数据模型 · SQLite/faiss 存储 · LLM 与 embedding 客户端
├── aggregate/  源注册表 + 多仓库克隆
├── curate/     解析 · 安全 · 许可 · 分类 · 质量 · 去重 + 全库扫描
├── export/     语料写出（parquet + 附件 + dataset card）
├── match/      SkillRouter —— 发布的两个模型 + 训练配方            ← 依赖独立
├── evaluate/   skillsbench · qwenclawbench · gdpval 评测           ← 依赖独立
└── cli.py      build · stats · export
```

`cli build` 会跑完整条策展链路（`ingest → quality_pass → dedup_pass → licence_audit →
export.corpus`）。当没有可达的模型端点时，LLM 分类和质量打分会优雅降级为规则实现，因此
管线总能端到端跑通。

`match/` 和 `evaluate/` 是独立工具包，各有自己的 `requirements.txt`（torch / transformers，
按 benchmark 区分）；**不会**被 `pip install` 主包时带进来。

- **检索** —— [`skillcorpus/match/`](skillcorpus/match) 就是**发布的那两个模型**：
  从 `Qwen3-Embedding-0.6B` 微调的 bi-encoder 负责候选召回，从 `Qwen3-Reranker-0.6B` 微调的
  reranker 负责对候选打分重排。SkillHub 已托管这两个模型；要自己部署见
  [部署脚本](#)。该目录同时包含训练配方（合成 query → InfoNCE → listwise CE）和
  `eval_compare.py`（检索指标 nDCG / MRR / Hit / Recall）。
- **评测** —— [`skillcorpus/evaluate/`](skillcorpus/evaluate)：`skillsbench`、`qwenclawbench`、
  `gdpval`，各自独立，带自己的 README 和依赖。

<a id="build-your-own"></a>

## 🛠️ 构建自己的语料

只有当你想策展**自己的**源时才需要。需要一个 LLM 端点做分类和质量打分、一个 embedding
端点做去重——见 [`docs/running.md`](docs/running.md)。

```bash
git clone <repo-url> skillcorpus && cd skillcorpus
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip && pip install -e .

python -m skillcorpus.cli build     # 4 个 demo 源 -> 策展 -> 导出
python -m skillcorpus.cli stats     # 按 source / category / license 统计
python -m skillcorpus.cli export --out ./corpus
```

只有来自 GREEN 许可**源仓库**的技能会被导出（demo 直接信任
`audit/license_safe_sources.json` 里的白名单；生产环境按源仓库 SPDX 逐个把关）。每行的
`license` 是技能自己声明的值，所以 demo 语料里仍可能出现非 GREEN 的 `license` 字符串。
用 `--sources-config your.yaml` 指定自己的源注册表，或用 `--source <name>` 只跑单个源。

```bash
pip install -e ".[dev]"
python -m pytest skillcorpus/tests -p no:cacheprovider --import-mode=importlib
```

## 🗺️ 路线图

<!-- TODO(@team)：这是按已知缺口列的初版，请按实际计划修改。 -->

- [x] 策展管线：16 类体系、三维质量、逐源许可审计
- [x] 微调检索栈 + 三个 benchmark 的评估
- [ ] 公开的 SkillHub 端点
- [ ] 语料、检索模型与重排模型上 HuggingFace
- [ ] 两个检索模型的部署脚本（自建 `match/`）
- [ ] Hermes 集成

## 引用

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

## 许可

- **代码** —— Apache-2.0（`match/` 和 `evaluate/` 两个工具包各自为 MIT，见其目录下的 `LICENSE`）。
- **语料** —— 每个技能保留其**上游原始许可**；只收录 GREEN（MIT / Apache-2.0 / BSD / ISC / …）
  许可的技能，不做任何重新授权。每行都带 `source`、`source_url`、`license`，下游使用须遵循
  各技能自身的条款。

完整的 GREEN/RED/YELLOW 策略、许可数据流与 opt-out 通道见
[`docs/licence-and-governance.md`](docs/licence-and-governance.md)。
