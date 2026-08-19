<!-- 所有发布链接均已上线：SkillHub、代码仓库、两个检索模型，以及 1k demo 语料。
     完整的 114,190 条语料尚未发布（见 Corpus 行与路线图）。 -->

<div align="center">

[English](README.md) | **简体中文**

# SkillCorpus

**一句话装进你的 agent，此后每一轮都自动带上对的技能——从 114,190 条经审核、许可宽松的技能里检索。不需要工具调用，也不用记任何技能名字。**

EverMind agent 技术栈的一部分：[Raven](https://github.com/EverMind-AI/raven) 是终端原生的
agent harness，[EverOS](https://github.com/EverMind-AI/EverOS) 是它所依赖的记忆底座，而
SkillCorpus 则是二者检索的社区技能语料。

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![SkillHub](https://img.shields.io/badge/SkillHub-live-2ea44f.svg)](https://evermind.ai/skillhub)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k)
[![Models](https://img.shields.io/badge/%F0%9F%A4%97-Retriever%20%2B%20Reranker-yellow.svg)](https://huggingface.co/EverMind-AI/models)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#许可)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

<img src="docs/assets/pipeline.png" alt="SkillCorpus：构建语料（aggregate + curate）与使用语料（match + evaluate）" width="100%">

</div>

## 安装——把这句话粘给你的 agent

> 根据 https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/INSTALL.agent.md 安装 SkillCorpus Plugins。

每个受支持的宿主本身就是 agent，最快的装法是让它自己装。它执行的剧本会先判断宿主、
改任何配置前先备份并给出 diff、装完验证 `# Skills` 块确实出现——想逐步审计的话可以
[先读一遍](skillcorpus_plugin/INSTALL.agent.md)。

| 宿主 | 接入缝 | 状态 |
|---|---|---|
| **WorkBuddy**（5.3.13） | `UserPromptSubmit` 钩子 | ✅ 插件 |
| **OpenClaw**（≥ 2026.3.8） | `before_prompt_build` 钩子 | ✅ 插件 |
| **Hermes** | memory provider 的 `prefetch` | ✅ 插件 |
| **DeepSeek Harness** | `agent/pre-step` waterfall | ✅ 插件 |
| **Raven** | 原生 SkillHub 源（`skillForge.router.hub`） | ✅ 内置——插件版等上游插槽 |
| **其他任何宿主** | 旁边跑 HTTP adapter，`POST /retrieve` | ✅ [engine-python](https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/engine-python/README.md) |

以上都不需要改宿主代码。详见 [`skillcorpus_plugin/`](skillcorpus_plugin)。

## 📰 动态

- **2026-08-19** —— **SkillCorpus Plugins** 发布：在 WorkBuddy、Hermes、OpenClaw、DeepSeek Harness 里逐轮检索技能，一句话安装，不用改宿主代码。见 [`skillcorpus_plugin/`](skillcorpus_plugin)。
- **2026-08-12** —— 检索模型（bi-encoder + reranker）与 1,000 条 demo 语料上 [🤗 HuggingFace](https://huggingface.co/EverMind-AI)。
- **2026-08-06** —— 论文 v5 上 [arXiv](https://arxiv.org/abs/2607.15557)。

## SkillCorpus 是什么

Agent 技能（即封装了可复用过程性知识的 `SKILL.md` 文件）散落在成千上万个公开
仓库中，彼此重复、良莠不齐，再分发权也不明确。SkillCorpus 把这个庞杂的池子整理成 agent 能直接取用的语料，分四个阶段：

- **`aggregate`** —— 从公开的 `SKILL.md` 仓库发现并克隆技能。
- **`curate`** —— 解析 · 安全 · 许可门禁 · 去重 · 16 类分类 · 三维质量打分。
- **`match`** —— 微调的 bi-encoder + reranker + LLM selector，为任务挑选技能。
- **`evaluate`** —— 三个真实 agent benchmark、两个 harness、开源与前沿 backbone。

从约 821,000 个爬取文件中，最终留下 96,401 个技能。每个都保留其上游原始许可，每个源仓库也都
通过了许可审计，因此这套发布的语料可商用、可再分发。

## 📦 我们发布了什么

| | 产物 | 内容 | 链接 |
|---|---|---|---|
| 🌐 | **SkillHub** | 114,190 条技能 + 那两个模型的托管 API，无需安装 | [evermind.ai/skillhub](https://evermind.ai/skillhub) |
| 📚 | **语料** *(demo)* | 1,000 条样本 —— `skills.parquet` + `attachments.tar.zst` + dataset card；完整的 114,190 条语料后续发布 | [🤗 demo-1k](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k) |
| 🔡 | **检索模型** | 从 `Qwen3-Embedding-0.6B` 和 `Qwen3-Reranker-0.6B` 微调出的 bi-encoder 与 reranker | [🤗 bi-encoder](https://huggingface.co/EverMind-AI/skillcorpus-embedding-0.6b) · [reranker](https://huggingface.co/EverMind-AI/skillcorpus-reranker-0.6b) |
| 🛠️ | **代码** | 本仓库 —— 构建语料、训练那两个模型的流水线（`aggregate` · `curate` · `match` · `evaluate` · `export`） | [GitHub](https://github.com/EverMind-AI/SkillCorpus) |
| 🔌 | **插件** | 在 WorkBuddy · Hermes · OpenClaw · DeepSeek Harness 里逐轮检索本语料，其他宿主走 HTTP adapter | [`skillcorpus_plugin/`](skillcorpus_plugin) |

*开源：代码、语料、模型；不开源：仅托管的 SkillHub 服务。*

<div align="center">
<img src="docs/assets/taxonomy.png" alt="96,401 个有效技能的 16 类分布" width="58%">
</div>

论文所评测的那一版 96,401 条，按 16 类体系和三个质量维度（utility / robustness / safety）组织，并带 1024 维
检索向量。字段约定见 [`docs/corpus-schema.md`](docs/corpus-schema.md)。

## 📊 效果

同一 harness、同一 backbone，通过率从「无技能」到「接入 SkillCorpus」的变化
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

## 🚀 其余用法

上面的插件覆盖了大多数人。如果你想自己调服务，或者把整套跑在自己的机器上：

### 直接调 API

[SkillHub](https://evermind.ai/skillhub) 把语料分三级提供：发现（元数据）、读正文
（`skill_md`）、下载（含 `scripts/` 的 zip）。大多数技能是纯指令，读正文这一级通常已足够。

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills?q=extract+tables+from+a+PDF"
```

从结果中取一个 `id`，获取它的 `skill_md`，注入 agent 的 prompt。
[`examples/skillhub_demo.py`](examples/skillhub_demo.py) 把三级都跑通了：

```bash
# 检索 + 读正文 —— 纯标准库，不用安装，不用 API key
python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

# 顺带把命中的第一个技能的脚本包拉下来
python examples/skillhub_demo.py --install ./skills "convert a PDF to images"

# 检索并把正文注入 prompt 后真正执行任务 —— 任意 OpenAI 兼容 LLM(OpenAI / OpenRouter / 本地 vLLM…）
export OPENAI_API_KEY=...                                # 用 OpenRouter / vLLM 时再设
# export OPENAI_BASE_URL=https://openrouter.ai/api/v1   # OPENAI_BASE_URL + --model openai/gpt-4o-mini
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

### 自建模型服务

若不想依赖托管端点，可自己跑 selection。语料和两个检索模型都已发布：加载数据、部署两个
模型，自己做 encode → top-k → rerank。

```python
# 数据 —— 目前是 1,000 条 demo；完整的 114,190 条语料后续发布
from datasets import load_dataset
skills = load_dataset("EverMind-AI/skillcorpus-demo-1k", split="train")   # 1,000 条 demo 技能
# 或者用 pandas 直接读文件（不依赖 datasets）：  pip install pandas
import pandas as pd; skills = pd.read_parquet("skills.parquet")
```

附件（`scripts/`、`references/`）以同级的 `attachments.tar.zst` 形式发布。

```bash
# 先装 serving 依赖（torch、transformers 等），再把两个环境变量指向已发布的检查点
# （脚本自带的默认值是训练产物，全新克隆里并不存在），然后把两个模型部署在同一个
# 端点后面  ->  /embed + /score
pip install -r skillcorpus/match/requirements.txt
EMBEDDING_MODEL=<embedding 检查点目录> RERANKER_MODEL=<reranker 检查点目录> \
  bash skillcorpus/match/scripts/run_server.sh
```

这个端点只提供模型的 `/embed` + `/score`
（见 [`skillcorpus/match/` → Serving](skillcorpus/match/README.md#serving)），并不是 SkillHub
那个仅托管的 `/openapi/v1/skills` API 的替代品。因此：

- `examples/skillhub_demo.py` 和 C 节的集成只对接**托管的** SkillHub；自部署时，你直接在 `/embed` + `/score` 之上运行自己的 selection。
- 它同时也是 producer 去重所用的 embedding 端点，把 `embedding.provider` 配成 `skillrouter_remote`，即可用它来[构建自己的语料](#build-your-own)。

想策展**自己的**源，见 [构建自己的语料](#build-your-own)。

## 🧩 工作原理

```
skillcorpus/
├── core/       数据模型 · SQLite/faiss 存储 · LLM 与 embedding 客户端
├── aggregate/  源注册表 + 多仓库克隆
├── curate/     解析 · 安全 · 许可 · 分类 · 质量 · 去重 + 全库扫描
├── export/     语料写出（parquet + 附件 + dataset card）
├── match/      发布的两个模型 + 训练配方                          ← 依赖独立
├── evaluate/   skillsbench · qwenclawbench · gdpval 评测           ← 依赖独立
└── cli.py      build · stats · export
```

`cli build` 会执行完整的策展链路（`ingest → quality_pass → dedup_pass → license_audit →
export.corpus`）。当没有可用的模型端点时，LLM 分类与质量打分会自动降级为规则实现，因此
整条管线始终能端到端跑通。

`match/` 和 `evaluate/` 是独立的工具包，各自带有 `requirements.txt`（torch / transformers，
按 benchmark 区分）；安装主包时**不会**被一并拉入。

- **检索** —— [`skillcorpus/match/`](skillcorpus/match) 就是**发布的那两个模型**：
  从 `Qwen3-Embedding-0.6B` 微调的 bi-encoder 负责候选召回，从 `Qwen3-Reranker-0.6B` 微调的
  reranker 负责对候选打分重排。SkillHub 已托管这两个模型；如需自行部署，见
  [Serving](skillcorpus/match/README.md#serving)（`serve.py` + `run_server.sh`）。该目录还包含训练配方（合成 query → InfoNCE → listwise CE）和
  `eval_compare.py`（检索指标 nDCG / MRR / Hit / Recall）。
- **评测** —— [`skillcorpus/evaluate/`](skillcorpus/evaluate)：`skillsbench`、`qwenclawbench`、
  `gdpval`，各自独立，带自己的 README 和依赖。

<a name="build-your-own"></a>

## 🛠️ 构建自己的语料

仅当你想策展**自己的**源时才需要这一节。它需要一个 LLM 端点做分类与质量打分，以及一个
embedding 端点做去重，详见 [`docs/running.md`](docs/running.md)。

```bash
git clone https://github.com/EverMind-AI/SkillCorpus.git skillcorpus && cd skillcorpus
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip && pip install -e .

python -m skillcorpus.cli build     # 4 个 demo 源 -> 策展 -> 导出
python -m skillcorpus.cli stats     # 按 source / category / license 统计
python -m skillcorpus.cli export --out ./corpus
```

只有来自 GREEN 许可**源仓库**的技能才会被导出（demo 直接信任
`audit/license_safe_sources.json` 里的白名单；生产环境按源仓库 SPDX 逐个把关）。每行的
`license` 是技能自己声明的值，因此 demo 语料里仍可能出现非 GREEN 的 `license` 字符串。
用 `--sources-config your.yaml` 指定自己的源注册表，或用 `--source <name>` 只构建单个源。

```bash
pip install -e ".[dev]"
python -m pytest skillcorpus/tests -p no:cacheprovider --import-mode=importlib
```

## 🗺️ 路线图

<!-- TODO(@team)：这是按已知缺口列的初版，请按实际计划修改。 -->

- [x] 策展管线：16 类体系、三维质量、逐源许可审计
- [x] 微调检索栈 + 三个 benchmark 的评估
- [x] 公开的 SkillHub 端点
- [x] 检索模型（bi-encoder + reranker）与 1k demo 语料上 HuggingFace
- [ ] 完整的 114,190 条语料上 HuggingFace
- [x] 两个检索模型的部署脚本（自建 `match/`）
- [ ] 把 skill 库 + 检索框架打包成插件，供任意 agent harness 使用
- [ ] Raven 插件——等上游 `context_segments` 插槽

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
