# SkillCorpus Plugins

[English](README.md) | 简体中文

**[SkillCorpus](https://github.com/EverMind-AI/SkillCorpus) 的官方宿主插件集：让你的 agent 每一轮都自动带上对的技能。** SkillCorpus Plugins 盯着用户刚说的话，从本地目录以及默认开启的三个远程来源中检索匹配的 `SKILL.md` 技能，在模型作答之前把技能正文放到它面前——不需要工具调用，模型也不需要事先知道任何技能的名字。

一个真实轮次，发生在 WorkBuddy 上：问 *"帮我生成一个二维码，内容是 https://evermind.ai，存到桌面"*。这台机器上没有任何二维码技能——但语料库里有，于是模型作答前，它的上下文多出：

```markdown
# Skills

### Skill: fireflylan-qr-code  [hub/24493cfe-c3cc-4dbe-9f6c-dfeac945b4c1]
**Skill directory**: `~/.workbuddy-ai/skillsearch-bundles/fireflylan-qr-code@<version>`
Relative refs (e.g. `references/x.md`, `./scripts/y.sh`) resolve under
this directory — use the absolute form for read_file / exec.

生成二维码/条形码，支持文本、URL、WiFi 配置等内容，可自定义尺寸、颜色并指定保存路径 …
```

技能自带的脚本已经解包在旁边；模型直接运行它，二维码落到桌面。没有检索的话，模型只能即兴发挥——`pip install qrcode`，然后碰运气。

技能来源可以是你自己的技能目录，也可以是默认开启的 EverMind SkillHub、ClawHub 和 skillhub.cn，所有来源融合进同一个排序。上面的二维码技能来自 [SkillHub](https://evermind.ai/skillhub)，即 [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus) 的托管端点。

## 安装——把这段话粘给你的 agent

每个受支持的宿主本身就是 agent，最快的安装方式是让它自己装。把下面这段粘进 WorkBuddy / Hermes / OpenClaw / DeepSeek Harness 的会话：

> 根据 https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/INSTALL.agent.md 安装 SkillCorpus Plugins。

那份剧本人类可读：先判断宿主、改配置前备份并给出 diff、装完验证 `# Skills` 块、逐步汇报——你可以[先读一遍](INSTALL.agent.md)再让它动手。

**手动安装**——按宿主选：

| 你的宿主 | 操作 | 详情 |
| --- | --- | --- |
| **WorkBuddy** | 构建 `plugin-workbuddy`、注册为 marketplace、启用——文件级步骤，agent 干最合适：把它 README 里的 prompt 粘给 WorkBuddy | [plugin-workbuddy](https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/plugin-workbuddy/README.md#install--paste-this-to-workbuddy) |
| **Hermes** | `pip install ./engine-python && cp -r plugin-hermes "$HERMES_HOME/plugins/skillsearch" && hermes memory setup` | [plugin-hermes](https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/plugin-hermes/README.md#install) |
| **OpenClaw** | `npm install --prefix plugin-openclaw && npm run --prefix plugin-openclaw build`，再往 `openclaw.json` 加两个键 | [plugin-openclaw](https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/plugin-openclaw/README.md#install) |
| **DeepSeek Harness** | 把 `engine-typescript/` 拷到 `packages/skill/skill-search/`，`cordis.yml` 加一行 | [engine-typescript](https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/engine-typescript/README.md#where-this-goes) |
| **Raven** | `pip install ./engine-python ./plugin-raven`——等 Raven 上游的 `context_segments` 插槽合并后生效；Raven 自带的检索今天照常工作 | [plugin-raven](https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/plugin-raven/README.md#install) |
| **其他任何宿主** | 旁边跑 `python -m skillsearch.adapters.http_server`，POST `/retrieve` | [engine-python](https://github.com/EverMind-AI/SkillCorpus/blob/main/skillcorpus_plugin/engine-python/README.md) |

## 30 秒尝鲜

不配目录服务、不配模型、零配置——一个本地技能加一个问题：

```bash
mkdir -p skills/pdf-tables && cat > skills/pdf-tables/SKILL.md <<'EOF'
---
name: pdf-tables
description: Extract tables from PDF documents, scanned or native, into CSV.
---
Use camelot for native PDFs; OCR scanned pages first.
EOF
```

问装好插件的 agent 怎么从 PDF 里提表格——上面那个块就会出现在它的上下文里。问它今天天气——什么都不会注入：匹配不到技能的查询就是检索为空。

## 检索到底带来什么，四个宿主实测

每个宿主一个案例，取自 QwenClawBench——同一个宿主跑自己的 agent，开与不开检索各一次。

| 宿主 | 任务 | 无技能 | 有技能 | 检索到 |
| --- | --- | --- | --- | --- |
| OpenClaw | 早报摘要技能 | 0.00 | **1.00** | `news-daily`、`news-express` |
| Hermes | 网关进程监控检查 | 0.17 | **0.92** | `openclaw-cli` |
| Raven | memos 发现与工作区初始化 | 0.00 | **0.74** | `caihhub-preference` |
| DeepSeek Harness | polygon 套利监控检查 | 0.50 | **0.83** | `defi-wallet-monitor` |

**OpenClaw —— 0.00 到 1.00。** 任务要求做一个早报摘要技能并把摘要发出去。没有检索时什么都没产出：没有技能文件、没有 frontmatter、没有消息。`news-daily` 和 `news-express` 正好补齐两半——摘要技能长什么样，以及发送它的调用——四个评分点全拿到。

**Hermes —— 0.17 到 0.92。** `openclaw-cli` 写明了怎么列 cron 任务、怎么读网关日志。从零分变满分的那五个点，恰好就是需要这些命令的：按简报执行、解释 cron 缺口、识别安全策略、完成日志分析、给出完整状态汇总。agent 缺的不是推理，是命令。

**Raven —— 0.00 到 0.74。** 工作区初始化，分数取决于跑完之后磁盘上留下了什么。`caihhub-preference` 描述了这个产品预期的目录布局，有了它，这一轮初始化了 git、写了身份文件、跟踪了工作区状态，文档里填的是真实内容而不是占位符。唯一还缺的一分是 memos 调查——那个技能没提。

**DeepSeek Harness —— 0.50 到 0.83。** 套利监控本来两种情况下都能跑；技能改变的是它的输出去了哪里。`defi-wallet-monitor` 规定了数据目录和日志约定，于是这次运行把产物写到了检查程序会去找的位置，而不是丢在脚本旁边。

## 你真正会碰的五个配置

每宿主的完整配置表在各插件 README 里；决定行为的是这七个（Python / TypeScript 写法）：

| 配置 | 默认 | 决定什么 |
| --- | --- | --- |
| `skills_dir` / `skillsDirs` | 宿主自己的技能目录 | 本地技能扫哪里。目录不存在 = 这个源就不存在。 |
| `hub_endpoint` / `hubEndpoint` | `https://skillhub.evermind.ai` | EverMind SkillHub；空值只关闭这个来源。 |
| `clawhub_endpoint` / `clawhubEndpoint` | `https://clawhub.ai` | ClawHub 检索；空值关闭。 |
| `skillhub_cn_endpoint` / `skillhubCnEndpoint` | `https://api.skillhub.cn` | skillhub.cn 检索；空值关闭。 |
| `model`（+ 宿主自己的路由字段） | *(空)* | 启用查询改写器和 gate。空 = 检索裸跑，按关键词排序注入。 |
| `top_k` / `topK` | 2 | 每轮最多注入的技能数。 |
| `gate` | *自动* | 用 LLM 剔除本 agent 跑不了的技能。自动 = 纯本地时**关**（自己的技能，排序够了），配了目录服务时**开**（野生技能需要把关）。可用 `true`/`false` 显式覆盖。 |

## 它花你什么代价

每轮最坏情况，全部有上限、全部 fail-open——慢或坏只让该轮少技能，绝不坏掉对话：

| 步骤 | 何时发生 | 上限 |
| --- | --- | --- |
| 本地 BM25 | 总是 | 毫秒级，进程内 |
| 远程目录检索 | 开启了任一远程来源 | 每请求 5s |
| 查询改写 | 配了 model | 一次小模型调用，5s |
| Gate | 配了 model +（自动）hub | 一次模型调用（≤10 候选），20s |
| Bundle 下载 | 选中了远程技能 | 30s，按版本缓存——之后同版本只是一次磁盘 stat |
| 注入文本 | 有匹配 | 0 到 `top_k` 条技能正文（开 gate 时通常 ≤2 条；一条正文常见 1–4k token） |

注入不进持久历史——每轮重建、随轮消失。

## 哪些数据会离开你的机器

如实交代，因为检索跑在你的对话上：

- **显式清空三个远程 endpoint 后的纯本地模式**——什么都不出去。扫描、排序、注入全在进程内。
- **默认安装**——EverMind SkillHub、ClawHub 与 skillhub.cn 默认开启，检索查询会发送给三个服务；将任一 endpoint 设为空字符串可单独关闭。未配置 `model` 时不会运行 LLM gate，但仍执行来源安全检查和 EverMind 关键词相关性过滤。
- **EverMind SkillHub**——选中技能的正文和 bundle 会从它下载。zip 解包有路径穿越拒绝、扩展名白名单、单文件 8 MiB / 整包 64 MiB 上限，缓存目录在所有被扫描技能目录之外（默认 `~/.workbuddy-ai/skillsearch-bundles`、`~/.skillsearch/hub`、`~/.openclaw/skillsearch-bundles` 或 `~/.dsh/skillsearch-bundles`）。
- **Marketplace 正文获取**——每个启用的 marketplace 最多会有两个候选在可选 LLM gate 之前下载并安全解包，因为这两个 API 通过 bundle 提供技能正文。被 gate 拒绝的候选可能仍留在缓存里，但插件不会自动执行它。
- **配了 `model`**——改写器看到你的消息（截断到 2,000 字符）；gate 看到你的消息加候选技能的名字、描述和 300 字符正文摘录。两者都发给**你自己配置的**模型，宿主有 provider 通道的走宿主通道。

下载的技能是第三方内容，模型会被指示遵循它。ClawHub 与 skillhub.cn 条目不在 SkillCorpus 的仓库许可证审计范围内，重新分发前应检查其上游条款。gate 能剔除依赖不可用工具或环境的技能，但只有配置了模型时才真正存在。

## 让你的技能可被搜到

检索索引的是**名字和描述**（有意为之——正文是停用词噪声的来源），所以 description 就是技能的搜索面。写触发场景，不要只写主题：

```yaml
# 搜得到：写明任务、输入和会触发它的问法
description: Extract tables from PDF documents, scanned or native, into CSV
  or JSON. Use when asked to "get the tables out of this PDF", "parse this
  invoice", or "convert a PDF report to a spreadsheet".

# 搜不到：几乎匹配不到用户会打的任何词
description: PDF helper.
```

完全没有 description 的技能只能靠名字被搜到。（需要旧行为可开 `index_body: true`。）

## 出问题时

- **什么都没注入**——按序查：技能目录存在且有 `SKILL.md`（frontmatter 有 `name:`/`description:`）吗？你的问法和描述有共同的信息词吗？gate 开着并且把它毙了吗（见下条）？
- **看 gate 到底怎么判的**——Python 宿主：设 `SKILLSEARCH_GATE_LOG_PATH=/tmp/gate.jsonl`，逐轮记录候选、模型的 plan、选中与被拒。
- **gate 太严**——它按设计偏 precision（"宁可不选也不选错"）。纯本地用就 `gate: false`，或调大 `max_select`。
- **hub 超时**——目录服务预算每请求 2s；`HTTP_PROXY` 里一个不通的代理会吃光预算。该源 fail-open：该轮没有远程技能，其余照常。
- **从早期版本升级**——三个行为变了：`memory=`/`agent_id` 已移除（改用 `extra_sources=`）、改写器不再有否决权、索引面默认只含名字+描述。详见 [`CHANGELOG.md`](CHANGELOG.md)。

## 卸载

安装的逆操作，没有暗桩：删插件目录 / pip 卸载、删你加的配置键、可选删上面列的 bundle 缓存目录。各插件 README 有精确路径，agent 剧本里也有[卸载节](INSTALL.agent.md#uninstall)——对 agent 说"卸载 skillsearch"同样管用。

## 工作原理

```
query
  ├─ rewrite          把消息清洗成检索查询            （可选）
  ├─ fan out          本地 BM25 · 远程目录 · 宿主自有源¹
  ├─ fuse             加权 RRF（K，默认 10），跨源去重
  ├─ hydrate          给只有元数据的候选取回正文
  ├─ resolve (local)  {baseDir} 与链接，先解析再交给 gate 判
  ├─ gate             剔除本 agent 跑不了的            （可选）
  └─ render           解包远程 bundle，解析其路径
→ 注入文本
```

¹ 内置两个源。宿主自己的源——自演化技能召回、私有库、第二个目录服务——按 `SkillSource` 协议实现（一个名字、一个权重、一个 `search`）传进来即可：Python `SkillSearch(extra_sources=[...])`，TypeScript `EngineParts.sources`。引擎不知道也不需要知道它是什么。

三条性质处处成立：**检索永不抛异常**（失败只让该轮少技能，不伤回答）；**融合按位次不按分数**（BM25 和目录分数的量纲才融得起来——这也是为什么精度过滤靠 gate 而不是分数阈值）；**能力即存在**（没配端点就没有远程源、没配模型就没有改写和 gate——配置永远说不出自相矛盾的话）。

每个插件把这条管道绑到宿主的同一个时刻——**用户消息之后、模型调用之前**：

| 插件 | 宿主 | 接入缝 | 宿主需要改动 |
| --- | --- | --- | --- |
| [`plugin-workbuddy/`](plugin-workbuddy) | WorkBuddy（5.3.13） | `UserPromptSubmit` 钩子——每轮一个进程 | 无 |
| [`plugin-hermes/`](plugin-hermes) | Hermes | memory provider 的 `prefetch` | 无 |
| [`plugin-openclaw/`](plugin-openclaw) | OpenClaw（向下验证至 2026.3.8） | `before_prompt_build` 钩子 | 无 |
| [`engine-typescript/`](engine-typescript) | DeepSeek Harness | `agent/pre-step` waterfall | 无 |
| [`plugin-raven/`](plugin-raven) | Raven | 认领 `skills` 阶段的 context segment | `context_segments` 插槽，上游合并中 |

## 远程技能库

`hub` 源使用三层 API——发现（元数据）、读取（`skill_md`）、下载（bundle zip），路径在 `/openapi/v1/skills` 下，按贵贱分级：每轮一次搜索，只给入围者补正文，只给 gate 幸存者下载。[SkillHub](https://evermind.ai/skillhub) 在 SkillCorpus 上提供这套 API：

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills?q=extract+tables+from+a+PDF"
```

也可以指向任何实现同一响应信封的自建服务。SkillCorpus 每条技能保留上游许可、每个来源仓库过了许可审计；检索在真实 agent 基准上的增益见 [SkillCorpus 论文](https://arxiv.org/abs/2607.15557)。

## 仓库结构

```
engine-python/       Python 3.11+ 的检索管道（另含面向任意宿主的 HTTP 适配器）
engine-typescript/   TypeScript / Node 18+ 的检索管道，兼 DeepSeek Harness 入口
plugin-workbuddy/    WorkBuddy 插件 · 基于 engine-typescript——每轮一个钩子进程
plugin-hermes/       Hermes 插件    · 基于 engine-python
plugin-raven/        Raven 插件     · 基于 engine-python
plugin-openclaw/     OpenClaw 插件  · 基于 engine-typescript
INSTALL.agent.md     你的 agent 执行的安装剧本
```

代码层面，引擎和各软件包沿用 `skillsearch` 这个名字——`import skillsearch`、插件 id、配置路径都是它；SkillCorpus Plugins 是产品名。两个引擎是同一设计的独立移植，不是共享内核加绑定。让它们保持一致的是测试而不是文档：prompt 字节一致、BM25/融合数值一致、`{baseDir}` 解析规则一致——[`parity.test.ts`](engine-typescript/tests/parity.test.ts) 用 Python 套件产出的数值钉住 TS 侧，CI 每次推送双跑。

## 参与开发

各目录测试相互独立：

```bash
pip install -e './engine-python[dev,hub]' -e ./plugin-raven
pytest engine-python/tests plugin-hermes/tests plugin-raven/tests -q
ruff check engine-python/skillsearch engine-python/tests plugin-raven
cd engine-typescript && npx tsx --test tests/parity.test.ts
cd plugin-openclaw   && npm install && npm run ci
```

测试套件只是宿主的替身，替身测不出"宿主拒绝加载插件"。两个检查补上一部分（CI 跑第一个）：

```bash
# Hermes 插件为脱离宿主运行声明了兜底基类；对着真实 ABC，漏实现的抽象方法才会真的失败：
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git
PYTHONPATH=hermes-agent pytest plugin-hermes/tests -q

# plugin-openclaw 手抄的宿主类型，对着原件编译验证：
git clone --depth 1 https://github.com/openclaw/openclaw.git ../openclaw-host
npm --prefix plugin-openclaw run check:host
```

其余只能靠真实宿主：这里每个插件都装进过宿主、由宿主自己的加载器端到端驱动——根目录的 `verify-raven.py` 把 Raven 路径一路驱动到宿主自己的 `ContextAssembler`。

**测试过的版本**：Python 3.11–3.13 · Node 18+（CI 用 22）· WorkBuddy 5.3.13 · hermes-agent `main` · OpenClaw（向下验证至 2026.3.8） · DeepSeek Harness workspace `main` · Raven 等上游插槽。

## EverMind agent 全家桶的一员

[Raven](https://github.com/EverMind-AI/Raven)（终端原生 agent harness）· [EverOS](https://github.com/EverMind-AI/EverOS)（记忆基座）· [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus)（社区技能语料库）。SkillCorpus Plugins 是把宿主——以上这些，以及和它们毫无关系的宿主——连到技能上的检索层。

## 引用

如果基于 SkillCorpus 的技能检索是你工作的一部分，请引用语料库论文：

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

## 许可证

本仓库与 Python 包为 Apache-2.0；TypeScript 包在其 `package.json` 中声明 MIT，与其所嵌入的 harness 保持一致。版本历史见 [`CHANGELOG.md`](CHANGELOG.md)。
