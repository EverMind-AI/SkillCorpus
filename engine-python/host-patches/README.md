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

### Status: not end-to-end yet

The patch is cut against Raven `1cb604a`; on a newer tree expect conflicts
in `context_engine/factory.py`, the file it touches most.

Read this before applying: the machinery lands, but one wire is missing,
so applying this alone does not turn retrieval on.

**What works.** The contribution kind, the registry, the discovery change,
the `ServiceLocator` callable, the named-stage factory, and the config
bridge — whose twenty keys are checked against `SearchConfig`'s field names
and now all land.

**What is missing.** `build_plugin_segments()` is added but never called.
`AgentLoop` accepts `plugin_segments` and passes it to the factory, so the
receiving end is ready; nothing constructs the dict. Until a caller is
added wherever the host builds its `AgentLoop`, `plugin_segments` is always
`None` and the `# Skills` block simply does not appear — no error, no log.

**What that caller must also pass.** The adapter reads a live provider from
`_provider` and a skill store from `_store` in its config slice, and
`build_plugin_segments` currently injects neither (it takes `model` as a
name, and `backend` which it injects as `_memory`). Without `_provider` the
rewriter and the gate are absent, and retrieval runs unfiltered — which,
because fusion ranks by position, means an unrelated turn gets an unrelated
skill. Threading the provider through is part of the same missing wire.

Cut this patch again once the Raven-side work lands, rather than layering
another patch on top of it.
