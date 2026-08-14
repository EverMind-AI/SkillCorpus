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

The patch is cut against Raven `1cb604a` and was applied to a clean
checkout of it, then driven end to end — plugin discovered, segment built,
skills retrieved from a local directory and a live catalog. On a newer tree
expect conflicts in `context_engine/factory.py`, the file it touches most.

It changes the plugin machinery only. Retrieval itself is this package, so
the patch does not carry a copy of it.
