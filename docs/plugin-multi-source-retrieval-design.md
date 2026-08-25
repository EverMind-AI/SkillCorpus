# SkillCorpus 插件多来源 Skill 检索方案

## 1. 目标

插件同时检索用户本地 Skill、EverMind SkillHub、ClawHub 和
skillhub.cn，在保证本地 Skill 优先、远程来源可控和响应时间稳定的前提下，
每次最多向 Agent 提供 2 个真正相关的 Skill。

Top 2 是上限，不是必须凑满的数量。没有候选达到要求时返回 0 个。

## 2. 来源与候选数量

| 来源 | 每次保留数量 | 默认状态 |
|---|---:|---|
| 用户本地 Skill | 0～2 | 开启 |
| EverMind SkillHub | 0～2 | 开启 |
| ClawHub | 0～2 | 默认开启 |
| skillhub.cn | 0～2 | 默认开启 |

四个来源最多产生 8 个候选，再统一排序并输出最多 2 个。

第三方来源默认开启，用户可以在配置中单独关闭。安装或首次启用插件时，应明确提示
搜索内容会发送给已开启的对应服务商。

skills.sh 暂时继续作为离线数据发现来源，不进入第一版插件的在线检索。

## 3. 总体流程

```text
用户问题
  → 并行查询所有已开启来源
  → 各来源执行自己的准入规则
  → EverMind 结果执行关键词相关性过滤
  → 每个来源保留 0～2 个候选
  → 转换为统一候选格式
  → 按来源身份去重
  → 获取远程 Skill 正文
  → 统一相关性排序和最低相关性过滤
  → 按正文内容再次去重
  → 本地 Skill 优先规则
  → 最多返回 2 个 Skill
```

某个来源超时或失败时，只跳过该来源，不得影响整个 Agent 回合。

## 4. 统一候选格式

每个来源适配器把自己的响应转换为统一结构：

```typescript
interface SkillCandidate {
  source: "local" | "evermind" | "clawhub" | "skillhub_cn"
  sourceId: string
  sourceRank: number
  sourceScore?: number

  name: string
  description: string
  body?: string
  version?: string
  author?: string

  canonicalUrl?: string
  upstreamUrl?: string
  upstreamCommit?: string
  upstreamPath?: string
  downloadUrl?: string
  contentHash?: string

  qualityScore?: number
  safetyScore?: number
  license?: string
  verified?: boolean
  suspicious?: boolean
  localPath?: string
}
```

不同平台的原始分数量纲不同，只能用于该平台内部筛选，不能直接横向比较。

## 5. 各来源准入规则

各来源先执行自己的准入规则，再保留最多 2 个。达不到要求时可以返回
1 个或 0 个，不能为了凑数量放入低质量结果。

### 5.1 用户本地 Skill

准入条件：

- `SKILL.md` 可以正常解析；
- 未被用户禁用；
- 本地搜索结果达到该本地库的最低要求；
- 最多保留前 2 个。

本地检索分数受用户 Skill 数量和内容影响，阈值应可配置，并通过实际运行数据校准。

### 5.2 EverMind SkillHub

初始配置：

```yaml
max_candidates: 2
min_quality: 0.45
min_safety: 0.70
```

质量分只用于过滤语料质量，不能代替查询相关性判断。被下架、被阻止或安全分不足的
Skill 直接拒绝。

当前公开搜索接口会返回固定数量的 Top K，并且不返回相关性分数。即使查询完全无关，
也可能返回最接近但实际不适用的候选。因此插件先增加一个轻量关键词匹配函数：

```typescript
function checkEverMindRelevance(query: string, skill: SkillCandidate): {
  passed: boolean
  matchedTerms: string[]
  requiredMatched: boolean
  matchRatio: number
}
```

处理规则：

1. 对查询做小写化、去标点和空白归一化；
2. 去掉中英文常见停用词；
3. 规范化常见别名和词形，例如 `k8s → kubernetes`、`PR → pull request`、
   `PPT → powerpoint`、`transcription → transcribe`；
