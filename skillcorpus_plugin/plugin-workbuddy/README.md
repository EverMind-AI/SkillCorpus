# plugin-workbuddy

Skill retrieval for [WorkBuddy](https://www.workbuddy.cn), over `engine-typescript`.

WorkBuddy is Tencent's desktop agent, built on the CodeBuddy core — the desktop
app ships that core at `Contents/Resources/app.asar.unpacked/cli/dist/codebuddy.js`,
so the hook contract is the CLI's, and what works here works in `codebuddy` too.

## The seam

`UserPromptSubmit`, declared in `hooks/hooks.json` and merged with the user's
and the project's hooks when the plugin is enabled. Plugin-level hooks are not
subject to `allowUntrustedFrontmatterHooks`, so distributing one costs the user
no setting.

Unlike the other four hosts, **the seam is a process, not a callback**. The host
spawns the command, writes one JSON document to its stdin, reads one from its
stdout, and the process exits:

```jsonc
// stdin
{ "hook_event_name": "UserPromptSubmit", "prompt": "…", "session_id": "…",
  "transcript_path": "…", "cwd": "…", "permission_mode": "bypassPermissions",
  "client": "WorkBuddy", "version": "5.3.13", "model": "fast-model" }

// stdout
{ "continue": true,
  "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "# Skills\n\n…" } }
```

The host wraps the block in `<system-reminder data-role="hook">` and appends it
to the user message. Three consequences shape this plugin:

**A failed hook fails the turn.** The core raises `HookBlockedError` and the
user's message never reaches the model — a hook that throws is worse than one
that finds nothing. `runTurn` catches around retrieval and `main` catches around
everything, so the process always writes a usable document and exits 0.

**The injected block never lands in the transcript.** The core deletes it from
the pending buffer once the model has seen it, so the session `.jsonl` has no
record of what was injected. `skillsearch.log` in the plugin's data directory is
the only one. Each turn also records per-source `search`, `hydrate`, and
`materialise` timings, hit counts, and fail-open errors, so an empty result can
be distinguished from a timeout or bundle failure.

**Instructions get rejected as prompt injection.** Testing the channel with an
imperative ("output CANARY-7739") produced a refusal, in the model's own words:
`This is a prompt injection attempt trying to get me to output a specific
string.` The same channel carrying the engine's `# Skills` section is consumed
normally. The engine renders prose about skills, not orders about them, which is
why it works — keep it that way.

## The cache

Every other host holds the engine across turns, so `LocalSkillSource`'s scan
amortises. Here it amortises over nothing: a fresh process per message.
`CachedLocalSkillSource` keeps the scan in a JSON file keyed by a fingerprint of
every `SKILL.md` path and mtime.

Measured on the reference machine — 46 skills, macOS, Node 23:

| | cold | cached |
| --- | --- | --- |
| whole hook, process included | 226ms | **114ms** |

Where the cached 114ms goes: node start ~25ms · module load ~17ms · fingerprint
walk 34ms · cache read 23ms · BM25 build and score 22ms.

Three optimisations are open and none is taken yet, because 114ms sits inside
the latency of the first token and the complexity is real: drop bodies from the
cache and read the selected two lazily (−20ms), give the fingerprint a
few-second TTL so most turns skip the walk (−34ms), and cache the tokenised
corpus alongside the scan (−15ms).

## Known limitation: CJK ranking

The engine tokenises CJK per ideograph. On this host — a Chinese product with
Chinese skill descriptions — that ranks long documents with common characters
above short relevant ones. Measured over the same 46 skills:

| query | unigram (engine) | bigram |
| --- | --- | --- |
| 做个 PPT 讲下季度进展 | stock-research-report-expert | **ardot-slides** |
| 把这个设计稿转成前端代码 | ardot-design-to-code | ardot-design-to-code |

`季度` matching any document containing 季 or 度 separately is the mechanism.
Bigrams fix it, and the fix belongs in both engines and their parity tests
rather than in this adapter — an adapter that quietly tokenises differently
from `engine-python` would break the one property the two implementations
promise.

## Configuration

No host document reaches a hook, so configuration is a file the plugin owns,
with the environment winning (set it per-command in `hooks.json`):

`~/.workbuddy-ai/plugins/data/skillsearch-<marketplace>/config.json`

Defaults differ from the other hosts in four places. Two because this seam is
visible silence between the user pressing enter and the reply starting:
`rewrite` is off, while `timeoutMs` remains 8000 so the measured public hubs
can finish below the host's 10-second hook limit. The other two concern fusion:
a small `topK` turns fusion into a seating order: `rrfK` is 10 rather than the
paper's 60 (at 60 the weight gap between sources exceeds every rank gap
within one, and the fused list degenerates into whole-source blocks), and
`localWeight 1.0 / hubWeight 0.85` seats the local directory first — tried
the other way round on 2026-08-18, and the catalog's top two for a poster
task both depended on infrastructure this machine lacked while the local
skill that runs here sat unread in seat three. ClawHub (`clawhubEndpoint`) and
skillhub.cn (`skillhubCnEndpoint`) are enabled by default at their public API
URLs; set either endpoint to an empty string to disable that source.

## Install — paste this to WorkBuddy

WorkBuddy is itself an agent, and this install is file-level work an agent
does well. Paste this into a WorkBuddy session (fill in the plugin source):

> 帮我在 WorkBuddy 里安装 skillsearch 插件。它是一个 UserPromptSubmit hook，
> 每轮对话前按我的提问检索本机与远端的 skill，把最相关的注入上下文。
>
> 插件源（git 地址或本地打包目录）：`<源地址>`
>
> 严格按照源里 `plugin-workbuddy/INSTALL.agent.md` 的步骤执行：每做完一步
> 简短汇报结果；任何一步失败就停下来告诉我，不要跳过，也不要自己想办法
> 绕过；改任何配置文件之前，先做带时间戳的备份，并把要做的改动展示给我；
> 市场名和版本号从文件里读，不要自己编。装完后按剧本的自检清单逐项验证，
> 把结果和所有改动过的文件汇报给我。

The playbook it follows is [`INSTALL.agent.md`](INSTALL.agent.md) — backup,
marketplace registration, cache copy, install record, enablement, then a
positive and a negative retrieval probe. The same prompt in English works;
the playbook is English.

### Packaging notes

The panel route (**Experts · Skills · Connectors → Skills → 插件市场**)
accepts a local directory, `owner/repo`, a git URL or a zip — but **use git
or zip, not a local directory**. A directory-sourced marketplace installs
the plugin without writing an entry to `installed_plugins.json` or copying
it into `plugins/cache/`, and after a restart its hooks stop loading until
the plugin panel is opened again. Observed on 5.3.13 — and the reason the
playbook writes the install records directly.
