# skillsearch for OpenClaw

Skill retrieval on `before_prompt_build`. Every turn, this searches a local
skills directory and an optional remote catalog against what the user just
wrote, narrows the result with a model, and returns the matching skill
bodies for the host to prepend.

The engine is the TypeScript implementation in [`../engine-typescript`](../engine-typescript),
bundled into `dist/index.js` at build time. One engine, two TypeScript hosts:
this one and the DeepSeek Harness plugin.

## Install

```bash
npm install --prefix plugin-openclaw
npm run --prefix plugin-openclaw build     # -> dist/index.js
```

Then two keys in OpenClaw's config — one names the directory, the other
configures the plugin the manifest inside it declares:

```jsonc
{
  "plugins": {
    "load": { "paths": ["/abs/path/to/plugin-openclaw"] },
    "entries": {
      "skillsearch": {
        "enabled": true,
        "config": { "skillsDirs": ["~/.openclaw/skills"], "model": "gpt-4o-mini" }
      }
    }
  }
}
```

`load.paths` points at the plugin **root** — the directory holding
`openclaw.plugin.json` — not at the built file. The host reads that
manifest to validate `entries.skillsearch.config` without executing any
plugin code, and only then imports `dist/index.js`.

The bundle imports nothing from the host: the entry is a plain
`{ id, name, register }` object, and the host types it compiles against are
copied into `src/openclaw-types.ts`. So it loads on any OpenClaw that reads
a default-exported plugin definition — verified on `2026.3.8`, which
predates the `definePluginEntry` helper that newer plugins use. Importing
that helper would have made this plugin fail to load on anything older
than `2026.6.10` and bought nothing: it stamps the same fields.

## Configuration

Set under `plugins.entries.skillsearch.config`, or through the environment,
which wins so a credential never has to be copied into a file.

| Key | Env | Default | Purpose |
|---|---|---|---|
| `skillsDirs` | `SKILLSEARCH_SKILLS_DIRS` | `["~/.openclaw/skills"]` | Directories scanned for `SKILL.md` |
| `hubEndpoint` | `SKILLSEARCH_HUB_ENDPOINT` | `https://skillhub.evermind.ai` | EverMind SkillHub; empty disables this source only |
| `clawhubEndpoint` | `SKILLSEARCH_CLAWHUB_ENDPOINT` | `https://clawhub.ai` | ClawHub API; empty disables it |
| `skillhubCnEndpoint` | `SKILLSEARCH_SKILLHUB_CN_ENDPOINT` | `https://api.skillhub.cn` | skillhub.cn API; empty disables it |
| `hubApiKey` | `SKILLSEARCH_HUB_API_KEY` | — | Bearer token for that catalog |
| `model` | `SKILLSEARCH_MODEL` | — | Model for the rewriter and the gate |
| `modelBaseUrl` | `SKILLSEARCH_MODEL_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `modelApiKey` | `SKILLSEARCH_MODEL_API_KEY` | — | Credential for that endpoint |
| `topK` | `SKILLSEARCH_TOP_K` | `2` | Upper bound on skills injected per turn |
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
registers no hook at all.

**Configure a `model`.** Fusion ranks by position, so every source's best hit
reaches the shortlist however weakly it matched, and the gate is the only
step that removes those. Without one, an unrelated turn still gets a skill.

**`availableTools` restores half the gate.** The gate normally drops a skill
whose workflow needs a tool the agent lacks — a vendor API, a `{baseDir}`
placeholder, a slash-command dispatcher. OpenClaw does not report the tool
set to a hook (`PluginHookAgentContext` carries the session and channel, not
the tools), so that check runs only when a deployment states its own tools
here. Left empty, the gate still judges relevance.

## What the model sees

A `prependContext` block: the heading `# Skills`, then one section per
selected skill — `### Skill: <name>  [<qualified id>]`, the skill's
directory when its files are on disk, and the body with frontmatter
stripped.

`prependContext`, not `prependSystemContext`: the system-context fields
exist for static guidance a provider can cache, and this selection changes
every turn. The host concatenates each plugin's contribution, so this adds
to whatever else contributed rather than replacing it.

## What it costs a turn

Two auxiliary model calls when retrieval runs, and between zero and
`maxSelect` skill bodies. A turn the rewriter judges needs no skills makes
one call and injects nothing.

Every failure is open. The hook runs between the user's message and the
model's reply, so a broken catalog, an unreachable endpoint or a timeout
returns no block and the turn proceeds without skills.

## Tests

```bash
npm run --prefix plugin-openclaw ci     # typecheck, tests, build
```

The suite runs without an OpenClaw checkout: it drives `register` with a
fake `api` and points the pipeline at a local HTTP server standing in for
the model provider, so the request the plugin sends, the reply it parses and
the selection the gate makes are all the shipping code.

What that suite cannot check is whether the host still looks like the copy in
`src/openclaw-types.ts`. Against a checkout, this does:

```bash
npm run --prefix plugin-openclaw check:host    # needs ../openclaw-host/src
```

It compiles the copied types against the host's own declarations and fails
when either gains or loses a field. Verified to fail on a deliberate drift
before being relied on.

## Known limitations

- **The gate's environment check needs `availableTools`.** Explained above;
  it is host-imposed, not a choice this plugin makes.
- **Bundles land in `bundleCacheDir`, outside every scanned directory.**
  Default `~/.openclaw/skillsearch-bundles`. Inside a scanned directory, a
  downloaded skill would reappear as a local one on the next scan.
- **The local scan is cached for the process lifetime.** A `SKILL.md`
  written after the first retrieval is invisible until restart.
- **One route serves both model calls,** though the rewriter's job is far
  cheaper than the gate's.
