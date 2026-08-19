# skillsearch for Raven

Skill retrieval as a Raven context segment. It claims the host's `skills`
stage — the per-turn `# Skills` block naming what the model should see for
the message just received — and the host keeps no built-in fallback, so
disabling this plugin turns retrieval off rather than silently reverting.

## Install

Raven needs a `context_segments` contribution slot, which is being taken
upstream rather than carried here as a patch: a fork of the host is a fork
to maintain, and this one had already drifted from `main` and wired only
one of the three `AgentLoop` construction sites. Until that lands the
plugin installs cleanly and simply never gets a stage to claim.

```bash
pip install ./engine-python ./plugin-raven      # the engine, then the plugin
```

Or copy the package into Raven's user plugin directory, which the host
ranks above a pip install so a local edit shadows the installed copy:

```bash
cp -r plugin-raven/skillsearch_raven ~/.raven/plugins/skillsearch
```

The path is fixed by the host (`Path.home() / ".raven" / "plugins"`), and
each subdirectory must hold a `raven-plugin.toml`; the directory name is
informational, the id inside the manifest is canonical. Discovery only
reads manifests — plugin code is imported later, when the registry resolves
the factory the manifest names.

Either route needs `skillsearch` importable — the plugin is an adapter, and
the pipeline lives in the engine.

## Configuration

Under `plugins.config.skillsearch` in Raven's config. Every key is listed
in [`skillsearch_raven/raven-plugin.toml`](skillsearch_raven/raven-plugin.toml),
which the host validates against; the ones that matter most:

| Key | Default | Purpose |
|---|---|---|
| `skills_dir` | `skills` | Directory scanned for `SKILL.md`, relative to the workspace |
| `hub_endpoint` | — | Remote catalog base URL; empty means local only |
| `model` | — | Model for the rewriter and the gate |
| `top_k` | `5` | Upper bound on skills injected per turn |
| `max_select` | `2` | Upper bound on what the gate keeps |

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

**Configure a `model`.** Fusion ranks by position, so every source's best
hit reaches the shortlist however weakly it matched, and the gate is the
only step that removes those. Without one, an unrelated turn still gets a
skill.

The host passes live objects through the config slice under private keys
— not settings, and not something a user writes. `_provider` is the model
channel the rewriter and the gate run on; `_store` is the host's own
`SkillRegistry`, reused so the plugin does not rescan a directory the host
already scans and watches.

A host with a source of its own — recall over self-evolved skills, a
private library — writes it against the engine's `SkillSource` protocol
and passes it to `SkillSearch(extra_sources=...)`. That adapter belongs to
the host, not here: this plugin knows about a skills directory and a
catalog, and nothing else.

## What the host requires of a segment

Three class attributes and a method, and the attributes are read *before*
anything is built: `ContextAssembler` sorts every builder by `order` while
constructing itself, so a segment missing them does not degrade retrieval
— the agent fails to start.

| | Value | Why |
|---|---|---|
| `name` | `skills` | The stage claimed |
| `order` | `5` | The slot reserved for `# Skills`, between the always-on skills (4) and the Curator (6) |
| `needs_prefix` | `False` | Retrieval reads the current message, not the assembled prefix, so it stays in the parallel phase |

## Tests

```bash
python -m pytest plugin-raven/tests -q
```

The host is not importable outside a Raven checkout, so the suite pins what
the host reads. Against a checkout named by `RAVEN_ROOT`, the repository's
`verify-raven.py` drives the real path: the registry discovers the plugin, `skills` is
claimed, `ContextAssembler` is built through the host's own factory, and
the block is asserted to reach the assembled prompt.

## Known limitations

- **The patch wires one of three entry points.** `cli/agent_commands.py`
  builds the segments and passes them to `AgentLoop`; `gateway_commands.py`
  and `tui_commands.py` do not. Retrieval is on for `raven agent` and
  absent from the gateway and the TUI.
- **The patch is cut against `1cb604a`** and does not apply to `5e6478e`;
  four of the files it touches have moved since.
- **`{baseDir}` resolution needs a shared filesystem.** Turn `resolve_refs`
  off where the agent and the skills do not share one.
