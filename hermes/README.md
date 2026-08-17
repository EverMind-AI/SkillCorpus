# skillsearch for Hermes

Skill retrieval as a Hermes memory provider. Every turn, Hermes calls
`prefetch` before the model; this answers it with the skills that turn
wants, and does nothing else.

## Install

```bash
pip install ./python                       # the retrieval engine
cp -r hermes ~/.hermes/plugins/memory/skillsearch
hermes memory setup                        # pick "skillsearch"; answer the prompts
```

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
python -m pytest hermes/tests -q
```

The host is not importable outside a Hermes runtime, so the tests pin the
contract instead — the method names, signatures and return types Hermes
calls, taken from the shipped EverOS provider in the same plugin family —
and drive the real pipeline over a local HTTP endpoint.

## Known limitations

- **The gate cannot see the agent's tools.** It drops a skill whose workflow
  needs a tool the agent lacks only when the host exposes `get_tool_names`
  on the plugin context. Without it the gate still judges relevance.
- **One route serves both model calls.** The rewriter's job is far cheaper
  than the gate's; splitting them is deferred until a deployment shows the
  cost difference matters.
- **The local scan is cached for the life of the session.** A `SKILL.md`
  written mid-session is invisible until the next `initialize`.
