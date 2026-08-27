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
`materialise` timings, search hit counts, hydrate/materialise success, and fail-open errors,
so an empty result can be distinguished from a timeout or bundle failure.

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

## Configuration

No host document reaches a hook, so configuration is a file the plugin owns,
with the environment winning (set it per-command in `hooks.json`):

`~/.workbuddy-ai/plugins/data/skillsearch-<marketplace>/config.json`

The marketplace name is read from the installed hook path. Source-checkout or
non-standard launchers use the neutral `skillcorpus-marketplace` fallback.
`SKILLSEARCH_MARKETPLACE` explicitly overrides the name, while
`SKILLSEARCH_DATA_DIR` overrides the complete state directory. Existing
installs keep their current directory because their marketplace remains present
in the cache path.

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
skill that runs here sat unread in seat three. EverMind SkillHub
(`hubEndpoint`, `https://skillhub.evermind.ai`), ClawHub (`clawhubEndpoint`),
and skillhub.cn (`skillhubCnEndpoint`) are enabled by default at their public
API URLs; set any endpoint to an empty string to disable that source.

## Install

Use WorkBuddy's standard marketplace flow:

1. Open **Experts · Skills · Connectors → Skills → Plugin Marketplace**.
2. Add `EverMind-AI/SkillCorpus` (or its git URL/release zip) as a marketplace
   source. Do not use a local directory; it is not persistent across restart on
   WorkBuddy 5.3.13.
3. Confirm `CODEBUDDY_DISABLE_EXTENDED_PLUGIN_HOOKS` is not `1` in the
   environment that launches WorkBuddy; that value disables every extended
   plugin hook. Clear it from the launcher and fully restart before continuing.
4. Open the `skillcorpus` marketplace and install and enable **Skill Search**.
5. Fully quit and reopen WorkBuddy.

Do not manually edit WorkBuddy's internal JSON files or copy the plugin into
its cache. The root `.codebuddy-plugin/marketplace.json` is the discovery entry,
and WorkBuddy owns the install records.

To delegate the installation to WorkBuddy, paste:

> Install Skill Search from the `EverMind-AI/SkillCorpus` marketplace using
> WorkBuddy's standard plugin marketplace. Do not manually edit
> `settings.json`, `installed_plugins.json`, or `known_marketplaces.json`, and
> do not copy files into the plugin cache. Fully restart WorkBuddy, then follow
> `skillcorpus_plugin/plugin-workbuddy/INSTALL.agent.md` to verify one real hook
> turn and its Skill Search log entry. A live `.in_use/<pid>` marker is optional
> diagnostic evidence because some WorkBuddy builds do not create one. Stop and
> report any discovery failure instead of bypassing it.

Installation is not complete until the restart and hook checks in
[`INSTALL.agent.md`](INSTALL.agent.md) pass. A plugin card alone does not prove
the host loaded its hook.
