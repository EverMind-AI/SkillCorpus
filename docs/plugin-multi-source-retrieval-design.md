# SkillCorpus 插件多来源检索：当前实现流程

> 本文描述当前代码已经实现的行为，不把规划中的能力写成已完成。
> 当前最终结果上限是 2；0、1、2 条都属于正常结果。

## 1. 当前接入的来源

| 来源 | 标识 | 默认权重 | 单来源上限 | 默认启用方式 |
|---|---|---:|---:|---|
| 用户本地 Skill | `local` | 1.00 | 2 | 存在有效目录时启用 |
| EverMind SkillHub | `hub` | 0.85 | 2 | 配置 `hubEndpoint` / `hub_endpoint` 后启用 |
| ClawHub | `clawhub` | 0.75 | 2 | OpenClaw、WorkBuddy 默认启用；Python engine 由宿主配置 |
| skillhub.cn | `skillhub_cn` | 0.75 | 2 | OpenClaw、WorkBuddy 默认启用；Python engine 由宿主配置 |

所有远程来源都接收原始查询。插件不自行把自然语言压缩成关键词。

## 2. 完整运行流程

```text
用户当前问题（original query）
        │
        ├─ [可选] Query Rewriter
        │      仅在配置了模型且 rewrite=true 时存在
        │      成功：产生 search query
        │      失败/超时：退回 original query
        │
        ▼
并行搜索所有已启用来源
        │
        ├─ local
        │    扫描/读取本地 SKILL.md 索引
        │    BM25 排序
        │    默认索引 name + name + description
        │
        ├─ EverMind SkillHub
        │    使用 search query 请求远端 Top K
        │    quality_score >= 0.45（字段存在时）
        │    score_safety >= 0.70（字段存在时）
        │    再用 original/search query 对
        │    name + description + tags 做关键词相关性过滤
        │    最多保留 2 条
        │
        ├─ ClawHub
        │    原样发送 search query
        │    请求 nonSuspiciousOnly=true
        │    拒绝 suspicious / blocked / 不可安装结果
        │    最多保留 2 条
        │
        └─ skillhub.cn
             原样发送 search query
             拒绝安全报告为 malicious / suspicious 的结果
             最多保留 2 条
        │
        ▼
单来源失败隔离
        任一来源超时、HTTP 错误或解析失败 → 该来源按空列表处理
        其他来源继续，不能阻断用户回合
        │
        ▼
Weighted RRF 融合
        只使用“来源内排名”，不横向比较各平台原始 score
        contribution = source_weight / (rrf_k + rank)
        默认按 name 去重（可配置 qualifiedId）
        同名项累加多个来源贡献
        │
        ├─ 有 LLM gate：先保留 gatePool（默认 10，实际最多 8）
        └─ 无 LLM gate：直接保留 topK（默认 2）
        │
        ▼
Hydrate 正文
        ├─ local：搜索结果已经包含正文
        ├─ EverMind：获取详情/正文；此时通常不下载附件包
        └─ ClawHub / skillhub.cn：下载 ZIP、校验并解压，读取 SKILL.md
             Marketplace 正文在 ZIP 内，因此必须在 gate 前取得
        │
        ├─ Marketplace 无正文/安装失败 → 淘汰该候选
        └─ 其他来源 hydrate 失败 → 保留已有信息或降级为空
        │
        ▼
解析本地文件引用
        对已经在磁盘上的 Skill 解析 {baseDir}、scripts/、references/ 等引用
        │
        ▼
[可选] LLM Gate
        仅在配置了模型且 gate 启用时存在
        使用 original query，而不是改写后的 query
        同时可读取宿主声明的 availableTools
        选择 0～2 条；失败时按 gate 自身的 fail-open 策略保留头部候选
        │
        ▼
截断到 topK（默认 2）
        │
        ▼
Materialise 最终远程 Skill
        ├─ EverMind：只为最终入选项下载/解压 bundle
        └─ Marketplace：复用 hydrate 阶段已经安装的 bundle
        │
        ▼
解析最终文件路径
        把相对脚本、references、assets 路径解析到实际 skill_dir
        PathGuard {{...}} 占位符属于独立 PR；默认关闭
        │
        ▼
渲染 # Skills 块
        每项包含 name、qualifiedId、正文；有目录时附 skill directory
        │
        ▼
宿主注入
        OpenClaw：before_prompt_build
        WorkBuddy：UserPromptSubmit stdout.additionalContext
        Hermes / Raven：各自的上下文适配层
```

