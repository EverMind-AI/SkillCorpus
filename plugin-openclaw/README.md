# skillsearch for OpenClaw

Skill retrieval on `before_prompt_build`. Every turn, this searches a local
skills directory and an optional remote catalog against what the user just
wrote, narrows the result with a model, and returns the matching skill
bodies for the host to prepend.

The engine is the TypeScript implementation in [`../typescript`](../typescript),
bundled into `dist/index.js` at build time. One engine, two TypeScript hosts:
this one and the DeepSeek Harness plugin.

## Install

```bash
npm install --prefix plugin-openclaw
npm run --prefix plugin-openclaw build
# then point OpenClaw at the built plugin, and enable it:
#   plugins.entries.skillsearch.enabled = true
```

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
| `hubEndpoint` | `SKILLSEARCH_HUB_ENDPOINT` | — | Remote catalog base URL; empty means local only |
| `hubApiKey` | `SKILLSEARCH_HUB_API_KEY` | — | Bearer token for that catalog |
| `model` | `SKILLSEARCH_MODEL` | — | Model for the rewriter and the gate |
| `modelBaseUrl` | `SKILLSEARCH_MODEL_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `modelApiKey` | `SKILLSEARCH_MODEL_API_KEY` | — | Credential for that endpoint |
| `topK` | `SKILLSEARCH_TOP_K` | `5` | Upper bound on skills injected per turn |
| `gatePool` | `SKILLSEARCH_GATE_POOL` | `10` | Candidates the gate judges |
| `maxSelect` | `SKILLSEARCH_MAX_SELECT` | `2` | Upper bound on what the gate keeps |
| `timeoutMs` | `SKILLSEARCH_TIMEOUT_MS` | `8000` | Deadline for one retrieval |
| `availableTools` | `SKILLSEARCH_AVAILABLE_TOOLS` | `[]` | See below |

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
- **The remote catalog's bundles are never downloaded.** A catalog skill
  contributes its body only, so one whose procedure depends on its own
  scripts will describe files that are not on disk.
- **The local scan is cached for the process lifetime.** A `SKILL.md`
  written after the first retrieval is invisible until restart.
- **One route serves both model calls,** though the rewriter's job is far
  cheaper than the gate's.
