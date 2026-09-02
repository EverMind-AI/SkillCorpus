# skillsearch for OpenClaw 2.0

Skill retrieval for OpenClaw 2.0 (2026.8.1 and newer). The default
`on_demand` mode registers a `skill_search` tool and lets the agent decide when
to retrieve. `mode: auto` runs the same retrieval pipeline through the active
context engine and assembles matching skills on every turn. The modes are
exclusive.

OpenClaw 2.0 needs this separate package because its 1.x
`before_prompt_build` injection path no longer delivers plugin context. The
engine is the TypeScript implementation in
[`../engine-typescript`](../engine-typescript), bundled into `dist/index.js`.

## Install

```bash
npm install --prefix plugin-openclaw2
npm run --prefix plugin-openclaw2 build     # -> dist/index.js
```

For the default `on_demand` mode, add the plugin root and entry to
`~/.openclaw/openclaw.json`:

```jsonc
{
  "plugins": {
    "load": { "paths": ["/abs/path/to/plugin-openclaw2"] },
    "entries": {
      "skillsearch": {
        "enabled": true,
        "config": {
          "mode": "on_demand",
          "skillsDirs": ["~/.openclaw/skills"]
        }
      }
    }
  }
}
```

`load.paths` points to the directory containing `openclaw.plugin.json`, not
to `dist/index.js`. In this mode the plugin registers `skill_search` and leaves
the exclusive context-engine slot free.

Starting from the configuration above, set `mode` to `auto`, grant the plugin
conversation and prompt-injection access, and select it for the context-engine
slot. Keep the same `plugins.load.paths` entry:

```jsonc
{
  "plugins": {
    "entries": {
      "skillsearch": {
        "enabled": true,
        "hooks": {
          "allowConversationAccess": true,
          "allowPromptInjection": true
        },
        "config": {
          "mode": "auto",
          "skillsDirs": ["~/.openclaw/skills"]
        }
      }
    },
    "slots": { "contextEngine": "skillsearch" }
  }
}
```

The two grants belong under `plugins.entries.skillsearch`; a top-level `hooks`
block is invalid. `auto` is inert if either grant or
`plugins.slots.contextEngine` is missing. Restart the gateway/app after
changing the package or config.

## Configuration

Set under `plugins.entries.skillsearch.config`, or through the environment,
which wins so a credential never has to be copied into a file.

| Key | Env | Default | Purpose |
|---|---|---|---|
| `mode` | `SKILLSEARCH_MODE` | `on_demand` | `on_demand` registers `skill_search`; `auto` occupies the context-engine slot and retrieves every turn |
| `skillsDirs` | `SKILLSEARCH_SKILLS_DIRS` | `["~/.openclaw/skills"]` | Directories scanned for `SKILL.md` |
| `hubEndpoint` | `SKILLSEARCH_HUB_ENDPOINT` | `https://skillhub.evermind.ai` | EverMind SkillHub; empty disables this source only |
| `clawhubEndpoint` | `SKILLSEARCH_CLAWHUB_ENDPOINT` | `https://clawhub.ai` | ClawHub API; empty disables it |
| `skillhubCnEndpoint` | `SKILLSEARCH_SKILLHUB_CN_ENDPOINT` | `https://api.skillhub.cn` | skillhub.cn API; empty disables it |
| `hubApiKey` | `SKILLSEARCH_HUB_API_KEY` | — | Bearer token for that catalog |
| `model` | `SKILLSEARCH_MODEL` | — | Model for the rewriter and the gate |
| `modelBaseUrl` | `SKILLSEARCH_MODEL_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `modelApiKey` | `SKILLSEARCH_MODEL_API_KEY` | — | Credential for that endpoint |
| `topK` | `SKILLSEARCH_TOP_K` | `2` | Upper bound on skills delivered per retrieval |
| `gatePool` | `SKILLSEARCH_GATE_POOL` | `10` | Candidates the gate judges |
| `maxSelect` | `SKILLSEARCH_MAX_SELECT` | `2` | Upper bound on what the gate keeps |
| `timeoutMs` | `SKILLSEARCH_TIMEOUT_MS` | `8000` | Deadline for one retrieval |
| `availableTools` | `SKILLSEARCH_AVAILABLE_TOOLS` | `[]` | See below |

The default is the directory the host already conventions, so a deployment
that keeps its skills there configures nothing. A directory that does not
exist is not an error: the local source simply is not there, and retrieval
runs on whatever else is configured — or, with nothing else, stays off.

`gate` is unset by default, which means *on when a catalog is configured*.
The gate is told to reject when unsure: a directory you curate is better
served by ranking and `top_k`, especially now that an unrelated query
returns nothing from it at all, while a catalog of unvetted skills needs
the check for whether this agent even has the tools a skill calls for. Set
it explicitly either way and that wins.

`skillsDirs: []` turns the local source off. With no local directory and no
catalog there is nothing to search: the plugin logs that retrieval is off and
registers neither a tool nor a context engine.

**Configure a `model`.** Fusion ranks by position, so every source's best hit
reaches the shortlist however weakly it matched, and the gate is the only
step that removes those. Without one, an unrelated turn still gets a skill.

**`availableTools` is a fallback for the gate.** In `auto`, OpenClaw 2.0 reports the live tool surface to the context engine, so the gate can reject skills that need unavailable tools. The configured list is used only when the host reports none. In `on_demand`, it supplies that environment information to the retrieval tool. Left empty, the gate still judges relevance.

## What the model sees

In `on_demand`, the agent receives a `skill_search` tool. A successful call
returns the selected skill sections, including each name, qualified id, local
directory when present, and body with frontmatter stripped.

In `auto`, the active context engine appends the same rendered `# Skills` block
during `assemble()`. It reports `ownsCompaction: false`, so OpenClaw keeps
control of transcript storage and compaction.

## What retrieval costs

When retrieval runs, it can make up to two auxiliary model calls and deliver between zero and `maxSelect` skill bodies. `auto` runs retrieval every turn; `on_demand` runs it only when the agent calls `skill_search`. With no model configured, neither auxiliary call runs.

Every failure is open. A broken catalog, unreachable endpoint, or timeout returns no skills; it does not fail the agent turn or tool protocol.

## Tests

```bash
npm run --prefix plugin-openclaw2 ci     # typecheck, tests, build
```

The suite runs without an OpenClaw checkout: it drives `register` with a
fake `api` and points the pipeline at a local HTTP server standing in for
the model provider, so the request the plugin sends, the reply it parses and
the selection the gate makes are all the shipping code.

Real-host coverage lives in [`../tests/host-e2e`](../tests/host-e2e). Run the OpenClaw driver with `--generation 2`; it verifies both modes through the host transcript rather than calling the engine directly.

## Known limitations

- **The context-engine slot is exclusive in `auto`.** Selecting skillsearch replaces any other configured context engine. `on_demand` does not occupy the slot.
- **Automatic mode needs explicit capability grants.** Both hook grants and `plugins.slots.contextEngine` must be present; otherwise retrieval is inert.
- **Bundles land in `bundleCacheDir`, outside every scanned directory.**
  Default `~/.openclaw/skillsearch-bundles`. Inside a scanned directory, a
  downloaded skill would reappear as a local one on the next scan.
- **The local scan is cached for the process lifetime.** A `SKILL.md`
  written after the first retrieval is invisible until restart.
- **One route serves both model calls,** though the rewriter's job is far
  cheaper than the gate's.
