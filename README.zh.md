# SkillCorpus Plugins

[English](README.md) | 简体中文

**[SkillCorpus](https://github.com/EverMind-AI/SkillCorpus) 的官方宿主插件集：让你的 agent 每一轮都自动带上对的技能。** 内置的 `skillsearch` 引擎盯着用户刚说的话，从本地目录和可选的远程技能库里检索匹配的 `SKILL.md` 技能，在模型作答之前把技能正文放到它面前——不需要工具调用，模型也不需要事先知道任何技能的名字。

问一句 *"帮我把这张扫描发票里的表格提出来"*，模型的上下文里会多出：

```markdown
# Skills

### Skill: pdf-tables  [local/pdf-tables]
**Skill directory**: `/home/you/.openclaw/skills/pdf-tables`
Relative refs (e.g. `references/x.md`, `./scripts/y.sh`) resolve under
this directory — use the absolute form for read_file / exec.

用 camelot 处理原生 PDF 的表格；扫描件先跑 `scripts/ocr.sh` 做 OCR，然后 …
```

技能来源可以是你自己的技能目录、[SkillHub](https://evermind.ai/skillhub)（[SkillCorpus](https://github.com/EverMind-AI/SkillCorpus) 的托管端点，96,401 条经审核、许可宽松的社区技能），或两者融合进同一个排序。

## 安装——把这段话粘给你的 agent

每个受支持的宿主本身就是 agent，最快的安装方式是让它自己装。把下面这段粘进 Raven / Hermes / OpenClaw / DeepSeek Harness 的会话：

> 帮我安装 skillsearch 插件。克隆 https://gitlab.com/npc-work/aic/ai/skillsearch_plugins
> 并遵循仓库根目录的 `INSTALL.agent.md`：先判断你运行在哪个宿主里；
> 改任何配置前先备份并给我看 diff；装完用一个测试问题验证出现
> `# Skills` 块；最后汇报你改了什么和验证结果。

它执行的剧本就是 [`INSTALL.agent.md`](INSTALL.agent.md)——人类可读，你可以先审计 agent 将要做的每一步。

**手动安装**——按宿主选：

| 你的宿主 | 操作 | 详情 |
| --- | --- | --- |
| **Hermes** | `pip install ./engine-python && cp -r plugin-hermes "$HERMES_HOME/plugins/skillsearch" && hermes memory setup` | [plugin-hermes](plugin-hermes#install) |
| **OpenClaw** | `npm install --prefix plugin-openclaw && npm run --prefix plugin-openclaw build`，再往 `openclaw.json` 加两个键 | [plugin-openclaw](plugin-openclaw#install) |
| **DeepSeek Harness** | 把 `engine-typescript/` 拷到 `packages/skill/skill-search/`，`cordis.yml` 加一行 | [engine-typescript](engine-typescript#where-this-goes) |
| **Raven** | `pip install ./engine-python ./plugin-raven`——等 Raven 上游的 `context_segments` 插槽合并后生效；Raven 自带的检索今天照常工作 | [plugin-raven](plugin-raven#install) |
| **其他任何宿主** | 旁边跑 `python -m skillsearch.adapters.http_server`，POST `/retrieve` | [engine-python](engine-python/README.md) |

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

## 你真正会碰的五个配置

每宿主的完整配置表在各插件 README 里；决定行为的是这五个（Python / TypeScript 写法）：

| 配置 | 默认 | 决定什么 |
| --- | --- | --- |
| `skills_dir` / `skillsDirs` | 宿主自己的技能目录 | 本地技能扫哪里。目录不存在 = 这个源就不存在。 |
| `hub_endpoint` / `hubEndpoint` | *(空)* | 远程技能库，如 `https://skillhub.evermind.ai`。空 = 纯本地。**设置前先读[哪些数据会离开你的机器](#哪些数据会离开你的机器)。** |
| `model`（+ 宿主自己的路由字段） | *(空)* | 启用查询改写器和 gate。空 = 检索裸跑，按关键词排序注入。 |
| `top_k` / `topK` | 5 | 每轮最多注入的技能数。 |
| `gate` | *自动* | 用 LLM 剔除本 agent 跑不了的技能。自动 = 纯本地时**关**（自己的技能，排序够了），配了目录服务时**开**（野生技能需要把关）。可用 `true`/`false` 显式覆盖。 |

## 它花你什么代价

每轮最坏情况，全部有上限、全部 fail-open——慢或坏只让该轮少技能，绝不坏掉对话：

| 步骤 | 何时发生 | 上限 |
| --- | --- | --- |
| 本地 BM25 | 总是 | 毫秒级，进程内 |
| 目录检索 + 正文补全 | 配了 hub | 每请求 2s |
| 查询改写 | 配了 model | 一次小模型调用，5s |
| Gate | 配了 model +（自动）hub | 一次模型调用（≤10 候选），20s |
| Bundle 下载 | 选中了远程技能 | 30s，按版本缓存——之后同版本只是一次磁盘 stat |
| 注入文本 | 有匹配 | 0 到 `top_k` 条技能正文（开 gate 时通常 ≤2 条；一条正文常见 1–4k token） |

注入不进持久历史——每轮重建、随轮消失。

## 哪些数据会离开你的机器

如实交代，因为检索跑在你的对话上：

- **纯本地（默认）**——什么都不出去。扫描、排序、注入全在进程内。
- **配了 `hub_endpoint`**——每个检索轮次，检索查询（你的消息，或模型清洗后的改写）会发给那个目录服务；选中技能的正文和 bundle 会从它下载。zip 解包有路径穿越拒绝、扩展名白名单、单文件 8 MiB / 整包 64 MiB 上限，缓存目录在所有被扫描技能目录之外（默认 `~/.skillsearch/hub`、`~/.openclaw/skillsearch-bundles` 或 `~/.dsh/skillsearch-bundles`）。
- **配了 `model`**——改写器看到你的消息（截断到 2000 字符）；gate 看到你的消息加候选技能的名字、描述和 300 字符正文摘录。两者都发给**你自己配置的**模型，宿主有 provider 通道的走宿主通道。

下载的技能是第三方内容，模型会被指示遵循它。gate 存在的意义就是剔除那些假定了你的 agent 没有的工具或环境的技能——这也是配了目录服务时它默认开启的原因。

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
  ├─ fuse             加权 RRF（K = 60），跨源去重
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
plugin-hermes/       Hermes 插件    · 基于 engine-python
plugin-raven/        Raven 插件     · 基于 engine-python
plugin-openclaw/     OpenClaw 插件  · 基于 engine-typescript
INSTALL.agent.md     你的 agent 执行的安装剧本
```

两个引擎是同一设计的独立移植，不是共享内核加绑定。让它们保持一致的是测试而不是文档：prompt 字节一致、BM25/融合数值一致、`{baseDir}` 解析规则一致——[`parity.test.ts`](engine-typescript/tests/parity.test.ts) 用 Python 套件产出的数值钉住 TS 侧，CI 每次推送双跑。

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

**测试过的版本**：Python 3.11–3.13 · Node 18+（CI 用 22）· hermes-agent `main` · OpenClaw（向下验证至 2026.3.8） · DeepSeek Harness workspace `main` · Raven 等上游插槽。

## EverMind agent 全家桶的一员

[Raven](https://github.com/EverMind-AI/Raven)（终端原生 agent harness）· [EverOS](https://github.com/EverMind-AI/EverOS)（记忆基座）· [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus)（社区技能语料库）。skillsearch 是把宿主——以上这些，以及和它们毫无关系的宿主——连到技能上的检索层。

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
