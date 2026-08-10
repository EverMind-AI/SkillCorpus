<!-- 占位链接标为 `#`，待补：Corpus (HF dataset)、Embedding model (HF)、Code (repo)、SkillHub endpoint。 -->

[English](README.md) | **简体中文**

# SkillCorpus

[![Paper](https://img.shields.io/badge/arXiv-2607.15557-b31b1b.svg)](https://arxiv.org/abs/2607.15557)
[![Corpus](https://img.shields.io/badge/%F0%9F%A4%97-Corpus-yellow.svg)](#)
[![Embedding model](https://img.shields.io/badge/%F0%9F%A4%97-Embedding%20model-yellow.svg)](#)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](#许可)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

**给你的 agent 96,401 个经过筛选、许可合规的技能——外加一个能为每个任务挑出正确技能的检索器。**

SkillCorpus 把约 821,000 个爬取到的 `SKILL.md` 文件整合成一份经过策展和许可审计的语料，
发布配套的微调检索栈，并在三个真实 agent benchmark 上端到端评估了整套方案。

<p align="center">
  <img src="docs/assets/pipeline.png" alt="SkillCorpus：构建语料（aggregate + curate）与使用语料（match + evaluate）" width="100%">
</p>

## 它能帮你做什么

| 你想… | 用这个 |
|---|---|
| **让 agent 在运行时用上技能** | [SkillHub](#1-调用-skillhub托管) —— 查询托管索引，拿回该任务对应的技能 |
| **拿到数据** | [语料](#2-加载语料) —— HuggingFace 上 96,401 行，一行 `load_dataset` |
| **接进自己的 harness** | [集成](#3-接进你的-agent) —— OpenClaw / Raven / Hermes |
| **用自己的源构建语料** | [自行重建](#自行重建语料) —— 完整的六阶段管线 |
| **复现论文** | [`skillcorpus/evaluate/`](skillcorpus/evaluate) —— SkillsBench · GDPVal · QwenClawBench |

### 接入之后有什么变化

同一个 harness、同一个 backbone，无技能 → 接入 SkillCorpus 的表现（[论文 Table 1](https://arxiv.org/abs/2607.15557)）：

| Harness × backbone | SkillsBench | GDPVal | QwenClawBench |
|---|---|---|---|
| OpenClaw × Qwen3.5-27B | 8.8 → **13.0** | 81.2 → **83.1** | 65.2 → **66.7** |
| OpenClaw × Qwen3.5-397B | 11.1 → **16.9** | 82.2 → **84.0** | 65.7 → **67.0** |
| Raven × Qwen3.5-27B | 10.0 → **16.5** | 82.6 → **83.8** | 66.9 → **70.8** |
| Raven × Qwen3.5-397B | 9.2 → **22.6** | 84.0 → **85.2** | 68.8 → **73.2** |
| **合并 ∆** | **+7.5**±2.3 (z=3.2) | **+1.51**±0.49 (z=3.1) | **+2.79**±0.70 (z=4.0) |

任务越依赖模型本身不具备的过程性知识，收益越大（SkillsBench）；模型本来就能做的开放式
经济类任务收益最小（GDPVal）。

---

## 快速开始

### 1. 调用 SkillHub（托管）

<!-- TODO(@team)：把 SKILLHUB_URL 换成真实地址，并确认下面的请求/响应格式与实际服务一致。 -->

不用安装、不用下模型——直接用任务描述来要技能：

```bash
curl -X POST https://<SKILLHUB_URL>/v1/skills/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "extract tables from a scanned PDF invoice", "top_k": 3}'
```

```json
{
  "skills": [
    {
      "name": "pdf-table-extraction",
      "description": "Extract tables from scanned PDFs into structured rows …",
      "category": "DOC-PROC",
      "score": 0.91,
      "license": "MIT",
      "source_url": "https://github.com/…",
      "body": "# PDF Table Extraction\n## Steps\n…"
    }
  ]
}
```

把 `body` 注入 agent 的 prompt，它就能完成任务。整个闭环就这么简单——
[`examples/skillhub_demo.py`](examples/skillhub_demo.py) 把这条链路跑通了：

```bash
export SKILLHUB_URL=https://<SKILLHUB_URL>

# 只检索 —— 纯标准库，不用安装，不用 API key
python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

# 检索并把技能注入 prompt 后真正执行任务
export OPENAI_API_KEY=...
python examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"
```

```
task: extract tables from a scanned PDF invoice

SkillHub returned 3 skill(s):

  1. pdf-table-extraction   (score 0.912)
     Extract tables from scanned PDFs into structured rows using OCR + layout analysis.
     MIT · https://github.com/…

  2. invoice-field-parser   (score 0.864)
     …

→ built a prompt of 14,203 chars with the skills injected
  (re-run with --ask to actually execute the task)
```

### 2. 加载语料

```python
from datasets import load_dataset

skills = load_dataset("<org>/skillcorpus", split="train")   # 96,401 行
skills.filter(lambda r: r["category"] == "DOC-PROC")
```

字段约定见 [`docs/corpus-schema.md`](docs/corpus-schema.md)。附件（`scripts/`、`references/`）
以同级的 `attachments.tar.zst` 形式发布。

### 3. 接进你的 agent

<!-- TODO(@team)：SkillHub 客户端发布后，补上三个 harness 的真实配置键和文件路径。
     下列三个即论文中评估过的 harness。 -->

<details>
<summary><b>OpenClaw</b></summary>

```yaml
# ~/.openclaw/config.yaml
skills:
  provider: skillhub
  endpoint: https://<SKILLHUB_URL>
  top_k: 3
```
</details>

<details>
<summary><b>Raven</b></summary>

```yaml
# raven config
skill_forge:
  provider: skillhub
  endpoint: https://<SKILLHUB_URL>
  top_k: 3
```
</details>

<details>
<summary><b>Hermes</b></summary>

```yaml
# TODO: Hermes 集成
```
</details>

任何能往 system prompt 里注入文本的 harness 都适用——调搜索接口，把返回的 `body` 贴进去。
详见 [`docs/integrations.md`](docs/integrations.md)。

### 自行重建语料

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

---

## 发布的产物

| | 产物 | 内容 | 链接 |
|---|---|---|---|
| 🌐 | **SkillHub** | 基于该语料的托管检索端点 | [endpoint](#) |
| 📚 | **语料** | `skills.parquet` + `attachments.tar.zst` + dataset card | [🤗 HuggingFace](#) |
| 🔡 | **Embedding 模型** | 面向技能检索微调的 `Qwen3-Embedding-0.6B`（2048 上下文） | [🤗 HuggingFace](#) |
| 🛠️ | **代码** | 本框架 —— `aggregate` · `curate` · `match` · `evaluate` · `export` | [GitHub](#) |

## 语料

<p align="center">
  <img src="docs/assets/taxonomy.png" alt="96,401 个有效技能的 16 类分布" width="60%">
</p>

96,401 个技能，来自约 821,000 个爬取文件，按 16 类体系和三个质量维度
（utility / robustness / safety）组织，并带 1024 维检索向量。

## 工作原理

```
aggregate ─────────────► curate ──────────────────────────────────► export
 抓取公开仓库             parse · safety · license                   skills.parquet
                          classify · quality · dedup · license-gate   + attachments.tar.zst + card
```

四个阶段就是包内的四个子模块：

1. **`aggregate`** —— 从公开的 `SKILL.md` 仓库发现并克隆技能。
2. **`curate`** —— 解析 · 安全 · 许可门禁 · 去重 · 16 类分类 · 质量打分。
3. **`match`** —— SkillRouter：微调的 bi-encoder + reranker，为任务检索技能。
4. **`evaluate`** —— 三个 benchmark：`skillsbench` · `qwenclawbench` · `gdpval`。

`cli build` 会跑完整条链路（`ingest → quality_pass → dedup_pass → license_audit → export.corpus`）。
当没有可达的模型端点时，LLM 分类和质量打分会优雅降级为规则实现，因此管线总能端到端跑通。

## 仓库结构

```
skillcorpus/
├── core/       数据模型 · SQLite/faiss 存储 · LLM 与 embedding 客户端
├── aggregate/  源注册表 + 多仓库克隆
├── curate/     解析 · 安全 · 许可 · 分类 · 质量 · 去重 + 全库扫描
├── export/     语料写出（parquet + 附件 + dataset card）
├── match/      SkillRouter —— 检索栈（bi-encoder + reranker）      ← 依赖独立
├── evaluate/   skillsbench · qwenclawbench · gdpval 评测           ← 依赖独立
└── cli.py      build · stats · export
```

`match/` 和 `evaluate/` 是独立工具包，各有自己的 `requirements.txt`（torch / transformers，
按 benchmark 区分）；**不会**被 `pip install` 主包时带进来。

- **检索** —— [`skillcorpus/match/`](skillcorpus/match)：在合成 query 上微调 Qwen3 bi-encoder
  和 reranker，然后为 query 排序技能。检索指标（nDCG / MRR / Hit / Recall）由
  `eval_compare.py` 计算。
- **评测** —— [`skillcorpus/evaluate/`](skillcorpus/evaluate)：`skillsbench`、`qwenclawbench`、
  `gdpval`，各自独立，带自己的 README 和依赖。

## 许可

- **代码** —— Apache-2.0（`match/` 和 `evaluate/` 两个工具包各自为 MIT，见其目录下的 `LICENSE`）。
- **语料** —— 每个技能保留其**上游原始许可**；只收录 GREEN（MIT / Apache-2.0 / BSD / ISC / …）
  许可的技能，不做任何重新授权。每行都带 `source`、`source_url`、`license`，下游使用须遵循
  各技能自身的条款。

完整的 GREEN/RED/YELLOW 策略、许可数据流与 opt-out 通道见
[`docs/licence-and-governance.md`](docs/licence-and-governance.md)。

## 测试

```bash
pip install -e ".[dev]"
python -m pytest skillcorpus/tests -p no:cacheprovider --import-mode=importlib
```

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