4. 把技术名词和任务对象作为核心词，例如 `postgresql`、`pdf`、`github`；
5. 在候选的 `name + description + tags` 中匹配；
6. 查询存在核心词时，至少命中一个核心词；
7. 有效关键词不超过 3 个时至少命中 1 个，4 个及以上时至少命中 2 个；
8. 未通过时淘汰，不用返回数量补齐。

关键词过滤是公开接口提供相关性分数前的临时保护。后续接口若返回可信的相关性分数，
可在保持函数接口不变的情况下替换内部判断。

### 5.3 ClawHub

初始配置：

```yaml
max_candidates: 2
non_suspicious_only: true
```

明确标记为可疑、不可安装、不可见或被阻止的候选，无论分数多高都直接拒绝。

ClawHub 对无意义查询会返回空结果，首版信任其搜索端的相关性过滤并直接取前 2 条，
不再用原始 `score` 二次过滤。实测相关结果的原始分数可能低至约 2000，设置 5000
会误删 PostgreSQL 优化等有效候选。

### 5.4 skillhub.cn

已经确认以下公开链路可以获取详情和 ZIP：

```text
GET https://api.skillhub.cn/api/skills
GET https://api.skillhub.cn/api/v1/skills/{slug}
GET https://api.skillhub.cn/api/v1/download?slug={slug}
```

已确认正式搜索参数为：

```text
GET https://api.skillhub.cn/api/skills?keyword={query}&sortBy=score&order=desc&page=1&pageSize=2
```

skillhub.cn 对无意义查询返回空结果。首版信任其搜索端的相关性过滤，直接取前 2 条，
不再用原始 `score` 二次过滤。该分数同时受到热度等因素影响，不能作为统一的相关性
阈值，也不能与其他来源的分数横向比较。

接入必须通过以下测试：

- PDF 查询返回 PDF 相关 Skill；
- Kubernetes 查询返回 Kubernetes 相关 Skill；
- 无意义查询返回空结果或低于阈值的结果；
- 公开详情和下载无需登录；
- 明确为恶意的安全报告会被拒绝。

默认配置：

```yaml
enabled: true
max_candidates: 2
reject_malicious: true
```

## 6. 去重规则

去重分为两个阶段。

### 6.1 获取正文前的来源身份去重

按照以下优先级生成身份键：

1. 上游仓库地址和仓库内 Skill 路径；
2. GitHub `owner/repo` 和 Skill slug；
3. 平台提供的 canonical 或 external reference；
4. `source + sourceId` 作为兜底。

同一 Skill 被多个远程平台返回时，只从一个来源获取正文，其他平台的质量、安全、
热度和版本信息合并到同一个候选中。

### 6.2 获取正文后的内容去重

对规范化后的 `SKILL.md` 计算 SHA-256。存在附件时，可以把排序后的附件路径和附件
哈希一起计入。

以下动态内容不参与哈希：

- 平台生成的元数据文件；
- 下载或安装时间；
- 当前机器的绝对路径；
- 平台统计数字。

内容哈希相同的候选合并为一个。第一版不根据语义相似直接删除 Skill，避免错误合并
功能相近但用途不同的 Skill。

## 7. 统一排序与最终阈值

来源初筛后的最多 8 个候选进入统一排序。统一排序只比较候选与当前用户问题的相关性，
不直接比较各平台的原始分数。

排序后应用统一最低相关性阈值：

```text
达到阈值 → 保留
低于阈值 → 淘汰
```

最终返回：

```text
通过 2 个 → 返回 2 个
通过 1 个 → 返回 1 个
全部未通过 → 返回 0 个
```

统一阈值必须可配置，并根据真实查询与用户是否实际采用 Skill 的数据持续校准。

## 8. 本地 Skill 优先规则

