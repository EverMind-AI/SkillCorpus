# Host-side patches

Changes a host needs before an adapter in this package can attach. They
belong upstream, not here — this directory only keeps them reachable while
they are pending.

## `raven-context-segments.patch`

Adds a third plugin contribution kind to Raven: `context_segments`, one
stage of the per-turn prompt assembly, claimable by name.

Raven contributes memory backends and tools. Skill retrieval is neither —
it is a stage in the assembled prompt — so there was no socket for it. This
patch adds one, and moves the `skills` stage from built-in code to that
socket, with no built-in fallback: disabling the plugin turns retrieval off
rather than silently reverting.

Seven files, ~730 lines:

| File | Change |
|---|---|
| `plugin/manifest.py` | `ContextSegmentContribution`, with a `replaces` field |
| `plugin/registry.py` | register / look up / build segments; conflict on a duplicate name |
| `plugin/context.py` | `ServiceLocator.get_tool_definitions` — a callable, since the tool set is not final when segments are built |
| `cli/_plugin_stack.py` | `build_plugin_segments()`, plus a bridge from the old `skillForge` config keys |
| `context_engine/factory.py` | ordered, *named* stages; `skills` left for a plugin to claim |
| `plugin/discover.py` | accept several bundled roots, so a plugin need not live under `plugin/memory/` |
| `agent/loop/main.py` | pass the built segments through |

Apply:

```bash
cd /path/to/raven
git apply /path/to/skillsearch/host-patches/raven-context-segments.patch
```

Verify:

```bash
python -c "from raven.plugin.manifest import ContextSegmentContribution"
```

It changes the plugin machinery only. Retrieval itself is this package, so
the patch does not carry a copy of it.

### Status: applied and driven, on `1cb604a`

Cut against Raven `1cb604a` and verified there: applied to a clean checkout
of it, the engine installed, and the `# Skills` block produced through the
host's own path — `build_plugin_registry` discovers the entry point,
`skillsearch` claims the `skills` stage, `build_plugin_segments` constructs
the segment, and `build()` returns the text Raven puts in the prompt.

On a newer tree expect conflicts. It does **not** apply to `5e6478e`:
`agent/loop/main.py`, `context_engine/factory.py`, `plugin/context.py` and
`plugin/registry.py` have all moved since. Re-cut it against the target
commit rather than forcing it.

Eight files. Six add the contribution kind, the registry, the discovery
change, the `ServiceLocator` callable, the named-stage factory and the
config bridge, whose twenty keys are checked against `SearchConfig`'s field
names. The other two are the wire that makes them do anything:

- `cli/agent_commands.py` calls `build_plugin_segments()` beside the
  existing `build_plugin_tools()` call and passes the result to
  `AgentLoop`. Without this the machinery all lands and nothing happens —
  `plugin_segments` stays `None` and the block simply never appears, with
  no error and no log.
- `cli/_plugin_stack.py` injects the live provider as `_provider`. The
  adapter reads it to build the rewriter and the relevance gate; without
  it retrieval runs unfiltered, and unfiltered means an unrelated turn
  still gets a skill, because fusion ranks by position and every source
  contributes its best hit however weakly it matched.

`agent_commands.py` is one of three places that construct an `AgentLoop`
(`gateway_commands.py` and `tui_commands.py` are the others). Only the one
is wired here, so retrieval is on for `raven agent` and absent from the
gateway and the TUI. Wiring those is the same three lines each.
