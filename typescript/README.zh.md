# @deepseek-ai/dsh-skill-search

[English](README.md) | 中文

逐轮技能检索。每一轮，本插件用用户刚写下的内容去检索本地目录和可选的远程目录服务，并在模型被调用之前把匹配到的技能正文放到它面前。

`dsh-tool-skill` 用相反的方式解决同一问题：它发布一份包含全部技能的目录，让模型按名称加载。二者是替代关系——同时运行会把同一批技能发布两次，一次作为工具 schema，一次作为注入文本。挂载本插件的部署应禁用 `dsh-tool-skill`（以及任何发布同一技能目录的其他插件）。

## 放在哪里

这是一个 DeepSeek Harness 包，不是独立的 npm 包：依赖是 `workspace:^`，`tsconfig.json` 的 references 也只在一个确切位置解析得开。把该目录复制到 harness 检出的 `packages/skill/skill-search/`，在 `tsconfig.host.json` 里加一行 `{ "path": "./packages/skill/skill-search" }`，然后 `pnpm install`。

## 流水线

1. **改写** —— 一次模型调用同时判断该轮是否需要技能，并为检索改写查询，剔除路径、id 和样板文字。若判定为 `need_retrieval: false`，下面全部跳过。
2. **扇出** —— 每个来源在各自的尺度上对改写后的查询排序。抛异常或超时的来源不贡献结果，其余来源照常回答。
3. **融合** —— 加权 Reciprocal Rank Fusion（K = 60）**按位次**合并各列表，因为本地 BM25 分数和目录质量分不是可比的数值。同名命中收敛为分数较高的那一份。
4. **补全** —— 仅带目录元数据进来的候选会被取回正文，每条通过融合的候选一次请求。
5. **Gate** —— 一次模型调用最多选出 `maxSelect` 条，并被要求宁可一条不选，也不要选不相关的。
6. **注入** —— 选中结果渲染后追加到该 step 的消息中。

Gate 不是优化项。融合按位次排序，因此每个来源的最佳命中无论匹配多弱都会进入短名单：没有 gate 时，"今天天气怎么样"会注入本地目录排在第一的那条技能。gate 也是唯一能剔除**本 agent 无法执行**的技能的步骤——正文假定了某个厂商 API、`{baseDir}` 占位符或斜杠命令分发器——这是任何排序函数都看不出来的。不配置 `provider`/`model` 时，检索以不过滤方式运行，按排名注入前 `topK` 条。

检索从不抛出。失败的来源、无法解析的 gate 回复、缓慢的目录服务，都只让该轮失去技能，模型照常回答。

## 配置

| 键 | 默认值 | 含义 |
| --- | --- | --- |
| `skillsDirs` | `['.dsh/skills']` | 扫描 `SKILL.md` 的目录，最深 5 层。相对路径相对 cwd 解析。 |
| `hubEndpoint` | `''` | 远程目录服务基址。为空则禁用远程来源。 |
| `hubApiKey` | `''` | 目录服务的 Bearer token。 |
| `hubTimeoutMs` | `2000` | 目录服务的单请求超时。 |
| `weightLocal` | `1.0` | 本地技能的融合权重。 |
| `weightHub` | `0.85` | 目录技能的融合权重——本地技能经过筛选，因此排在前面。 |
| `topK` | `5` | 每轮注入技能数的上限。 |
| `gatePool` | `10` | 交给 gate 判断的候选数。越大可供其剔除的越多。 |
| `maxSelect` | `2` | gate 保留数量的上限。 |
| `provider` / `model` | `''` | 改写器和 gate 使用的路由。两者要么都配，要么都不配；只配一个会在加载时失败。 |
| `rewriteTimeoutMs` | `5000` | 改写的超时，是该轮第一次模型调用。收得紧是因为它排在其余一切之前；超时则直接检索原始 query。 |
| `gateTimeoutMs` | `20000` | gate 的超时，它运行在用户看到回复之前。 |

