# Adapting skillsearch to a host

The engine answers one question — *given what the user just said, what
skills should the model see?* — and returns the text to inject. An
adapter's whole job is to call it at the right moment and hand the result
to its host.

```python
search = SkillSearch(config)
block = await search.retrieve("extract tables from a scanned PDF")
```

Everything below is about where that call goes in each host, and how that
host's configuration reaches `SearchConfig`.

## What differs between hosts

| | Raven | Hermes | OpenClaw |
|---|---|---|---|
| Language | Python | Python | TypeScript |
| Manifest | `raven-plugin.toml` | `plugin.yaml` | `openclaw.plugin.json` |
| Injection point | `SegmentBuilder.build(ctx) -> Segment` | `prefetch(query, session_id) -> str` | `api.on("before_prompt_build", …)` |
| Extension category | context segment | context engine | memory slot |
| Config location | plugin slice in TOML | `$HERMES_HOME/*.json` | env vars in `configSchema` |
| Can import this package | yes | yes | **no** |

The first two embed the engine. OpenClaw cannot — a TypeScript plugin
cannot import a Python package — so it calls the HTTP adapter instead.

## Raven

Retrieval claims the `skills` stage in Raven's ordered prompt assembly.

### Prerequisite: Raven must support context segments

**Raven's plugin system has to know about `context_segments` before this
plugin can attach.** Upstream Raven contributes two kinds — memory backends
and tools — and neither is a place to hang a prompt-assembly stage. The
missing piece is four changes in Raven itself:

| File | Change |
|---|---|
| `raven/plugin/manifest.py` | a `ContextSegmentContribution` kind, with a `replaces` field |
| `raven/plugin/registry.py` | register / look up / build those segments |
| `raven/plugin/context.py` | `ServiceLocator.get_tool_definitions`, so the gate can see the agent's tools |
| `raven/cli/_plugin_stack.py` | `build_plugin_segments()`, and `build_context_engine` taking them |

Without them, `pip install skillsearch` attaches nothing: Raven discovers
the entry point, finds a contribution kind it does not recognise, and
ignores it — no error, no `# Skills` block. Check with:

```bash
python -c "from raven.plugin.manifest import ContextSegmentContribution"
```

An `ImportError` means the host is not ready yet.

### Install

```bash
pip install skillsearch
```

Raven discovers pip-installed plugins through the `raven.plugins` entry
point, then reads `raven-plugin.toml` from inside the named package — both
ship with this one, so there is nothing to copy into `~/.raven/plugins/`.

Configure under the plugin's id in Raven's config:

```json
{
  "plugins": {
    "config": {
      "skillsearch": {
        "skills_dir": "skills",
        "hub_endpoint": "https://skillhub.evermind.ai",
        "model": "",
        "top_k": 5
      }
    }
  }
}
```

If a bundled plugin already claims the `skills` stage, Raven refuses to
activate either and logs the conflict — disable the other one via
`plugins.disabled`, since a stage takes exactly one owner.

### The manifest

Shipped inside the package; reproduced here for reference:

```toml
[plugin]
id                 = "skillsearch"
version            = "0.1.0"
bundled            = false
enabled_by_default = true

[[plugin.contributes.context_segments]]
name     = "skills"
factory  = "skillsearch.adapters.raven:make_segment"
replaces = "skills"

[plugin.config_schema]
skills_dir   = { type = "string",  default = "skills" }
hub_endpoint = { type = "string",  default = "" }
model        = { type = "string",  default = "" }
top_k        = { type = "integer", default = 5 }
max_select   = { type = "integer", default = 2 }
```

`make_segment(ctx)` reads `ctx.config` as the config slice and takes one
service from `ctx.services`: `get_tool_definitions`, the callable that
answers which tools the agent has this turn. Returning `None` declines the
stage, which is what happens when no source is configured — the host then
renders no `# Skills` block rather than an empty one.

Raven's live objects (its LLM provider, its skill registry, its memory
backend) arrive through private keys the host sets on the slice —
`_provider`, `_store`, `_memory`. They are objects, not user settings, and
are deliberately not part of `config_schema`.

**Model switching.** The rewriter and gate run on `model` from this
config. A host-side model switch does not reach them, which is the point:
a gate is routinely meant to run on a cheaper model than the agent. Set
`model` to the agent's model if you want them to match.

## Hermes

Hermes drives a selected engine through a fixed pipeline. The hook that
matters is `prefetch`, called before each model call, whose return value
the runtime injects into that turn.

`plugin.yaml`:

```yaml
name: skillsearch
version: "0.1.0"
manifest_version: 1
description: "Skill retrieval — local, remote catalog, and the agent's own."
```

`__init__.py`:

```python
from skillsearch.adapters.hermes import SkillSearchEngine

def register(ctx):
    ctx.register_context_engine(SkillSearchEngine.from_hermes(ctx))
```

Configuration lives in `$HERMES_HOME/skillsearch.json`, mirroring where the
EverOS provider keeps `everos.json`:

```json
{
  "skills_dir": "~/.hermes/skills",
  "hub_endpoint": "https://skillhub.evermind.ai",
  "model": "",
  "top_k": 5
}
```

Two contract points Hermes states, both honoured by the adapter:

- **`is_available()` makes no network calls.** It checks whether any source
  is configured, nothing more.
- **Nothing on the hot path raises.** `prefetch` is synchronous while the
  engine is async, so the work runs on a private event loop in a daemon
  thread with a hard deadline. A slow catalog costs the turn its skills,
  never the turn itself.

## OpenClaw

OpenClaw's plugins are TypeScript. Run the HTTP adapter beside the agent
and call it from the `before_prompt_build` hook:

```bash
python -m skillsearch.adapters.http_server --config ./skillsearch.json --port 8477
```

```ts
api.on("before_prompt_build", async (ctx) => {
  const res = await fetch("http://127.0.0.1:8477/retrieve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: ctx.userMessage }),
    signal: AbortSignal.timeout(5000),
  });
  const { text } = await res.json();
  if (text) ctx.addSystemContext(text);
}, { timeoutMs: 5000 });
```

The server always answers `200` with `{"text": "..."}`, empty on any
internal failure — the same fail-open rule the in-process adapters follow,
so a retrieval problem cannot break a turn.

**One caveat specific to this route.** If the server and the agent are on
different machines they share no filesystem, so `{baseDir}` and relative
links inside a skill body cannot be resolved to paths the agent can open.
Set `resolve_refs: false` in that case; otherwise the injected text
promises files the agent cannot read.

The server binds to loopback and has no authentication. Put a proxy in
front before exposing it anywhere else.

## Writing an adapter for another host

Three things, in order of how much they vary:

1. **Find the injection point** — the hook that runs before the model sees
   the prompt and can add to it. Call `retrieve(query)` there; if the host
   renders its own format, call `hits(query)` and get records instead of
   text.
2. **Translate the config.** Build a `SearchConfig` from whatever your host
   already reads. Every field has a working default, so translate only what
   the host actually exposes.
3. **Wire the optional capabilities** — a chat model for the rewriter and
   gate, a tool-name callable for the gate's environment check, a skill
   store if the host already scans a skills directory. Each is optional and
   each degrades on its own: no model means no gate, not a failure.

The protocols in `skillsearch/ports.py` are duck-typed, so a host object
that already has the right shape can be passed straight through — no
subclassing, no registration.