## 3. 候选数量到底是多少

每个来源最多返回 2 条，四个来源理论上最多产生 8 条原始候选。

但后续数量取决于是否存在 LLM gate：

```text
无模型 / 无 gate：
  最多 8 条来源候选 → RRF 立即裁成 2 条 → hydrate → 最终 0～2 条

有模型且 gate 开启：
  最多 8 条来源候选 → RRF 保留最多 8 条 → hydrate → gate → 最终 0～2 条
```

因此“每个来源 Top 2”不等于插件必然下载 8 条，也不等于最终每个来源都占一个位置。
来源权重、来源内排名、同名去重和 gate 会共同决定最终结果。

## 4. 各来源的实际准入规则

### 4.1 Local

- 只搜索可读取的本地 `SKILL.md`；
- 默认使用 `name + name + description` 建索引；
- `indexBody=true` 时才把截断后的正文加入索引；
- BM25 负责来源内部排序；
- 本地权重默认最高（1.0），但不是硬性保送；
- 最多贡献 2 条。

### 4.2 EverMind

- 远端接口可能固定返回 Top K，因此客户端必须再过滤；
- `quality_score` 存在且低于 0.45时拒绝；
- `score_safety` 存在且低于 0.70 时拒绝；
- 使用查询在 `name + description + tags` 中做轻量关键词匹配；
- 小于 4 个有效词时至少命中 1 个，4 个及以上至少命中 2 个；
- 有核心词时必须至少命中一个核心词；
- 最多保留 2 条。

这层是对 EverMind 固定 Top K 的客户端保护，不修改发送给 Hub 的查询。

### 4.3 ClawHub

- 原始查询直接传给 ClawHub；
- 请求 `nonSuspiciousOnly=true`；
- visibility/installability 为 blocked 时拒绝；
- `isSuspicious=true` 时拒绝；
- 不把 ClawHub score 与其他来源 score 比较；
- 最多保留 2 条。

### 4.4 skillhub.cn

- 原始查询作为 `keyword` 发送；
- 使用 `sortBy=score&order=desc&page=1&pageSize=2`；
- 任一安全报告为 `malicious` 或 `suspicious` 时拒绝；
- 不把平台 score 当成跨来源相关性阈值；
- 最多保留 2 条。

## 5. 当前排序与去重

当前实现有两层不调用模型的确定性去重：

1. RRF 默认按 `qualifiedId` 融合；仍可显式配置按 `name` 融合，但不再默认把同名、不同内容的 Skill 误认为同一条；
2. hydrate 获得正文后，统一换行、移除行尾空白并 trim，再计算完整 body 的 SHA-256。哈希完全一致才合并，跨来源重复时优先保留本地副本。

空正文不参与内容去重；只要正文有实际字符差异就不会合并，因此这不是语义或模糊去重。去重发生在 RRF 候选池之后，重复项被移除时暂不从池外回填。

当前没有实现：

- upstream repo + path 身份去重；
- 跨来源语义近重复去重；
- `local_tie_margin`；
- 自动合并多个平台的许可证、热度和版本元数据。

这些能力如需加入，应单独设计和测试，不能把它们当作当前运行行为。

## 6. Gate 与“阈值”

当前没有一个跨来源统一数值阈值。原因是 BM25、平台热度、质量分和平台搜索分数
量纲不同，不能直接比较。

实际精度控制来自：

1. 各来源自身准入；
2. EverMind 客户端关键词过滤；
3. Weighted RRF；
4. 可选 LLM gate；
5. 最终 `topK=2`。

没有配置模型时不会调用 LLM gate。此时 RRF 头部结果直接进入 hydrate，最终仍可能是
0、1 或 2 条。

## 7. 正文、bundle 与缓存

### EverMind

- 搜索先返回元数据；
- gate 前获取正文详情；
- 最终入选后再安装 bundle（若需要）；
- 避免为未入选项下载完整附件包。