本地 Skill 与远程 Skill 候选数量相同，但在最终选择时拥有执行优先权。

规则如下：

1. 本地与远程属于同一身份或内容哈希时，使用本地正文和附件；
2. 本地与远程最终分数差不超过 `local_tie_margin` 时，优先本地；
3. 远程候选明显更相关时，允许远程候选胜出；
4. 检索过程中不得自动覆盖或更新用户本地 Skill；
5. 发现远程新版本时只记录 `updateAvailable`，由用户决定是否更新。

初始配置：

```yaml
local_tie_margin: 0.05
```

## 9. 正文获取与缓存

只有通过来源准入的候选才获取正文。每次最多获取 6 个远程正文。

缓存键：

```text
source + sourceId + version/contentHash
```

缓存命中时不重复下载和解压。平台提供版本号、ETag、Last-Modified 或内容哈希时，
优先用于缓存失效判断。

## 10. 下载安全

所有远程包必须执行：

- 请求超时和正文大小限制；
- 压缩包大小、解压后大小和文件数量限制；
- 拒绝绝对路径和 `..` 路径穿越；
- 拒绝不安全软链接、设备文件和特殊文件；
- 解压后必须存在有效 `SKILL.md`；
- 拒绝平台明确标记为恶意或被阻止的包；
- 不自动执行下载包中的脚本。

第三方平台的安全报告只能作为输入，不能替代插件自己的包安全检查。

## 11. 初始配置

```yaml
retrieval:
  max_results: 2
  per_source_max_candidates: 2
  source_timeout_ms: 5000
  body_timeout_ms: 30000
  local_tie_margin: 0.05
  unified_relevance_threshold: null

sources:
  - type: local
    enabled: true

  - type: evermind
    enabled: true
    endpoint: https://skillhub.evermind.ai
    min_quality: 0.45
    min_safety: 0.70

  - type: clawhub
    enabled: true
    endpoint: https://clawhub.ai
    non_suspicious_only: true

  - type: skillhub_cn
    enabled: true
    endpoint: https://api.skillhub.cn
    reject_malicious: true
```

原有单一 `hub_endpoint` 配置继续兼容，并在内部转换为一个 EverMind source。

## 12. 运行统计

每轮记录以下统计，但默认不记录完整用户问题和 Skill 正文：

```text
各来源是否成功、失败或超时
各来源原始返回数和阈值过滤数
各来源最终候选数
身份去重数和内容去重数
远程正文下载数和缓存命中数
统一相关性过滤数
最终结果数量和来源分布
本地优先规则触发次数
各阶段耗时
```

这些数据用于调整各来源阈值、统一相关性阈值和超时时间。

## 13. 实施状态（已完成）

### 核心能力

- 统一候选结构；
- 保持本地和 EverMind 现有功能；
- 增加 ClawHub 适配器；
- 实现每来源 `0～2` 准入；
- 实现身份去重、内容去重和缓存；
- 实现本地优先和运行统计。

### 第三方来源与宿主接入

- 接入并验证 skillhub.cn 搜索协议；
- 增加 skillhub.cn 适配器；
- 根据真实数据校准各来源阈值；
- 在四个宿主插件中加入默认开启的第三方来源、独立关闭开关和查询共享提示。

## 14. 验收标准

- 每个开启的来源每轮贡献 0～2 个候选；
- 统一排序的候选总数不超过 8 个，LLM gate 最终选择 0～2 个；
- 某个来源超时不会导致整个回合失败；
- 无意义查询可以返回 0 个 Skill；
- 同一个 Skill 的多个平台副本只获取一次正文；
- 已安装的本地副本优先于相同远程副本；
- 本地与远程近似同分时选择本地；
- 明显更相关的远程 Skill 可以胜过无关本地 Skill；
- 被阻止、可疑、格式错误或压缩包不安全的 Skill 不得进入最终结果；
- 检索过程不得静默修改用户本地 Skill。
