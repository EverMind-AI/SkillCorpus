# skillsearch for Hermes

Skill retrieval as a Hermes memory provider. Every turn, Hermes calls
`prefetch` before the model; this answers it with the skills that turn
wants, and does nothing else.

## Install

```bash
pip install ./engine-python                        # the retrieval engine
cp -r plugin-hermes "$HERMES_HOME/plugins/skillsearch"
hermes memory setup                                # pick "skillsearch"
```

`$HERMES_HOME/plugins/<name>/` is where the host scans for user-installed
providers — one of four sources it walks, ranked bundled > user > project >
pip entry point. The directory needs an `__init__.py` exposing a class that
implements the `MemoryProvider` ABC, which is what this package is.

The provider occupies the **memory slot**, because that is the slot Hermes
routes `prefetch` through. Nothing here writes memory — there is no
`sync_turn`, no session hooks — but a deployment still has to choose: one
provider holds the slot at a time, so this and a memory backend cannot both
be active.

## Configuration (`$HERMES_HOME/skillsearch.json`)

| Key | Default | Purpose |
|---|---|---|
| `skills_dir` | `~/.hermes/skills` | Directory scanned for `SKILL.md` files |
| `hub_endpoint` | — | Remote catalog base URL; empty means local only |
| `hub_api_key` | — | Bearer token for that catalog |
| `model` | — | Model for the rewriter and the gate |
| `model_base_url` | `https://api.openai.com/v1` | OpenAI-compatible endpoint for it |
| `model_api_key` | — | Credential for that endpoint |
| `top_k` | `5` | Upper bound on skills injected per turn |
| `max_select` | `2` | Upper bound on what the gate keeps |
| `timeout_s` | `8.0` | Deadline for one retrieval |

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

**Configure a `model`.** Fusion ranks by position, so every source's best hit
reaches the shortlist however weakly it matched, and the gate is the only
step that removes those. Without one, an unrelated turn still gets a skill.

The provider prefers a model client the host exposes (`model_client` or
`llm` on the plugin context) and falls back to the configured endpoint. It
never dials out from `is_available`, per the host contract.

## What it costs a turn

Two auxiliary model calls when retrieval runs — the rewriter, then the gate
— and between zero and `max_select` skill bodies injected. A turn the
rewriter judges needs no skills makes one call and injects nothing.

Every failure is open. `prefetch` sits between the user's message and the
model's reply, so a broken catalog, an unreachable endpoint or a timeout
costs the turn its skills and never the turn itself.

## Tests

```bash
python -m pytest plugin-hermes/tests -q
```

The suite runs without a Hermes checkout, against the fallback base class
this plugin declares when `agent.memory_provider` is unimportable. That
fallback is also what hides a missing method, so run the suite against a
real checkout too — the provider then subclasses the host's own ABC, and an
unimplemented abstract method fails at instantiation:

```bash
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git
PYTHONPATH=hermes-agent python -m pytest plugin-hermes/tests -q
```

Verified further by loading the plugin through the host's own discovery:
copied to `$HERMES_HOME/plugins/skillsearch/`, `load_memory_provider(
"skillsearch")` returns this provider and `prefetch` produces the block.

## Known limitations

- **The gate cannot see the agent's tools.** It drops a skill whose workflow
  needs a tool the agent lacks only when the host exposes `get_tool_names`
  on the plugin context. Without it the gate still judges relevance.
- **One route serves both model calls.** The rewriter's job is far cheaper
  than the gate's; splitting them is deferred until a deployment shows the
  cost difference matters.
- **The local scan is cached for the life of the session.** A `SKILL.md`
  written mid-session is invisible until the next `initialize`.
