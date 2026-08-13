<!-- 所有发布链接均已上线：SkillHub、代码仓库、两个检索模型，以及
     1k demo 语料。完整的 96,401 条语料尚未发布（见「可用内容」与路线图）。 -->

<div align="center">

# SkillCorpus

**整理分散的 Agent 技能，按任务检索相关技能。**

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![SkillHub](https://img.shields.io/badge/SkillHub-live-2ea44f.svg)](https://evermind.ai/skillhub)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k)
[![Models](https://img.shields.io/badge/%F0%9F%A4%97-Retriever%20%2B%20Reranker-yellow.svg)](https://huggingface.co/EverMind-AI/models)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#许可)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

[English](README.md) | **简体中文**

</div>

## 从开源底座到线上产品

| 入口 | 提供什么 | 为开发者与爱好者带来的价值 |
|---|---|---|
| [**SkillCorpus GitHub Repo**](https://github.com/EverMind-AI/SkillCorpus) | 语料聚合、治理、去重、分类和导出的开源代码，以及检索、评测与集成工具。 | 可以从头到尾学习整套系统，也可以 fork 语料管线与模型服务组件，用自己的数据源和策略构建技能库与检索流程。各组件以其声明的许可证为准；第三方技能和数据保留上游条款。 |
| [**Hugging Face 模型与 demo**](https://huggingface.co/EverMind-AI) | [1K 示例语料](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k)、生成召回向量的[嵌入模型](https://huggingface.co/EverMind-AI/skillcorpus-embedding-0.6b)，以及为任务和候选技能评分的[重排序模型（reranker）](https://huggingface.co/EverMind-AI/skillcorpus-reranker-0.6b)。 | 无需从头训练初始模型，即可做原型验证、本地评测或自托管检索。语料索引、候选选择流程和编排仍需由你提供。 |
| [**SkillHub**](https://evermind.ai/skillhub) | 这套技术栈的线上落地产品：基于经过筛选和治理的技能库，提供托管目录和 API。 | 无需克隆仓库或自行运行模型，即可立即体验这套系统，搜索技能、查看来源、阅读 `SKILL.md`，并为兼容的 Agent 下载技能包。 |

> **可用性说明：**目前公开可下载的语料是 1K demo。**96,401 条技能**指论文使用的生产快照，该完整版本尚未公开下载。

## 30 秒体验 SkillHub

无需安装或 API Key。

直接向 SkillHub 查询适合当前任务的技能：

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills?q=extract+tables+from+a+PDF"
```

也可以[在浏览器中打开同一个实时查询](https://skillhub.evermind.ai/openapi/v1/skills?q=extract%20tables%20from%20a%20PDF)。下面是精简后的返回示例：

```yaml
name: extract-tables-from-pdf
category: DOC-PROC
source: mzlzyCA/html-markdown
source_url: https://github.com/mzlzyCA/html-markdown
license: MIT
quality_score: 0.708
```

随着目录更新，实时排序与元数据可能发生变化。

或者运行仅依赖 Python 标准库的示例，搜索并阅读匹配技能的 `SKILL.md` 正文：

```bash
python3 examples/skillhub_demo.py "extract tables from a scanned PDF invoice"
```

响应先返回元数据，随后可获取 `skill_md` 并将其加入 Agent 提示词。完整的三层流程（搜索、阅读和可选下载）见 [`examples/skillhub_demo.py`](examples/skillhub_demo.py)；API 契约见 [`docs/integrations.md`](docs/integrations.md)。

> **运行下载的技能之前：**随附的脚本、资源和参考资料均为第三方内容。使用这些内容或执行其中脚本前，请审阅它们及其上游条款。

## 为什么需要 SkillCorpus

Agent 技能以 `SKILL.md` 文件的形式封装可复用的流程知识。它们分散在公开仓库中，可能重复、质量不一，也未必清楚能否再分发。SkillCorpus 通过四个阶段把这些碎片化内容变成面向检索的语料：

- **`aggregate`：**发现并克隆公开的 `SKILL.md` 仓库。
- **`curate`：**解析内容，应用基于文本的安全信号和来源级许可证策略，去重，归为 16 类，并评估三项质量维度。
- **`match`：**使用微调后的 bi-encoder 召回候选技能，再按任务相关性重新排序。
- **`evaluate`：**在三项 Agent 基准、两个 Agent 框架，以及开源和前沿底座模型上测试技能效果。

质量和安全维度的评分用于语料整理与筛选；它们不保证某项技能、其依赖项或随附文件对你的环境安全。

<div align="center">
<img src="docs/assets/pipeline.png" alt="SkillCorpus：构建语料（aggregate 加 curate）并使用它（match 加 evaluate）" width="100%">
</div>

## 构想：从 GitHub Issue 到 Agent 任务交接包

**粘贴一个 GitHub Issue，为你的 Agent 准备一份可审阅的任务交接包。**

> **该功能尚未发布。**SkillHub 目前已经支持单项技能的搜索、读取和下载；仓库分析与多技能任务交接包仍未上线。

任务交接包会把 Issue 与仓库快照整理成一个可审阅、可分享的产物：

```text
GitHub Issue + 仓库快照
             -> 任务边界、文件引用、仓库检查命令、候选技能
             -> 以 Markdown 审阅、交给 Agent，或贴回 Issue/PR
```

维护者可以在代码修改前确认任务边界，贡献者可以带着所需上下文和检查要求着手处理不熟悉的 Issue，团队则能让所有协作者持续看到这份交接内容。

候选技能会附带匹配理由、来源和声明的许可证，供用户审阅；它们仍然只是建议，不构成正确性、安全性或许可证合规保证。

## 与你的 agent 一起使用

### SkillHub

[SkillHub](https://evermind.ai/skillhub) 以三个层级提供语料：发现元数据、阅读 `skill_md`，以及下载含 `scripts/` 的 zip。大多数技能仅含指令，因此通常阅读层就已足够。

```bash
# 搜索并读取正文；仅用标准库，无需安装或 API key
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
<summary><b>Raven</b>：官方 SkillHub 来源</summary>

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
<summary><b>其他 harness</b>：OpenClaw、Hermes、Claude Code，…</summary>

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

- **2026-08-12：**检索模型（bi-encoder + reranker）和 1,000 条技能 demo 语料已发布至 [Hugging Face](https://huggingface.co/EverMind-AI)。
- **2026-08-06：**论文 v5 已发布至 [arXiv](https://arxiv.org/abs/2607.15557)。

## 路线图

- [x] 策展管线：16 类分类法、三项质量维度、按来源进行许可证审计
- [x] 微调检索栈 + 三个 benchmark 评估
- [x] 公开 SkillHub 端点
- [x] Hugging Face 上的检索模型（bi-encoder + reranker）和 1K demo 语料
- [ ] 从 GitHub Issue 生成带来源引用的 Agent 任务交接包与审阅清单
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

- **代码：**Apache-2.0。`match/` 和 `evaluate/` 工具包均为 MIT；见其各自的 `LICENSE`。
- **语料：**导出仅允许来自 GREEN 许可证来源仓库的技能。每行保留其声明的上游 `license`；它可能与来源仓库的许可证不同，仍需单独审阅。语料不会被重新授权，下游使用必须遵守逐技能条款。

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
