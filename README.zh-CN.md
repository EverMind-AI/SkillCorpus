<!-- 所有发布链接均已上线：SkillHub、代码仓库、两个检索模型，以及
     1k demo 语料。完整的 96,401 条语料尚未发布（见「可用内容」与路线图）。 -->

<div align="center">

# SkillCorpus

**几秒钟为你的 agent 找到可复用技能——阅读、下载，或直接投入使用。**

搜索公开的 [SkillHub](https://evermind.ai/skillhub)，试用 [1,000 条技能 demo 语料](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k)，运行[检索模型](https://huggingface.co/EverMind-AI/models)，或阅读[论文](https://arxiv.org/abs/2607.15557)。

EverMind agent 技术栈的一环 —— [Raven](https://github.com/EverMind-AI/raven)，终端原生的 agent harness · [EverOS](https://github.com/EverMind-AI/EverOS)，其构建所依赖的记忆底座 · SkillCorpus，它们从中检索的社区技能语料。

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![SkillHub](https://img.shields.io/badge/SkillHub-live-2ea44f.svg)](https://evermind.ai/skillhub)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k)
[![Models](https://img.shields.io/badge/%F0%9F%A4%97-Retriever%20%2B%20Reranker-yellow.svg)](https://huggingface.co/EverMind-AI/models)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#许可)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

[English](README.md) | **简体中文**

</div>

## 三个部分，一套系统

它们是**三个入口，而不是三个必须完成的部署步骤**。根据你的目标选择即可：现在使用技能、构建开源技术栈，或加入自托管精排。

| 部分 | 它做什么 | 它不做什么 | 什么时候从这里开始 |
|---|---|---|---|
| [**Hugging Face re-ranker**](https://huggingface.co/EverMind-AI/skillcorpus-reranker-0.6b) | 用于技能检索的可下载精排组件。向量检索先召回一批候选技能；它将任务与每个候选技能文档（名称、描述和正文）成对读取，为每一对返回相关性分数，再由你的应用按分数重排候选。 | 它不负责直接搜索整个语料库、选择或执行技能，也不验证安全性、质量或许可证。分数只适合同一候选列表内的排序，不能跨任务比较，也不应套用统一阈值。 | 你已经有候选技能，希望在自己的环境中进行更精准的排序。 |
| [**SkillCorpus GitHub**](https://github.com/EverMind-AI/SkillCorpus) | 开源的技能语料构建与检索工具：聚合、筛选治理、去重、分类和导出技能集合；训练检索模型并提供基础的 `/embed` 与 `/score` 端点；查看或改造评测与集成代码。 | 它不是托管技能目录。克隆仓库不会获得论文完整快照、模型权重或复现论文结果所需的全部产物；仓库提供的推理端点也不是本地版 SkillHub。 | 你想理解或扩展系统、构建自己的语料、让已有评测组件适配你的输入，或自托管部分检索链路。 |
| [**SkillHub**](https://evermind.ai/skillhub) | 面向客户的托管产品：浏览和搜索在线技能目录，查看元数据与来源，阅读 `SKILL.md`，以及下载技能包、交给兼容的 agent 或 harness 安装。无需克隆仓库或自行运行模型。 | 它不是源代码仓库或模型检查点，也不会代替你的 agent 执行最终任务。 | 你现在就想找到并使用技能。 |

```text
SkillCorpus GitHub —— 开源管线与参考代码
  ├─ 语料工作支撑 ───────→ SkillHub —— 托管目录 + UI/API
  └─ 检索工作发布 ───────→ Hugging Face reranker —— 可下载的候选精排模型
```

上图表达的是项目来源与产品分工，并不声称 SkillHub 网站的每次搜索都会调用公开的 Hugging Face 检查点。重排序模型（reranker）是 **SkillCorpus 项目公开发布的一项匹配组件**；SkillHub 是供客户发现和获取技能的**托管目录与分发入口**，使用它不需要自行运行 SkillCorpus 数据管线或模型。

## 30 秒试用：无需安装或 API key

向托管 SkillHub 查询与任务对应的技能：

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills?q=extract+tables+from+a+PDF"
```

或者使用只依赖标准库的 demo，搜索并阅读匹配技能的 `SKILL.md` 正文：

```bash
python3 examples/skillhub_demo.py "extract tables from a scanned PDF invoice"
```

响应先返回元数据，随后可获取 `skill_md` 并注入 agent prompt。完整的三层流程——搜索、阅读和可选下载——见 [`examples/skillhub_demo.py`](examples/skillhub_demo.py)；API 契约见 [`docs/integrations.md`](docs/integrations.md)。

> **运行下载的技能之前：**随附的脚本、资源和参考资料均为第三方内容。执行任何内容前，请审阅它们及其上游条款。

## 能用它做什么

| 你的角色 | SkillCorpus 能帮你 | 从这里开始 |
|---|---|---|
| 构建 agent | 无需手工维护技能目录，即可找到相关的流程指令 | [搜索 SkillHub](https://evermind.ai/skillhub) |
| 自动化任务 | 获取 `SKILL.md`、加入 prompt，或为兼容 harness 下载其技能包 | [运行 demo](examples/skillhub_demo.py) |
| 评估检索 | 使用已发布的嵌入模型和 reranker 测试自己的候选集合 | [获取模型](https://huggingface.co/EverMind-AI/models) |
| 构建专业技能库 | 聚合、许可审计、策展、去重并导出自己的来源 | [构建自己的语料](#build-your-own-corpus) |

## 当前可用内容

| 产物 | 当前可用 | 是什么 | 边界 |
|---|---|---|---|
| 🌐 **SkillHub** | [托管服务](https://evermind.ai/skillhub) | 面向语料的公开检索；搜索和阅读无需安装 | 托管 API，不是可自托管的软件包 |
| 📚 **语料** | Hugging Face 上的 [1K demo](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k) | `skills.parquet`、`attachments.tar.zst` 和数据集卡片 | 公开下载的是 1,000 条技能 demo，而非论文完整快照 |
| 🔡 **SkillRouter** | [Bi-encoder](https://huggingface.co/EverMind-AI/skillcorpus-embedding-0.6b) + [reranker](https://huggingface.co/EverMind-AI/skillcorpus-reranker-0.6b) | 基于 `Qwen3-Embedding-0.6B` 与 `Qwen3-Reranker-0.6B` 微调的检索检查点 | 语料、候选检索流程和服务环境需由你提供 |
| 🛠️ **代码** | [本仓库](https://github.com/EverMind-AI/SkillCorpus) | `aggregate`、`curate`、`match`、`evaluate` 与 `export` 工具包 | 代码不含本地 SkillHub 兼容的语料索引或路由器 |

论文的生产快照包含约 821,000 个爬取文件中选出的 **96,401 条技能**。该完整快照尚不能下载；目前公开的语料是上述 1K demo。

## 为什么需要 SkillCorpus

Agent 技能——把可复用流程知识封装为 `SKILL.md` 的文件——分散在公开仓库中，可能重复、质量不一，也未必清楚能否再分发。SkillCorpus 通过四个阶段把这些碎片化内容变成可检索的语料：

- **`aggregate`** —— 发现并克隆公开的 `SKILL.md` 仓库。
- **`curate`** —— 解析，经安全与许可门禁，去重，归为 16 类，并评估三项质量维度。
- **`match`** —— SkillRouter 将微调 bi-encoder、reranker 和 LLM selector 结合，为任务选择技能。
- **`evaluate`** —— 在三个 agent benchmark、两个 harness，以及开源和前沿 backbone 上测试技能使用效果。

质量和安全维度的评分用于语料策展与筛选；它们不保证某项技能、其依赖项或随附文件对你的环境安全。

<div align="center">
<img src="docs/assets/pipeline.png" alt="SkillCorpus：构建语料（aggregate 加 curate）并使用它（match 加 evaluate）" width="100%">
</div>

## 与你的 agent 一起使用

### SkillHub

[SkillHub](https://evermind.ai/skillhub) 以三个层级提供语料：发现元数据、阅读 `skill_md`，以及下载含 `scripts/` 的 zip。大多数技能仅含指令，因此通常阅读层就已足够。

```bash
# 搜索并读取正文——仅用标准库，无需安装或 API key
python3 examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

# 将首个命中的随附文件下载到本地技能目录
python3 examples/skillhub_demo.py --install ./skills "convert a PDF to images"

# 检索后通过 OpenAI 兼容 LLM 执行任务
export OPENAI_API_KEY=...
python3 examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"
```

使用 OpenRouter 或本地 vLLM 时，还需设置 `OPENAI_BASE_URL`；详见 demo 的内联帮助。端点、响应封装、状态码和速率限制见 [`docs/integrations.md`](docs/integrations.md)。

### Raven 与其他 harness

<details>
<summary><b>Raven</b> —— 官方 SkillHub 来源</summary>

Raven 通过加权 RRF（`skillForge.router`）将 SkillHub 与本地和 EverOS 技能来源结合：

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
      min_safety: 0.7        # 按策展安全维度筛选；并非安全保证
      source: raven          # 用于安装统计的下载标签
```
</details>

<details>
<summary><b>其他 harness</b> —— OpenClaw、Hermes、Claude Code，…</summary>

目前还没有官方插件。任何可读取技能目录的 harness 都能使用下载层：

```bash
python3 examples/skillhub_demo.py --install ~/.claude/skills "convert a PDF to images"
#                                          ~/.hermes/skills      (Hermes)
#                                ~/.openclaw/workspace/skills    (OpenClaw)
```

对于通过 prompt 注入的 harness，从阅读层获取 `skill_md` 并置于系统 prompt 前即可，demo 中的 `build_prompt()` 正是这样做的。
</details>

## 自行运行检索模型

如果希望技能选择完全在自己的环境中进行，可使用已发布的检索检查点。从公开的 1K demo 语料开始（完整的 96,401 条语料尚未发布）：

选择一种加载路径，并单独安装其依赖（这些依赖不包含在检索服务器依赖中）：

```bash
pip install datasets
# 或用于直接读取 Parquet 的路径：
pip install pandas pyarrow
```

```python
from datasets import load_dataset
skills = load_dataset("EverMind-AI/skillcorpus-demo-1k", split="train")

# 或直接读取下载的 parquet。
import pandas as pd
skills = pd.read_parquet("skills.parquet")
```

附件（`scripts/`、`references/`）位于同级的 `attachments.tar.zst` 中。通过提供的端点部署两个检查点：

```bash
pip install -r skillcorpus/match/requirements.txt
EMBEDDING_MODEL=EverMind-AI/skillcorpus-embedding-0.6b \
RERANKER_MODEL=EverMind-AI/skillcorpus-reranker-0.6b \
  bash skillcorpus/match/scripts/run_server.sh
```

自托管服务器提供 **`/embed`** 和 **`/score`**（[Serving](skillcorpus/match/README.md#serving)）。它**不会**提供本地 SkillHub 兼容的 `/openapi/v1/skills` 语料索引或端到端路由器：请在你的应用中加载语料、组合候选检索、top-k 选择和重排序。同一嵌入端点也可通过 `embedding.provider: skillrouter_remote` 支持语料生产管线去重。

## 论文结果

以下是[论文 Table 1](https://arxiv.org/abs/2607.15557)报告的结果，而非对全新克隆、公开 1K demo 或不同 agent 配置所能获得结果的承诺。

| Harness × backbone | SkillsBench | GDPVal | QwenClawBench |
|---|---:|---:|---:|
| OpenClaw × Qwen3.5-27B | 8.8 → **13.0** | 81.2 → **83.1** | 65.2 → **66.7** |
| OpenClaw × Qwen3.5-397B | 11.1 → **16.9** | 82.2 → **84.0** | 65.7 → **67.0** |
| Raven × Qwen3.5-27B | 10.0 → **16.5** | 82.6 → **83.8** | 66.9 → **70.8** |
| Raven × Qwen3.5-397B | 9.2 → **22.6** | 84.0 → **85.2** | 68.8 → **73.2** |
| **合并 ∆** | **+7.5**±2.3 (z=3.2) | **+1.51**±0.49 (z=3.1) | **+2.79**±0.70 (z=4.0) |

指标定义：SkillsBench 报告 pass@1，GDPVal 报告 LLM 裁判奖励，QwenClawBench 报告其混合分数；所有数值均以 ×100 显示。

报告中，SkillsBench 的提升最大，因为其任务需要模型可能不具备的流程知识；更开放的 GDPVal 任务提升最小。

<a id="build-your-own-corpus"></a>

## 构建自己的语料

此路径用于策展**你自己的**来源。分类和质量评分需要 LLM 端点，去重需要嵌入端点；见 [`docs/running.md`](docs/running.md)。

```bash
git clone https://github.com/EverMind-AI/SkillCorpus.git skillcorpus && cd skillcorpus
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip && pip install -e .

python -m skillcorpus.cli build     # 4 个 demo 来源 -> curate -> export
python -m skillcorpus.cli stats     # 按来源 / 类别 / 许可证统计
python -m skillcorpus.cli export --out ./corpus
```

`cli build` 运行 `ingest → quality_pass → dedup_pass → license_audit → export.corpus`。当没有可连接的模型端点时，语料生产管线的 LLM 分类和质量评分会回退为规则，因此管线仍可端到端运行。`match/` 和 `evaluate/` 是独立工具包，各自有自己的依赖，且不会随 producer 包一起安装。

仅来自 GREEN 许可证**来源**的技能会被导出。demo 完全信任 `audit/license_safe_sources.json` 中的白名单；生产环境按来源仓库 SPDX 进行门禁。每行仍保留声明的 `license`，因此 demo 语料仍可能包含非 GREEN 的许可证字符串。可用 `--sources-config your.yaml` 指定自己的注册表，或用 `--source <name>` 选择单一来源。

## 信任、溯源与可用性

- 公开语料是 [1K demo](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k)；**96,401** 是论文的生产快照，目前不是可下载语料。
- 每个已发布语料行都保留 `source`、`source_url` 和原始上游 `license`。你有责任遵守每项技能的条款，并在使用前审阅第三方脚本、资源、参考资料、依赖项和指令。
- 策展质量和安全评分可用于排序或过滤材料，但不构成安全性、兼容性或信息安全保证。
- 托管 SkillHub 及其可用性可能与自托管检索部署不同。自托管服务器只提供 `/embed` 和 `/score`，不是 SkillHub 语料 API 的本地替代品。

GREEN/RED/YELLOW 策略、许可证数据流和 opt-out 流程见 [`docs/licence-and-governance.md`](docs/licence-and-governance.md)。

## 动态

- **2026-08-12** —— 检索模型（bi-encoder + reranker）和 1,000 条技能 demo 语料已发布至 [Hugging Face](https://huggingface.co/EverMind-AI)。
- **2026-08-06** —— 论文 v5 已发布至 [arXiv](https://arxiv.org/abs/2607.15557)。

## 路线图

- [x] 策展管线：16 类分类法、三项质量维度、按来源进行许可证审计
- [x] 微调检索栈 + 三个 benchmark 评估
- [x] 公开 SkillHub 端点
- [x] Hugging Face 上的检索模型（bi-encoder + reranker）和 1K demo 语料
- [ ] Hugging Face 上完整的 96,401 条技能语料
- [x] 两个检索模型的部署脚本（`match/`）
- [ ] Hermes 集成

## 贡献

欢迎为语料管线、检索工具、评估、集成和文档贡献力量。提交 pull request 前，请先查看仓库 issue 和各组件的文档。

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

- **代码** —— Apache-2.0（`match/` 和 `evaluate/` 工具包均为 MIT——见其各自的 `LICENSE`）。
- **语料** —— 导出仅允许来自 GREEN 许可证来源仓库的技能。每行保留其声明的上游 `license`；它可能与来源仓库的许可证不同，仍需单独审阅。语料不会被重新授权，下游使用必须遵守逐技能条款。

## EverMind 生态系统

EverMind 是一个面向长期记忆、自我演进 agent、AI 原生界面和记忆评估的开源生态系统。

<table>
<tr>
<th colspan="2">EverMind 开源生态系统</th>
</tr>
<tr>
<td><strong>记忆运行时</strong></td>
<td><a href="https://github.com/EverMind-AI/EverOS">EverOS</a> - 面向 agent 与用户记忆的本地记忆操作系统与研究支撑运行时。</td>
</tr>
<tr>
<td><strong>自我改进 Agent Harness</strong></td>
<td><a href="https://github.com/EverMind-AI/Raven">Raven</a> - 将记忆、主动性、上下文控制和技能演进带入终端原生 agent 的自我改进 harness。</td>
</tr>
<tr>
<td><strong>Agent 技能与检索</strong></td>
<td><a href="https://github.com/EverMind-AI/SkillCorpus">SkillCorpus</a> - 开放策展管线、SkillRouter 检索模型、公开 <a href="https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k">1K demo 语料</a>、<a href="https://evermind.ai/skillhub">SkillHub</a> 集成、agent 集成与基准测试。</td>
</tr>
<tr>
<td><strong>算法引擎</strong></td>
<td><a href="https://github.com/EverMind-AI/EverAlgo">EverAlgo</a> - 为 EverOS 提供支持的无状态提取、排序、解析和记忆算子。</td>
</tr>
<tr>
<td><strong>超图记忆</strong></td>
<td><a href="https://github.com/EverMind-AI/HyperMem">HyperMem</a> - 面向长期对话的超图记忆，具有经基准支撑的主题 -&gt; 事件 -&gt; 事实检索方法。</td>
</tr>
<tr>
<td><strong>基准测试</strong></td>
<td><a href="https://github.com/EverMind-AI/EverMemBench">EverMemBench</a> · <a href="https://github.com/EverMind-AI/EvoAgentBench">EvoAgentBench</a> - 对话记忆和 agent 自我演进的评估套件。</td>
</tr>
<tr>
<td><strong>长上下文研究</strong></td>
<td><a href="https://github.com/EverMind-AI/MSA">MSA</a> - 用于可扩展潜在记忆和 100M token 上下文的 Memory Sparse Attention。</td>
</tr>
<tr>
<td><strong>个人记忆层</strong></td>
<td><a href="https://github.com/EverMind-AI/EverMe">EverMe</a> - 面向跨设备、跨 agent 个人记忆的 CLI 与 agent 插件套件。</td>
</tr>
<tr>
<td><strong>开发者集成</strong></td>
<td><a href="https://github.com/EverMind-AI/evermem-claude-code">evermem-claude-code</a> · <a href="https://github.com/EverMind-AI/everos-plugins">everos-plugins</a> - 面向 AI 编程 agent 的插件、技能与迁移工具。</td>
</tr>
</table>

这些仓库共同构成 EverMind 从研究到运行时的技术栈：新的记忆方法、可复用算法、基准证据与实用的 agent 集成。
