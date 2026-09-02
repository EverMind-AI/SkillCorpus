<!-- SkillHub 是在线产品；本仓库包含它背后的开源语料、检索、评估、导出和插件层。 -->

<div align="center" id="readme-top">

<table width="100%" border="1" bordercolor="#d9d9d9" cellspacing="0" cellpadding="0">
<tr><td><img src="https://github.com/user-attachments/assets/2ef7e877-275d-4115-8ddf-f9b49de8ff5d" alt="SkillCorpus 横幅" width="100%"></td></tr>
</table>

<p align="center">
  <a href="https://arxiv.org/abs/2607.15557"><img src="https://img.shields.io/badge/arXiv-2607.15557-b31b1b?labelColor=gray&style=for-the-badge" alt="Paper"></a>
  <a href="https://huggingface.co/EverMind-AI"><img src="https://img.shields.io/badge/HuggingFace-EverMind-F5C842?labelColor=gray&style=for-the-badge&logo=huggingface&logoColor=white" alt="Hugging Face"></a>
  <a href="https://discord.gg/gYep5nQRZJ"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fdiscord.com%2Fapi%2Fv10%2Finvites%2FgYep5nQRZJ%3Fwith_counts%3Dtrue&query=%24.approximate_presence_count&suffix=%20online&label=Discord&color=404EED&labelColor=gray&style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/EverMind-AI/EverOS/discussions/67"><img src="https://img.shields.io/badge/WeCom-EverMind_%E7%A4%BE%E5%8C%BA-07C160?labelColor=gray&style=for-the-badge&logo=wechat&logoColor=white" alt="WeChat"></a>
</p>

<p align="center"><a href="README.md">English</a> · <strong>简体中文</strong></p>

</div>

<br>

## SkillCorpus 能给你什么

SkillCorpus 是 EverMind 将公开仓库中散落的 `SKILL.md` 文件转化为可靠 agent 上下文的开源流水线：
它聚合源仓库，执行安全与许可门禁，评估质量，并在 agent 作答前匹配与任务相关的技能。

