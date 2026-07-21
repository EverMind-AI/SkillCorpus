# Skill Library

通用 skill 库构建管线 — 多源聚合 + CRUD + 入库筛选 + 主动 refresh.

**架构**: producer (本仓) 写 SQLite + faiss + 文件树, 通过 `skill_library.export` 导出
`mass_library.db`; consumer (`everclaw/skill_forge/`)
通过 `SqliteStore` attach 该 DB 作为 mass pool, 配合 filesystem 上的 scripts/references 附件.
LLM 主路径 + 规则兜底, embedding 走共享 SkillRouter remote endpoint (runtime 检索由 consumer 负责).

---

## 快速开始

```bash
pip install pyyaml numpy click faiss-cpu sqlite-vec openai   # 依赖
python3 -m skill_library.cli build                        # 从零构建 (默认 demo 4 源) → data/index.db
python3 -m skill_library.cli stats                        # 看库统计
python3 -m skill_library.cli build --update               # 之后: 增量更新 (按 cadence 只跑到期源)
```

- **构建/更新是同一条命令** `cli build`(`--update` 区分),详见 [用法](#用法)。
- 公开默认读 `sources.yaml`(demo 4 个 permissive 源);`data/` 产物不随仓库发布,`build` 会从这些源自建本地库。全量 62 源在私有 `sources.full.yaml`(`--full`)。
- embedding / LLM 端点见 `config.yaml`;**端点不可达时自动降级**(分类→`OTHER`,检索→BM25-only),流程仍可完整运行。

---

## 状态 (/new endpoint 迁移后, 2026-05-30)

```
producer index.db
  total              157,802
  active=1 deleted=0   96,401   (GREEN license, exportable)
  active=0 deleted=0   47,181   (非 GREEN, 入库保留但不导出)
  deleted=1            14,220   (dedup soft-delete)

consumer mass pool (post-license-filter + post-align)
  mass_library.db      96,401 行  ·  1.2 GB  (embedding-our-new, byte-identical vs producer)

Endpoint        http://<EMBEDDING_HOST>/new   (内网, 自训模型)
Embedding 公式   name | desc[:500] | strip(body)[:8000]   (producer 与 consumer 已对齐)
```

架构定型: SQLite mass pool + 共享 GPU embedding/reranker endpoint + cron 主动 refresh.

---

## 依赖

**SkillRouter remote endpoint** (共享 GPU service):
| Path | 用途 | 用方 |
|---|---|---|
| `POST /embed   {"texts":[...]}`  → `{"embeddings":[[1024]]}` | embedding | **producer**(去重 + 导出向量)|
| `POST /score   {"prompts":[...]}` → `{"scores":[...]}`         | reranker (P(yes)) | consumer(检索精排;producer 不用)|

`config.yaml` 的 `embedding.provider = "skillrouter_remote"` 指向 endpoint, helper
里加 5 次 retry/backoff 防 RST.

**LLM 调用** (分类 / quality judge / dedup judge): 远端 OpenAI-compatible endpoint,
见 `config.yaml` 的 `llm.endpoints`.

**降级**:
- LLM 不可用: 分类 fallback 为 `OTHER` + tag 仍走规则提取
- Embedding 不可用: 跳过 embedding 去重 + retrieval 退化 BM25-only

---

## 架构

```
┌─ PRODUCER (this repo) ──────────────────────┐
│  data/index.db        SQLite metadata       │
│  data/skill_index.faiss  HNSW (dedup 加速)  │
│  data/skills/<source>/<name>/{scripts,refs} │
│       ↓ ingest pipeline (并发 8)             │
│  parse → safety → quality 长度闸 →          │
│  sub-skill 过滤 → dedup 三层     →         │
│  classify → LLM quality → embed → store     │
└──────────┬──────────────────────────────────┘
           │ export_to_mass_library
           │   ┌──→ mass_library.db   (DB: body + emb + meta)
           │   └──→ skills/<src>/<n>/  (FS: scripts/refs only;
           │                            SKILL.md not needed,
           │                            body is in DB)
           │   写 .stale + .refresh_endpoint
           ▼
┌─ CONSUMER MOUNT ─────────────────────────────┐
│  mass_library.db        attach via SqliteStore
│  skills/<src>/<name>/   real path for {baseDir}
│  .stale                 next start consumes & clears
│  .refresh_endpoint      sentinel for `skill refresh` CLI
└──────────┬──────────────────────────────────┘
           │ consumer SqliteStore.iter_index_rows() → Retrieval
           │ + LocalPool (BM25 over workspace + builtin + everos_light)
           │ → RRF fusion
           ▼
       everclaw/skill_forge/
       (dense mass pool + lexical local pool)
```

### 入库 dedup 三层

1. **精确**: `content_hash` SHA-256 normalized body 完全相同 → DUPLICATE
2. **同 source canonical name**: name_hash 命中 → 覆盖旧 record
3. **跨 source 近似**: name_hash 跨 source 冲突 OR cosine ≥ 0.90 → `LLMDupJudge` 二次确认; cos ≥ 0.995 自动判重 (cache)

### 父-子 skill (三端一致)

- 父 skill = 顶层 `<source>/<name>/SKILL.md` (depth=3 in producer fs)
- 子文件夹下 SKILL.md = 父的附属普通文件 (read_file 可读, 不索引为单独 skill)
- producer `_drop_subskill_paths` (`pipeline.py`) 写时过滤 + consumer `_iter_skill_dirs`
  (`registry.py`) 读时过滤 = 双层防护

### `{baseDir}` 解析

producer 出库时只为 body 真正引用 fs 附件的 skill 填 `mass_library.db.path` 列 (~40%; 由
`export._dir_referenced_assets` 目录接地检测决定 — 读 skill 目录真实文件再与 body 比对).
consumer `_row_to_meta` 读 path 后,
`load_skills_for_context` 把 body 里的 `{baseDir}` 替换为 `meta.path.parent` (=
真实 fs 目录), agent 拿到的就是可访问绝对路径. `path=NULL` 的 sqlite-only row
触发 `sqlite://` 守卫跳过替换, body 原样输出.

---

## 分类 (LLM 分类器, 16 类)

| 组 | 分类 |
|---|---|
| 软件开发栈 (5) | DEV, FRONTEND-UI, DEVOPS-INFRA, TESTING, SECURITY |
| 数据/AI (2) | DATA, AI-ML |
| 认证 (1) | AUTH |
| 内容输出 (4) | DOC-PROC, WRITING, MULTIMEDIA, COMMS |
| 流程/办公 (2) | WORKFLOW, PRODUCTIVITY |
| 元工具 (1) | META |
| 兜底 (1) | OTHER |

实现:`metadata.py` 内置分类 prompt(自训 Qwen3.5-397B,1000-sample 测试 100% 命中,0 OOV)。
**Tag**: 由 `metadata.py` 的规则提取 3-5 个关键词(独立于主分类)。

---

## 源清单 & 可复现性边界

### 源列在哪 — 注册表(公开 demo + 私有全量)

**所有源入口统一收敛在 YAML 注册表**;`fetch.py`(全量 crawl)和 `scripts/refresh_loop.py`(定时刷新)都从它读,经 `fetch.py:discover_repos` 按 `type` 路由。**新增/删源只改 YAML,不动代码。**

| 文件 | 内容 | 发布 |
|---|---|---|
| `sources.yaml` | **公开默认 = demo**(4 个 permissive git_clone 源:anthropics/skills、vercel-labs/skills、addyosmani/agent-skills、K-Dense-AI/scientific-agent-skills)| ✅ 进 git,公开 |
| `sources.full.yaml` | **生产全量 62 源 / 6 type** | ❌ `.gitignore` 排除,私有 |

- 公开用户:`python -m skill_library.fetch`(默认 demo)→ 完整运行流程, 自建本地库
- 生产端:`python -m skill_library.fetch --config skill_library/sources.full.yaml` → 全量

**部分开源模型**:公开仓 = 完整 code + demo `sources.yaml`,**不发** `data/`(index.db/skills/mass_library 均被 .gitignore 排除)和 `sources.full.yaml`。用户可完整运行流程并得到自建的本地库,但无法获取全量源清单与成品 96K 语料。

全量 62 源的 type 分布(私有 sources.full.yaml):

| type | 条数 | discovery 方式 |
|---|---:|---|
| `readme_scrape` | 19 | clone awesome 清单 + 抓取 README 中的外链(动态展开成数千 repo) |
| `git_clone` | 38 | 指名直接 clone(含 antigravity→sickn33、majiayu000 等自定义 label) |
| `index_api` | 2 | REST API 分页(skillsdirectory + skillsmp) |
| `json_catalog` | 1 | clone + 解析 JSON 目录(skillmanager) |
| `sitemap_scrape` | 1 | 抓 skills.sh sitemap → owner/repo |
| `lobehub_json` | 1 | clone + lobehub_to_skills.py 转换 |

另:`data/source_manifest.csv`(5,375 行)是**输出快照**(这份 data 实际产出了哪些源 → repo URL → skill 数,从 index.db 导出,随 data/ 迁移)。sources.yaml 是**输入注册表**(要 fetch 什么);manifest 是**结果账本**。

(已退役入口:`--from-skillsbench`/`--from-datahub` 及 fetch.py 旧的硬编码 `README_SOURCES`/`DIRECT_REPOS` 常量、5 个 `--from-X` flag —— 全部收敛进 sources.yaml 或删除。)

### 能否从空 data/ 完整复现当前 data?**不能 bit-for-bit 复现**

注册表 62 个入口里 `readme_scrape`/`index_api`/`sitemap_scrape` 会**动态展开**成 ~5,375 源 / 96,401 skill。即使把上游清单和所有 repo **完全冻结**, 仍有 5 个相互独立的因素阻止逐字节复现:

| # | 因素 | 说明 |
|---|---|---|
| A | **上游列表更新** | awesome 清单增删链接、repo 删库/改名/转私有(最大来源) |
| B | **同仓内容漂移** | `git clone --depth 1` 取默认分支**最新 commit, 不锁 SHA** → 同 repo 内容可能已变 |
| C | **LLM 非确定** | `config.yaml temperature=0.1`(非 0)→ category + quality_score 每次跑可能不同; 端点模型可能被换 |
| D | **dedup winner 翻转** | `_pick_winner`: quality(LLM)→ source → `added_at`(入库时间)。并发 8 + 时间 tie-break → 近似重复里谁存活会变 |
| E | **embedding 漂移** | 向量驱动 dedup cosine 阈值(0.90/0.995), 临界对翻转; 换端点向量也变 |

(另有运维瞬态: GitHub license API 的 404/限流、clone 超时导致每次成功子集不同。)

### 缓解 & 实践结论

- LLM 打分/判重**按 content_hash 缓存**(`quality_judgments` / `dedup_judgments` 表)。**从冻结的 `data/skills/` + 缓存表重跑** → 内容不变即命中缓存 → C/D 基本可复现; **从空 data/ re-fetch 重判** → 无缓存 + 重 clone → A–E 全发作 → 不可复现。
- **可复现性取决于从哪起跑**: 现有 data 快照(含 skills/ 树 + index.db + 缓存)≈ 可复现且自包含可直接用; 空目录 re-fetch ≠ 可复现(会得到相似但不同的库)。
- 要做到空目录高度复现需额外: pin commit SHA(治 B)+ temperature=0 + 锁模型版本(治 C/E)+ 固定单线程 ingest 顺序(治 D)。**当前不做** —— producer 目标是"持续吸纳最新 skill", 不是"可重放实验"。当前 data 已是自包含快照, 配 `source_manifest.csv` 可审计来源。

---

## 文件结构

```
skill_library/
├── README.md / __init__.py / cli.py / config.yaml
│
│ ── 入库 pipeline (按阶段) ──
├── fetch.py            # 源注册表 (load_registry/discover_repos, 按 type dispatch)
│                       #   + Stage 0: 多源 git clone (GIT_TERMINAL_PROMPT=0)
├── pipeline.py         # Coordinator (串/并发 + sub-skill 过滤) + SkillLibrary 顶层 API
├── rules.py            # 纯规则阶段: SKILL.md 解析 + safety 正则 + license GREEN 闸
├── dedup.py            # 规则 dedup (content/name hash + cosine) + LLM 判重
├── metadata.py         # LLM 阶段: quality judge (3-facet/19-flag) + 16-class 分类 + tag
├── embed.py            # SkillRouter remote embedding 客户端
├── store.py            # SQLite + sqlite-vec + faiss (含 SkillRecord schema)
├── export.py           # producer index.db → mass_library.db (全量导出 + 增量 sync)
│
│ ── 辅助工具 ──
├── llm.py              # OpenAI-compatible LLM client (单端点)
├── license_audit.py    # license 维护 CLI: refresh/build/validate/apply/stats
│
│ ── 配置 (进 git) ──
├── sources.yaml                  # 公开 demo 源列表 (4 个 permissive 源)
├── sources.full.yaml             # 生产全量 62 源 (git-ignored, 私有)
├── license_safe_sources.json     # GREEN-license 白名单
│
│ ── 数据 (git-ignored) ──
├── data/
│   ├── index.db
│   ├── skill_index.faiss + skill_index_ids.json
│   └── skills/<source>/<name>/
│
│ ── 运维脚本 (非 pipeline) ──
├── scripts/
│   ├── rescan_dedup.py        # 整库 backfill 近似去重
│   ├── rescan_quality.py      # 整库 LLM 评分 backfill
│   ├── source_resync.py       # 单源增量 refresh (跳已在库的)
│   ├── refresh_loop.py        # cron 调度 (按 cadence 跑到期源)
│   ├── refresh_server.py      # HTTP trigger (:8765, consumer 远程触发)
│   └── lobehub_to_skills.py   # LobeHub agent JSON → SKILL.md
│
└── tests/ (9 单元测试)
```

---

## 用法

### 一键全流程 — `cli build`(新建 + 更新同一入口)

**新建和增量更新是同一个功能**, 靠 `--update` 区分。空库自动 init(`open()` 内置
`init_schema`), 链路 = discover → clone → ingest → quality → export:

```bash
python3 -m skill_library.cli build              # 从零新建 (默认 demo 4 源, 全跑)
python3 -m skill_library.cli build --update     # 增量更新 (按 cadence 只跑到期源)
python3 -m skill_library.cli build --full       # 全量注册表 (62 源, 生产; 可叠 --update)
python3 -m skill_library.cli build --source anthropics/skills   # 只跑单源
python3 -m skill_library.cli build --dry-run    # 只发现+打印, 不实际跑
```

底层是 `scripts/refresh_loop.py:run_refresh()`(`--update` 翻 `force`,`--full` 切注册表),
也可直接 `python3 -m skill_library.scripts.refresh_loop [--config ...] [--force] [--source ...]`。

**手动分步** — `cli build` 已自动串起下面这些;只有想单独控制每步时才用:

```bash
# 1. 多源 git clone → 暂存到 experiment-results/_reference_skills/_fetched/<owner>/<repo>/
#    (默认 demo 4 源; 全量加 --config sources.full.yaml, ~30min/~6K repo)
python3 -m skill_library.fetch --workers 16

# 2. ingest 到 DB (跑完整 pipeline: parse→safety→quality→dedup→classify→embed→store)
#    输入 = 上一步 clone 的 _fetched 目录 (store 再把入库的 skill 写到 data/skills/)
python3 -m skill_library.cli add-batch experiment-results/_reference_skills/_fetched/<owner>/<repo> --source <owner>/<repo>

# 3. (可选) 整库 backfill (大批量 ingest 关了 inline LLM 后补跑)
python3 -m skill_library.scripts.rescan_dedup
python3 -m skill_library.scripts.rescan_quality --workers 16

# 4. 导出到 consumer mass pool (写 mass_library.db + .stale)
python3 -m skill_library.export
```

### CRUD

```bash
python3 -m skill_library.cli stats
python3 -m skill_library.cli list --source anthropics/skills
python3 -m skill_library.cli get <skill_id>
python3 -m skill_library.cli retag <skill_id> "pdf,reportlab,financial"
python3 -m skill_library.cli reclassify <skill_id> DOC-PROC
python3 -m skill_library.cli delete <skill_id> [--hard]
python3 -m skill_library.cli add /path/to/skill-dir --source custom
```

### Python API

```python
from skill_library import SkillLibrary

with SkillLibrary().open() as lib:    # 默认路径 skill_library/data/
    lib.add("/path/to/skill", source="anthropics")
    lib.add_batch("/path/to/skills/", source="anthropics")
    print(lib.stats())
```

### 导出到 consumer (mass pool)

```bash
# 同机 zero-config (assets-dir 默认 = producer 数据目录):
python3 -m skill_library.export \
    --dst <PATH_TO>/mass_library.db

# 跨机部署: rsync producer skills/ 树到 consumer 端, 再 export 指向 consumer 路径:
python3 -m skill_library.export \
    --dst /path/to/mass_library.db \
    --assets-dir /path/to/consumer/side

# 写 .refresh_endpoint sentinel (consumer `skill refresh` 零配置自动发现):
python3 -m skill_library.export \
    --refresh-endpoint http://producer-host:8765
```

输出:
- `mass_library.db` — body / embedding / frontmatter_json / path / is_always / requires_json 全字段
- `.stale` — consumer 下次 attach 时 consume 并删, 用作"新版可用"信号
- `.refresh_endpoint` (可选) — consumer `skill refresh` CLI auto-discover

consumer 端在 `~/.everclaw/config.json` 加(以下均为新代码 default, 显式
列出仅作参考):
```json
{
  "skill_forge": {
    "enabled": true,
    "mass_library_db": "<PATH_TO>/mass_library.db",
    "embedding_model": "embedding-our-new",
    "embedding_url": "http://<EMBEDDING_HOST>/new",
    "reranker_url":  "http://<EMBEDDING_HOST>/new",
    "embedding_api_key": "<EMBEDDING_API_KEY>",
    "reranker_api_key":  "<EMBEDDING_API_KEY>",
    "disable_always":    true,
    "injection_mode":    "full_body"
  }
}
```

**关键默认值说明** (2026-05-20 更新):

- `disable_always`: **默认 `true`**(2026-05-20 翻转, 原 `false`)
  - `true`(默认): `always: true` skill 既不进 always 块也不进 top-K, 防止
    mirror-side persona/mood 类 skill 双重注入 + 占用 top-K 名额
  - `false`: builtin (memory / self-improving) 等 always-true skill 自动注入
    (兼容旧行为, 但 top-K 会被污染)

- `injection_mode`: 默认 `"full_body"` (eval 实测 top-1 keyword 召回 ~0.80)
  - `"summary"` 是替代选项 (XML 目录 + agent 自己 read_file), token 更省但
    召回降到 ~0.62; 选 `summary` 时 agent 必须熟悉 read_file 流程

- `enabled`: 默认 `false` (主开关), 必须显式 `true` 才开 skill_forge 功能.

- `embedding_model`: 必须与 `mass_library_db` 的 embedding 模型对齐
  (`embedding-our-new` ↔ `mass_library.db`); 错配会导致召回失效.

### 主动 refresh

```bash
# 起 HTTP trigger (让 consumer CLI 远程触发)
python3 -m skill_library.scripts.refresh_server --port 8765 &

# cron 自动调度 (按 sources.yaml 里各源的 pull_cadence 只跑到期源)
0 3 * * *   python3 -m skill_library.scripts.refresh_loop

# 手动跑某个源
python3 -m skill_library.scripts.refresh_loop --source openclaw/skills --force
```

cron 跑 `refresh_loop` 会:
1. 读 `sources.yaml` + `data/refresh_state.json` 判 cadence
2. 该跑的: git pull → fast-batch ingest → rescan_quality → export_to_mass_library
   (写 mass_library.db + .stale)

### 近似去重 backfill

```bash
python3 -m skill_library.scripts.rescan_dedup --dry-run --report /tmp/rescan.json
python3 -m skill_library.scripts.rescan_dedup --report /tmp/rescan.json
# 选项: --min-cos 0.92 / --top-k 10 / --max-pairs 100 / --limit 500
```

### 质量打分 (LLM judge + 落库)

```bash
# 对未评分 / 新增 active skill 跑 LLM judge, 结果写 quality_judgments 表 + skills.quality_score
python3 -m skill_library.scripts.rescan_quality --workers 16 --report /tmp/q.json
```

### License 维护 (`license_audit.py`)

单点维护 source → license 映射, 一个子命令一步: refresh / build / validate / apply / stats.

数据流: `GitHub API (spdx_id)` → `source_license_report.csv` → `license_safe_sources.json` → `index.db skills.license / active`

```bash
# 1. 增量补全: 给 DB 里有 active skill 但 CSV 没记录的 source 查 GitHub license
GITHUB_TOKEN=ghp_xxx python3 -m skill_library.license_audit refresh
#    --source <one>   只查一个   --refresh-all   重查已在 CSV 的   --dry-run   预览

# 2. 重建白名单: CSV → license_safe_sources.json (只留 GREEN 类 source)
python3 -m skill_library.license_audit build [--dry-run]

# 3. 一致性校验: CSV ↔ JSON ↔ DB 交叉检查 (CI / 发布前跑)
python3 -m skill_library.license_audit validate

# 4. 回填 DB: source-level CSV cat 写进 skills.license (仅覆盖 junk, 不动已声明值)
python3 -m skill_library.license_audit apply [--dry-run]

# 5. 看分布: source-level (CSV) + skill-level (DB) license 分布 + GREEN/RED/YELLOW tag
python3 -m skill_library.license_audit stats
```

例行: 扩源后跑 `refresh && build && apply`; 发布前跑 `validate` 把关.
`validate` 当前会报 ~742 个 JSON↔CSV 不一致 (源 license 为 RED/NO_LICENSE
但 skill fm.license 自报 GREEN, 如 lobehub Proprietary), 属已知信号非 bug
(单文件自报 license 不覆盖整仓专有声明)。

### Source 增量 refresh (单源, 跳已在库的)

```bash
python3 -m skill_library.scripts.source_resync /path/to/source --source anthropics
```

### Export bundle

```bash
python3 -m skill_library.cli export --category DOC-PROC --out /tmp/doc.zip
python3 -m skill_library.cli export --source anthropics/skills --out /tmp/anth.zip
```

---

## 测试

9 个测试文件 (纯 `__main__` 可独立跑, 无 pytest 依赖):

```bash
# 从 skill_library 的上级目录运行;用 sys.path.append 避免目录内同名模块遮蔽 stdlib
cd <dir containing skill_library/>
for t in skill_library/tests/test_*.py; do
  python3 -c "import sys; sys.path.append('.'); import runpy; runpy.run_path('$t', run_name='__main__')"
done
```

| 测试 | 覆盖 |
|---|---|
| `test_parse.py` | SKILL.md frontmatter 解析 + validate |
| `test_safety.py` | safety 正则 + `is_blocked` |
| `test_license_filter.py` | GREEN/RED/YELLOW 判定 + `is_green_license` |
| `test_classify.py` | 16 类分类器各类命中 + tag 抽取 |
| `test_dedup_round_a.py` | canonical_name / LLMDupJudge cache / 跨 source 合并 |
| `test_quality_round_b.py` | LLMQualityJudge cache/clamp + compute_quality 权重 |
| `test_e2e.py` | CRUD + quality rejection + export_bundle + reindex |
| `test_iter3_upgrade_smoke.py` | 全量 export 路径 smoke (index.db → mass_library.db) |
| `test_producer_review_fixes.py` | 回归: faiss 对齐 / 短 batch 拒绝 / stale-dim drop / active anti-clobber / 增量 stats 不重复计 |

---

## 外部集成 (consumer = everclaw/skill_forge)

producer 出 `mass_library.db` + fs assets, consumer 用 `SqliteStore` attach 后
跑 dense retrieval.

三句话集成总结:
- producer/consumer 共用 SkillRouter remote endpoint, CPU-only 节点也能跑
- consumer 通过 `mass_library_db` 配置 attach SqliteStore, dense pool + local BM25
  pool 通过 RRF 融合; FS 上只保留 scripts/references 等附件, body 已在 DB
- 用户 `everclaw skill refresh <src>` 远程触发 producer git pull + ingest + export,
  零 config (mass_library.db 旁边自带 `.refresh_endpoint`)

---

## 范围

**不做**: skill 进化 / 执行时质量追踪(4 计数器)—— 那是运行时的事, 本库只管入库时的一次性筛选与评分. 新增源只改 `sources.yaml`(见 [源清单](#源清单--可复现性边界)), 无需改代码.