### ClawHub / skillhub.cn

Marketplace 搜索结果不带完整 `SKILL.md`，所以 hydrate 时会：

```text
下载 ZIP
  → 安全解压到 staging
  → 原子 rename 到缓存目录
  → 定位 bundle root
  → 读取 SKILL.md
  → 去掉 frontmatter，保留正文
```

TypeScript 适配器会给错误增加阶段前缀：

```text
download failed: ...
extract failed: ...
read skill failed: ...
```

如果缓存目录存在但缺少可读的 `SKILL.md`，TypeScript 适配器会删除该无效缓存，
下一回合重新下载。Python 适配器当前使用同样的原子 staging 解压，但错误文本和无效缓存
恢复尚未完全对齐 TypeScript。

缓存键当前是：

```text
source + owner（如果有）+ slug + version
```

## 8. 下载安全

当前 bundle 解压包含：

- staging 目录和原子 rename，避免半成品被当作缓存命中；
- 拒绝绝对路径和 `..` 路径穿越；
- 单文件上限 8 MiB；
- 解压总量上限 64 MiB；
- 只写允许后缀的普通文件；
- 不自动执行下载包中的脚本；
- 缺少有效 `SKILL.md` 时该候选不能进入最终结果。

## 9. 失败与超时

失败策略始终是 fail-open：丢失 Skill，不丢失用户回合。

### WorkBuddy

```text
宿主 hook 硬超时：10s
插件全局 timeoutMs：默认/最大 8s
Marketplace 搜索 timeout：最多 6.5s
bundle 下载：同时受下载超时和 8s 全局 AbortSignal 约束
```

### 其他宿主

- Python 默认 Marketplace 搜索 5s、下载 30s；
- Python 默认 EverMind 搜索 2s、bundle 下载 30s；
- OpenClaw 使用自身配置的 hub timeout；
- 单来源失败只贡献空列表。

## 10. WorkBuddy 可观测性

WorkBuddy 每轮向 `skillsearch.log` 写一条 JSONL，包含：

- 截断后的 prompt；
- 模型和 agent 类型；
- 实际注入的 Skill；
- 注入字符数；
- 总耗时；
- 每来源诊断；
- 顶层 hook 错误。

每来源诊断结构：

```typescript
interface SourceDiagnostic {
  source: string
  stage: "search" | "hydrate" | "materialise"
  elapsedMs: number
  hitCount?: number      // 仅 search
  succeeded?: boolean    // hydrate / materialise
  error?: string
}
```

Marketplace hydrate 错误的 `error` 前缀可进一步区分下载、解压和读取失败。
诊断回调自身失败不会影响检索。

## 11. Python 与 TypeScript 当前差异

| 项目 | Python engine | TypeScript engine |
|---|---|---|
| 并行多来源搜索 | 已实现 | 已实现 |
| Weighted RRF | 已实现 | 已实现 |
| 每来源最多 2 条 | 已实现 | 已实现 |
| EverMind 质量/安全/关键词过滤 | 已实现 | 已实现 |
| ClawHub / skillhub.cn | 已实现 | 已实现 |
| 原始 query 透传 | 已实现 | 已实现 |
| 结构化 source diagnostics | 日志为主 | 已实现，WorkBuddy 写 JSONL |
| 无效 Marketplace 缓存自动恢复 | 尚未完全对齐 | 已实现 |
| PathGuard 占位符 | 独立 PR | 独立 PR |

## 12. 当前没有实现的规划项

以下内容不属于当前发布行为：

- 内容哈希去重；
- 语义近重复去重；
- 跨平台 canonical identity 合并；
- 一个统一的数值相关性阈值；
- 自动覆盖或更新用户本地 Skill；
- 将多个平台元数据合并为一条完整 provenance；
- skills.sh 在线检索；
- 对所有宿主统一输出结构化诊断。

## 13. 当前验收结果

在当前分支上：

- TypeScript engine：43/43；
- WorkBuddy：28/28；
- OpenClaw：27/27；
- WorkBuddy、OpenClaw 类型检查与构建通过；
- 两个 npm 发布 tarball 校验通过；
- 构建不再引用仓库外不存在的 `tsconfig.base.json`。