你可以直接使用线上的 [SkillHub](https://evermind.ai/skillhub)，不需要 clone 本仓库。只有当你想拥有
这套能力的开源底座时，才需要 clone SkillCorpus：

- **构建自己的技能层** —— 把流水线指向自己的 source registry，使用策展、安全和许可门禁，再为自己的
  agent 导出语料。
- **修改它的行为** —— 修改分类体系、质量与去重规则、检索配方、导出字段、评测套件或宿主插件。
- **掌控部署方式** —— 自己部署已发布的检索模型，把自己的 agent 接进来，而不是依赖托管的 SkillHub API。

核心代码采用 Apache-2.0 许可（`match/` 和 `evaluate/` 两个工具包为 MIT）；每个技能保留其上游许可。
公开的 1,000 条 demo 语料、三个 agent benchmark 和线上的 SkillHub 展示了结果。

https://github.com/user-attachments/assets/4d9a3241-df13-4b20-9798-fb7920069995

<br>

## &#128293; 最新动态

- **2026-09-02** 支持更智能的 Skill 注入策略：每轮 Query 自动检索，或由主 Agent 在复杂任务和长流程中按需调用 skill_search。
- **2026-08-27** 支持本地 Skill、EverMind SkillHub、ClawHub 和 skillhub.cn 多源检索，并完成过滤、去重和最终 0–2 条选择。
- **2026-08-26** 支持 PathGuard 占位符解析和宿主路径适配，正确处理 Skill 文件与 Agent 工作目录。
- **2026-08-25** 新增面向 WorkBuddy、OpenClaw、Hermes、Raven 和 DeepSeek Harness 的官方 SkillCorpus 插件。

<br>

## 让 agent 每一轮都更强

在 agent 作答时，真正的区别是一层检索：SkillHub 根据当前任务选出经过审核的过程性知识，
并把它放进 agent 的上下文里。

<table width="100%">
<tr>
<th>维度</th>
<th>没有 SkillCorpus</th>
<th>接入 SkillCorpus 之后</th>
</tr>
<tr>
<td><strong>上下文</strong></td>
<td>模型自身知识，加上人工维护的 prompt。</td>
<td>每一轮检索与任务匹配、经过许可审计的 <code>SKILL.md</code>。</td>
</tr>
<tr>
<td><strong>执行</strong></td>
<td>通用流程可能漏掉具体步骤、边界情况或配套脚本。</td>
<td>在执行前把流程、参考资料和可选脚本带进上下文。</td>
</tr>
<tr>
<td><strong>集成</strong></td>
<td>每个宿主都要自己维护一套任务指令。</td>
<td>同一套策展后的技能层服务 OpenClaw、Hermes、Raven、WorkBuddy、DeepSeek Harness 和其他宿主。</td>
</tr>
</table>

结果不是换了一个 agent，而是让同一个 agent 在真正需要时获得更贴合任务的过程性知识——不需要用户
记技能名字，也不需要自己接工具调用。

<br>

## 效果

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

<br>

## SkillHub 集成

SkillHub 已为下面五个 agent 平台提供技能检索。点击对应平台，直接查看插件指南：

<table width="100%">
<tr>
<td width="400" align="center"><a href="skillcorpus_plugin/engine-typescript/README.md"><img src="https://avatars.githubusercontent.com/u/148330874?s=200&amp;v=4" alt="DeepSeek Harness" width="72"><br><strong>DeepSeek Harness</strong></a></td>
<td width="400" align="center"><a href="skillcorpus_plugin/plugin-hermes/README.md"><img src="https://github.com/user-attachments/assets/477eebc4-e615-4425-921e-368d7667e491" alt="Hermes" width="72"><br><strong>Hermes</strong></a></td>
<td width="400" align="center"><img src="https://github.com/user-attachments/assets/01d948fe-1e2b-48e8-9b32-b8057cb3f336" alt="OpenClaw" width="72"><br><strong>OpenClaw</strong><br><a href="skillcorpus_plugin/plugin-openclaw/README.md">1.x</a> / <a href="skillcorpus_plugin/plugin-openclaw2/README.md">2.0</a></td>
<td width="400" align="center"><a href="skillcorpus_plugin/plugin-raven/README.md"><img src="https://github.com/user-attachments/assets/27e1ea63-69d4-48b3-a884-7f0355926907" alt="Raven" width="72"><br><strong>Raven</strong></a></td>
<td width="400" align="center"><a href="skillcorpus_plugin/plugin-workbuddy/README.md"><img src="https://github.com/user-attachments/assets/ab2157dc-90fc-4196-bbf3-87066820f7b4" alt="WorkBuddy" width="72"><br><strong>WorkBuddy</strong></a></td>
</tr>
</table>

两种模式，一个开关。**按需检索**（默认）把 `skill_search` 工具交给 agent，由它自己判断：
长任务只在真正需要的那一步付检索成本，其余轮次不花钱。**`mode: auto`** 是原来的行为——
每一轮、模型作答之前就检索并注入，不需要工具调用，也不用记技能名字。两者互斥，同时开会
让同一轮检索两遍。

OpenClaw 分成两个包，因为 2.0 去掉了 1.x 插件所依赖的那个 hook：`plugin-openclaw` 面向
2026.7.x 及更早，`plugin-openclaw2` 面向 2.0（2026.8.1）及以后。Raven 插件现在就能装、
按需模式可用；`mode: auto` 要等 Raven 上游合并 `context_segments` 插槽后才能认领 `skills`
阶段，在那之前是静默失效的。Raven 自带的检索无论哪种情况都照常工作。

> 根据 https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/INSTALL.agent.md 安装 SkillCorpus Plugins。

把上面这句粘给你的 agent，它会自己装好。各宿主的具体配置、你真正会碰的五个配置项、
每轮的开销、以及哪些数据会离开你的机器——都在
**[`skillcorpus_plugin/`](skillcorpus_plugin)**。

<br>

## 公开产物

下面这张表列出目前真正公开的产物。

| | 产物 | 内容 | 链接 |
|---|---|---|---|
| 🌐 | **SkillHub** | 当前 114,190 条在线目录 + 那两个模型的托管 API，无需安装 | [evermind.ai/skillhub](https://evermind.ai/skillhub) |
| 📚 | **语料** *(demo)* | 可下载的 1,000 条样本 —— `skills.parquet` + `attachments.tar.zst` + dataset card；完整目录由 SkillHub 提供服务 | [🤗 demo-1k](https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k) |
| 🔡 | **检索模型** | 从 `Qwen3-Embedding-0.6B` 和 `Qwen3-Reranker-0.6B` 微调出的 bi-encoder 与 reranker | [🤗 bi-encoder](https://huggingface.co/EverMind-AI/skillcorpus-embedding-0.6b) · [reranker](https://huggingface.co/EverMind-AI/skillcorpus-reranker-0.6b) |
| 🛠️ | **代码** | 本仓库 —— 构建语料、训练那两个模型的流水线（`aggregate` · `curate` · `match` · `evaluate` · `export`） | [GitHub](https://github.com/EverMind-AI/SkillCorpus) |
| 🔌 | **插件** | 为 OpenClaw · Hermes · WorkBuddy · Raven 提供宿主适配器，另有 DeepSeek Harness 与 HTTP adapter | [`skillcorpus_plugin/`](skillcorpus_plugin) |

*目前开源：代码、1,000 条 demo 语料和检索模型。托管的 SkillHub 服务不开源，完整在线目录也尚未作为可下载数据集发布。*

<div align="center">
<img src="https://github.com/user-attachments/assets/71edc5ab-291f-4fb8-8f5c-177d50e5b8f4" alt="96,401 个有效技能的 16 类分布" width="58%">
</div>

论文所评测的 96,401 条快照，按 16 类体系和三个质量维度（utility / robustness / safety）组织，并带 1024 维
检索向量。字段约定见 [`docs/corpus-schema.md`](docs/corpus-schema.md)。

<br>

## 直接调 API

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

<br>

## 自建模型服务

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

<br>

## 工作原理

<div align="center">
<img src="https://github.com/user-attachments/assets/e0e72150-373b-4381-ad6c-74668d436d49" alt="SkillCorpus：把策展后的技能匹配到任务，并在执行前注入 agent 上下文" width="100%">
<p><em>收集和策展是基础，真正的价值是 agent 执行前能拿到与任务匹配的技能。</em></p>
</div>

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

<br>

## 构建自己的语料

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

<br>

## 路线图

<!-- TODO(@team)：这是按已知缺口列的初版，请按实际计划修改。 -->

- [x] 策展管线：16 类体系、三维质量、逐源许可审计
- [x] 微调检索栈 + 三个 benchmark 的评估
- [x] 公开的 SkillHub 端点
- [x] 检索模型（bi-encoder + reranker）与 1k demo 语料上 HuggingFace
- [ ] 完整的 114,190 条语料上 HuggingFace
- [x] 两个检索模型的部署脚本（自建 `match/`）
- [x] 把 skill 库 + 检索框架打包成插件，供 WorkBuddy · Hermes · OpenClaw · DeepSeek Harness 使用
- [ ] Raven 插件——已打包，等上游 `context_segments` 插槽

<br>

## EverMind 生态

EverMind 将记忆研究、可直接使用的产品与实际集成连接为一个开源生态。

<table>
<tr>
<th colspan="2">产品</th>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/EverOS">EverOS</a></strong></td>
<td>本地优先、Markdown 原生的 Agent 与用户长期记忆运行时。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/Raven">Raven</a></strong></td>
<td>以记忆为核心的自进化 Agent Harness，具备主动性、上下文控制与 Skill 进化能力。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/EverMe">EverMe（CLI）</a></strong></td>
<td>面向跨设备、跨 Agent 个人记忆的 CLI 与 Agent 插件套件。</td>
</tr>
<tr>
<th colspan="2">研究与评测</th>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/SkillCorpus">SkillCorpus</a></strong></td>
<td>将分散的 Agent Skill 整理为可检索语料库，并提供检索与评测工具。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/EverAlgo">EverAlgo</a></strong></td>
<td>为 EverOS 提供无状态的提取、排序、解析与记忆算法。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/HyperMem">HyperMem</a></strong></td>
<td>基于超图的分层记忆架构，用于由粗到细的长期对话检索。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/MSA">MSA</a></strong></td>
<td>面向可扩展潜在记忆与一亿 Token 上下文的 Memory Sparse Attention。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/EverMemBench">EverMemBench</a></strong></td>
<td>从事实召回、应用推理和个性化泛化三个层面评测记忆系统。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/EverMind-AI/EvoAgentBench">EvoAgentBench</a></strong></td>
<td>纵向评测 Agent 自进化、迁移效率、错误规避和 Skill 使用能力。</td>
</tr>
<tr>
<th colspan="2"><a href="https://github.com/EverMind-AI/plugins">插件与集成</a></th>
</tr>
<tr>
<td><strong><a href="https://docs.openclaw.ai">OpenClaw</a></strong></td>
<td><a href="https://github.com/EverMind-AI/plugins/tree/main/openclaw">OpenClaw 插件</a>，自动管理召回、写入与会话记忆生命周期。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/NousResearch/hermes-agent">Hermes Agent</a></strong></td>
<td><a href="https://github.com/EverMind-AI/plugins/tree/main/hermes">Hermes 插件</a>，为 Hermes 会话提供持久记忆。</td>
</tr>
<tr>
<td><strong><a href="https://github.com/deepseek-ai/DeepSeek-Harness">DeepSeek Harness</a></strong></td>
<td><a href="https://github.com/EverMind-AI/plugins/tree/main/dsh">DSH 插件</a>，让 DeepSeek Harness Agent 使用长期记忆。</td>
</tr>
<tr>
<td><strong><a href="https://dify.ai">Dify</a></strong></td>
<td><a href="https://github.com/EverMind-AI/plugins/tree/main/dify">本地版</a>与<a href="https://github.com/EverMind-AI/plugins/tree/main/dify_cloud">云端版</a>工具，在工作流和 Agent 中显式搜索与写入记忆。</td>
</tr>
</table>

这些项目共同构成 EverMind 从研究到运行时的完整链路：将方法与评测转化为
可复用的记忆基础设施、产品和 Agent 集成。

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

<br>

## 许可

- **代码** —— Apache-2.0（`match/` 和 `evaluate/` 两个工具包各自为 MIT，见其目录下的 `LICENSE`）。
- **语料** —— 每个技能保留其**上游原始许可**；只收录 GREEN（MIT / Apache-2.0 / BSD / ISC / …）
  许可的技能，不做任何重新授权。每行都带 `source`、`source_url`、`license`，下游使用须遵循
  各技能自身的条款。

完整的 GREEN/RED/YELLOW 策略、许可数据流与 opt-out 通道见
[`docs/licence-and-governance.md`](docs/licence-and-governance.md)。