既无 `skillsDirs` 又无 `hubEndpoint` 时无可检索：插件记录一条"检索已关闭"日志，且不注册任何 hook。

## 替换目录方案挂载

base bundle 交付的是目录方案。在后续 patch 层里替换：禁用发布目录的那个工具，加上本插件这一行。

```yaml
- id: tool-skill
  name: '@deepseek-ai/dsh-tool-skill'
  disabled: true

- id: skill-search
  name: '@deepseek-ai/dsh-skill-search'
  config:
    skillsDirs: ['.dsh/skills']
    provider: deepseek-official
    model: deepseek-v4-flash
```

如果还有别的东西读 `ctx.skills`——用户显式调用路径、skill badge、Web UI——就保留 `skill` 和 `skill-filesystem`。本插件不使用该注册表，它自己扫描 `skillsDirs`，因此两者共存也不会把任何东西发布两次。确认没有其他消费方时，一并禁用即可。

## 扩展点

`SkillSource` 是接入点。实现 `name`、`weight` 和 `search(query, options, k)` 即可加入融合；随包交付的两个（`LocalSkillSource`、`HubSkillSource`）并不享有特殊地位。`SkillSearchEngine` 也被导出，供只要流水线、不要 `agent/pre-step` 绑定的消费方使用——`hits()` 返回记录，`render()` 生成文本。

## Model Experience

### 每个 step 之前注入的检索结果

#### What the model sees

追加到该 step 消息中的一条额外 user message，包含标题 `# Skills`，其后每条选中技能一节：`### Skill: <name>  [<qualified id>]`，然后是剥离 frontmatter 后的 `SKILL.md` 正文。文件位于磁盘上的技能还会被标出其目录，因为正文里写的 `scripts/x.sh` 否则会相对 agent 的 cwd 解析。这些正文是由一次模型调用选出的第三方内容，是模型被期望遵循的指令，消息的 `skill-search` source 以 `form: 'instructions'` 连同被注入的 id 一并记录这一点。

##### Verbatim directory note, emitted for an on-disk skill

```markdown
**Skill directory**: `<absolute path>`
Relative refs (e.g. `references/x.md`, `./scripts/y.sh`) resolve under this directory — use the absolute form for read_file / exec.
```

#### Token effect

有条件且不保留。一轮注入 0 到 `maxSelect` 条完整技能正文；改写器判定该轮不需要技能、或 gate 一条都没选中时，则完全不注入。注入内容按 step 构建，不写入持久历史，因此不会跨轮累积。每个执行检索的轮次有两次会话之外的辅助模型调用——改写器，以及对 `gatePool` 条候选（每条带 300 字符正文摘录）的 gate。

#### KV Cache effect

替换式。注入内容追加在派生历史之后，其文本随选择结果变化，因此从该消息往后的后缀在两个 step 之间不可复用；它之前的前缀不受影响。什么都没注入的一轮，其请求与不挂载本插件时完全相同。

## Known Limitations and Deferred Work

- **远程目录的 bundle 从不下载** —— 目录技能只贡献其 `skill_md` 正文。如果某技能的流程依赖它自己的脚本或参考文件，它描述的文件并不在磁盘上，而 gate 的环境检查是唯一能过滤掉这类技能的东西。
- **检索不作为 session event 记录** —— 被注入的消息在 `source.skillIds` 中携带 id，但"考虑过并被否决了哪些候选"以及"改写器判定了什么"只存在于模型调用中。需要审计某条技能**为何**被注入或未被注入的部署，必须自行补上该事件。
- **本地扫描在进程生命周期内缓存** —— `LocalSkillSource.invalidate()` 存在但无人调用，因此首次检索之后新写入的 `SKILL.md` 在重启前不可见。把它接到文件监听上，推迟到确有部署需要在活动会话中编辑技能时再做。
- **改写器和 gate 共用一条路由** —— 一组 `provider`/`model` 同时服务两者，尽管改写器的活儿便宜得多。拆分推迟到有部署证明该成本差异确实重要时再做。
