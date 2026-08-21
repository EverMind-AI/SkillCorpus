<!-- SkillHub 是在线产品；本仓库包含它背后的开源语料、检索、评估、导出和插件层。 -->

<div align="center" id="readme-top">

<table width="100%" border="1" bordercolor="#d9d9d9" cellspacing="0" cellpadding="0">
<tr><td><img src="https://github.com/user-attachments/assets/2ef7e877-275d-4115-8ddf-f9b49de8ff5d" alt="SkillCorpus 横幅" width="100%"></td></tr>
</table>

<p align="center">
  <a href="https://arxiv.org/abs/2607.15557"><img src="https://img.shields.io/badge/arXiv-2607.15557-b31b1b?labelColor=gray&style=for-the-badge" alt="Paper"></a>
  <a href="https://evermind.ai/skillhub"><img src="https://img.shields.io/badge/SkillHub-live-2ea44f?labelColor=gray&style=for-the-badge" alt="SkillHub"></a>
  <a href="https://huggingface.co/EverMind-AI"><img src="https://img.shields.io/badge/HuggingFace-EverMind-F5C842?labelColor=gray&style=for-the-badge&logo=huggingface&logoColor=white" alt="Hugging Face"></a>
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

## SkillHub 集成

SkillHub 已为下面五个 agent 平台提供逐轮技能检索。点击对应平台，直接查看插件指南：

<table width="100%">
<tr>
<td width="400" align="center"><a href="skillcorpus_plugin/engine-typescript/README.md"><img src="https://avatars.githubusercontent.com/u/148330874?s=200&amp;v=4" alt="DeepSeek Harness" width="72"><br><strong>DeepSeek Harness</strong></a></td>
<td width="400" align="center"><a href="skillcorpus_plugin/plugin-hermes/README.md"><img src="https://github.com/user-attachments/assets/477eebc4-e615-4425-921e-368d7667e491" alt="Hermes" width="72"><br><strong>Hermes</strong></a></td>
<td width="400" align="center"><a href="skillcorpus_plugin/plugin-openclaw/README.md"><img src="https://github.com/user-attachments/assets/01d948fe-1e2b-48e8-9b32-b8057cb3f336" alt="OpenClaw" width="72"><br><strong>OpenClaw</strong></a></td>
<td width="400" align="center"><a href="skillcorpus_plugin/plugin-raven/README.md"><img src="https://github.com/user-attachments/assets/27e1ea63-69d4-48b3-a884-7f0355926907" alt="Raven" width="72"><br><strong>Raven</strong></a></td>
<td width="400" align="center"><a href="skillcorpus_plugin/plugin-workbuddy/README.md"><img src="https://github.com/user-attachments/assets/ab2157dc-90fc-4196-bbf3-87066820f7b4" alt="WorkBuddy" width="72"><br><strong>WorkBuddy</strong></a></td>
</tr>
</table>

检索在每一轮、模型作答之前运行：不需要工具调用，不用记技能名字，也不用改宿主代码。
Raven 插件已经可以安装，但要等 Raven 上游合并 `context_segments` 插槽后才能真正认领
`skills` 阶段；Raven 自带的检索今天照常工作。

> 根据 https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/INSTALL.agent.md 安装 SkillCorpus Plugins。

把上面这句粘给你的 agent，它会自己装好。各宿主的具体配置、你真正会碰的五个配置项、
每轮的开销、以及哪些数据会离开你的机器——都在
**[`skillcorpus_plugin/`](skillcorpus_plugin)**。

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
<img src="docs/assets/taxonomy.png" alt="96,401 个有效技能的 16 类分布" width="58%">
</div>

论文所评测的 96,401 条快照，按 16 类体系和三个质量维度（utility / robustness / safety）组织，并带 1024 维
检索向量。字段约定见 [`docs/corpus-schema.md`](docs/corpus-schema.md)。

## 动态

- **2026-08-19** —— **SkillCorpus Plugins** 发布：在 WorkBuddy、Hermes、OpenClaw、DeepSeek Harness 里逐轮检索技能。
- **2026-08-12** —— 检索模型（bi-encoder + reranker）与 1,000 条 demo 语料上 [🤗 HuggingFace](https://huggingface.co/EverMind-AI)。
- **2026-08-06** —— 论文 v5 上 [arXiv](https://arxiv.org/abs/2607.15557)。

## 其余用法

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

## 工作原理

<div align="center">
<img src="docs/assets/pipeline.png" alt="SkillCorpus：把策展后的技能匹配到任务，并在执行前注入 agent 上下文" width="100%">
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

## EverMind 生态系统

EverMind 是一个面向长期记忆、自我演进 agent、AI 原生界面和记忆评估的开源生态系统。

<table>
<tr>
<th colspan="2">EverMind 开源生态系统</th>
</tr>
<tr>
<td><strong>记忆运行时</strong></td>
<td><a href="https://github.com/EverMind-AI/EverOS">EverOS</a> —— 面向 agent 与用户记忆的本地记忆操作系统和研究支撑运行时。</td>
</tr>
<tr>
<td><strong>自我改进 Agent Harness</strong></td>
<td><a href="https://github.com/EverMind-AI/Raven">Raven</a> —— 将记忆、主动性、上下文控制和技能演进带入终端原生 agent 的 harness。</td>
</tr>
<tr>
<td><strong>Agent 技能与检索</strong></td>
<td><a href="https://github.com/EverMind-AI/SkillCorpus">SkillCorpus</a> —— 开放策展与检索工具、公开的 <a href="https://huggingface.co/datasets/EverMind-AI/skillcorpus-demo-1k">1K demo 语料</a>、<a href="https://evermind.ai/skillhub">SkillHub</a>、agent 集成和基准测试。</td>
</tr>
<tr>
<td><strong>算法引擎</strong></td>
<td><a href="https://github.com/EverMind-AI/EverAlgo">EverAlgo</a> —— 为 EverOS 提供支持的无状态提取、排序、解析和记忆算子。</td>
</tr>
<tr>
<td><strong>超图记忆</strong></td>
<td><a href="https://github.com/EverMind-AI/HyperMem">HyperMem</a> —— 面向长期对话的超图记忆，提供经过基准验证的主题 → episode → 事实检索方法。</td>
</tr>
<tr>
<td><strong>评测基准</strong></td>
<td><a href="https://github.com/EverMind-AI/EverMemBench">EverMemBench</a> · <a href="https://github.com/EverMind-AI/EvoAgentBench">EvoAgentBench</a> —— 对话记忆和 agent 自我演进的评估套件。</td>
</tr>
<tr>
<td><strong>长上下文研究</strong></td>
<td><a href="https://github.com/EverMind-AI/MSA">MSA</a> —— 面向可扩展潜在记忆和 100M token 上下文的 Memory Sparse Attention。</td>
</tr>
<tr>
<td><strong>个人记忆层</strong></td>
<td><a href="https://github.com/EverMind-AI/EverMe">EverMe</a> —— 面向跨设备、跨 agent 个人记忆的 CLI 与 agent 插件套件。</td>
</tr>
<tr>
<td><strong>开发者集成</strong></td>
<td><a href="https://github.com/EverMind-AI/evermem-claude-code">evermem-claude-code</a> · <a href="https://github.com/EverMind-AI/everos-plugins">everos-plugins</a> —— 面向 AI 编程 agent 的插件、技能与迁移工具。</td>
</tr>
</table>

这些仓库共同构成 EverMind 从研究到运行时的技术栈：新的记忆方法、可复用算法、基准证据与实用的 agent 集成。

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
