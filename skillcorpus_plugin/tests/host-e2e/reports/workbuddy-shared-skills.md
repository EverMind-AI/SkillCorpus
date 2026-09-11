# SkillCorpus · WorkBuddy Host E2E — feat/shared-skills

> WorkBuddy 是唯一没有无头路径的宿主，所以这是一份人工报告，形态和
> `shared-skills.md` 里那些脚本产出的行不同：那边每一行背后有 transcript
> 和 `--dump` 的完整记录，这边是在 WorkBuddy 界面里操作观察到的。
> 用例编号对应 [`../cases.md`](../cases.md) 的 P1–P6 与 S1–S9；
> 手工步骤在同一文件的 `## WorkBuddy, by hand`。

## Environment

| Field | Value |
|---|---|
| Plugin version | 0.3.0 (marketplace 字段仍为 0.3.0；dist 已含 shared-skills 代码) |
| SkillCorpus commit | `ca324890b08d5082f57c87eeb35d91b84051b32d` |
| Host | WorkBuddy 5.4.7 (`com.tencent.workbuddy.mac`) |
| OS | macOS 15.1 (Darwin 24.1.0), Apple M4 Pro (arm64) |
| Node | v22.22.2 (WorkBuddy bundled) |
| Install method | 标准 marketplace（CLI: `plugin marketplace add <feat/shared-skills.zip> -n skillcorpus` → `plugin install skillsearch@skillcorpus`） |
| Remote sources | P1–P4 期间按文档关闭；共享库探测期间开启 |
| Model | WorkBuddy 默认 `balanced-model` |
| Test date / tester | 2026-09-09 / Claude Code（辅助）+ 人工在 WorkBuddy 内交互 |

> 注意：本地已装版本与分支 marketplace 版本号同为 0.3.0（分支未 bump），普通 `plugin update` 不会拉取新 dist。测试通过「卸载 + 删缓存 + 重新 install」强制拿到含 shared-skills 代码的 dist。

---

## P1–P6 · 单宿主检索（两种模式）

### on_demand（默认）

| Case | Verdict | 证据 |
|---|---|---|
| P1 正向检索 | **INCONCLUSIVE** | hook 不注入（`injected_chars:0`）✅；`tools/list → ['skill_search']` ✅；但模型从自己知识回答整套流程、未调工具 ❌。引擎直驱命中 `pdf-tables`、返回含 `Vireo-CSV-3`+`Okapi Ledger` ✅ |
| P2 内部约定触发 | **FAIL** | 模型未调 `skill_search`（index-cache 无更新）；回复编造不存在的 skill 名 `scanned-pdf-invoice-ocr`；无 sentinel facts |
| P3 零注入 | **PASS** | 模型未调工具 ✅；hook `injected_chars:0` ✅ |
| P4 typo (`mode:"atuo"`) | **PASS** | fallback 到 `on_demand`（工具仍 offered）；日志点名坏值 `unknown_mode:"atuo"` / `mode_used:"on_demand"` |

### auto

| Case | Verdict | 证据 |
|---|---|---|
| P1 正向检索 | **PASS** | hook 自动注入 `pdf-tables`（`injected_chars:450`）；注入文本含 `Vireo-CSV-3`+`Okapi Ledger` ✅；`tools/list → []`（工具面空）✅；无工具调用 ✅ |
| P2 内部约定触发 | **PASS** | hook 自动注入（`injected_chars:450`）；回复含 `Vireo-CSV-3`+`Okapi Ledger` ✅ |
| P3 零注入 | **PASS** | hook `injected_chars:0` ✅；无工具可调 |
| P4 typo | **PASS** | 与 on_demand 同（typo 与当前模式无关）|

### auto 第 1 点回归测试（cases.md 明确标记的已知 bug）

- MCP 进程在 `auto` 下 **alive** 且 `tools/list` 返回空 `[]` —— 未复现 `e337cfa` 时代「auto 下 MCP 启动即退」的问题。**PASS**

---

## S1–S9 · 共享技能库

共享根目录 `~/.evermind-skillsearch/`（默认，`SKILLSEARCH_HOME` 可移动）。

| # | Claim | Verdict | 证据 |
|---|---|---|---|
| S1 | 检索的 skill 出现在 `<shared root>/skills/` | **PASS** | 远程命中后 `skills/` 持久保留 `hub__<slug>__<hash>` 目录，含 `SKILL.md` + 脚本 |
| S2 | 下次 turn 本地找到、恰好一次、不重启 | not executed | — |
| S3 | agent B 检索 agent A 安装的 | **BLOCKED** | 需第二个 agent（本机仅有 WorkBuddy）|
| S4 | A 自己目录的 skill 在 B 可检索 | **BLOCKED** | 需第二个 agent |
| S5 | 手工放入共享目录、下次 turn 无重启命中 | not executed | fixture `rotate-signing-keys` 已放入 `skills/`，未在宿主内验证命中 |
| S6 | `enabled:false` 隐藏且重启后保持 | not executed | — |
| S7 | 移除留 `uninstalled.log` 记录 | not executed | — |
| S8 | 失败更新留下旧版本可用 | **BLOCKED** | 需死端点模拟（且需版本号 bump）|
| S9 | 损坏 registry 只损共享、不损检索 | not executed | — |

### 已确认的共享库核心机制（通过宿主内 + 直接驱动验证）

1. `registry.json` 记录 workbuddy host：`{"id":"workbuddy","dir":"~/.workbuddy-ai/skills","enabled":true}` ✅
2. 远程命中的 skill 持久保留到 `skills/`（不再用完即弃）✅
3. `.skillsearch-origin.json` 元数据完整（origin/source/slug/sha256/installed_at）✅
4. 宿主内触发（发 "天气"）→ 新远程 skill（global-weather、current-weather）写入共享库 ✅
5. `shareSkills:false` → 远程 skill 回到 throwaway、不写共享库 ✅
6. `SKILLSEARCH_HOME` 覆盖根目录（代码确认）✅

---

## 关键发现

1. **on_demand 的「模型主动调用工具」在 WorkBuddy 默认模型上不可靠。** P1 从知识回答（INCONCLUSIVE），P2 问内部约定时编造不存在的 skill 名（FAIL）。工具描述里「搜库优先于回答不知道」未能在该模型上生效。对照：中文显式指令「请调用 skill_search」能触发。

2. **P2 失败形态与文档历史记录不同。** 文档记录的早期失败是「I don't know」；WorkBuddy 本次是**编造 skill 名**（`scanned-pdf-invoice-ocr`），比「I don't know」更隐蔽（表面像「知道有个 skill」）。

3. **版本号未 bump。** 分支 `marketplace.json`/`plugin.json` 仍为 0.3.0，但 dist 已含 0.4.0 未发布的 shared-skills 代码。走 marketplace 的 host（如 WorkBuddy）会因版本号相同而**不触发更新**——这是发布流程隐患，需在正式发布前 bump。

   **已修**（本报告之后）：全部 14 处声明统一 bump 到 `0.4.0`——六个包、四份宿主清单、marketplace 条目、两个运行时常量、根 `__version__` 和 Hermes 的 `plugin.yaml`。`verify_release_versions.py` 现在对 0.4.0 通过；本报告里那个「卸载 + 删缓存 + 重装」的绕行不再需要。

4. **auto 通道完整可用。** hook 自动注入绕过「模型是否主动调工具」，P1/P2 均 PASS，sentinel facts 正确出现在注入文本与回复中。

5. **共享库机制完整工作**（见上表），跨 host 共享（S3/S4）因缺少第二 agent 未验证。

---

## 结论

- **插件代码无需修改**：两种模式 wiring、共享库核心机制均按设计工作。
- **on_demand 在 WorkBuddy 上的工具触发是真实风险点**，需团队决策：改进工具描述/触发，或对 WorkBuddy 推荐 `auto` 模式。
- **发布前需处理**：版本号 bump（否则 marketplace 更新不生效）。
- 本报告的缺口（S2/S5/S6/S7/S9 未执行、S3/S4/S8 BLOCKED）均已显式列出。

---

## 这份报告之后做了什么

| 报告里的发现 | 处理 |
|---|---|
| 版本号未 bump（发现 3） | **已修**：14 处声明统一到 0.4.0，校验器通过 |
| P2 编造 skill 名（发现 2） | **已记入用例**：`cases.md` 的 P2 现在写明这第二种失败形态，并说明为什么断言只看 fixture 事实和工具调用、不看回复"像不像找到了" |
| on_demand 触发不可靠（发现 1） | **未改代码**。是改工具描述还是对 WorkBuddy 推荐 `auto`，属于产品决策；报告已把风险写清楚 |
| S3/S4 BLOCKED（缺第二个 agent） | 脚本侧已在别的机器上覆盖：`e2e_shared.py` 用 Raven + OpenClaw 2.0 验过 S3/S4，见 `shared-skills.md`。WorkBuddy 参与的跨宿主组合仍未验 |
| S2/S5/S6/S7/S9 未执行 | 这五条**单宿主就能验**，不需要第二个 agent。`cases.md` 的手工步骤现已逐条标注哪些要第二个 agent、哪些不要，避免下次再整片留空 |
| S8 BLOCKED | 同样已在脚本侧对真 catalogue 验过（`e2e_install.py`），WorkBuddy 上不必重复 |
