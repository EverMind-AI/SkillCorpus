# skillsearch

**Per-turn skill retrieval for agent hosts: given what the user just said, decide which skills the model should see — and put them in front of it.**

Agent skills (`SKILL.md` files packaging reusable procedural knowledge) only help if the right ones reach the prompt at the right moment. Publishing a full catalog to the model wastes context and asks it to know names in advance; injecting everything is worse. skillsearch retrieves instead: every turn it searches two sources — a local directory the host already conventions, and a remote catalog — fuses the rankings, and injects what survives.

Pairs naturally with [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus), the open corpus of 96,401 vetted, permissively-licensed skills, whose hosted [SkillHub](https://evermind.ai/skillhub) endpoint is a ready-made remote catalog for the `hub` source below — no server of your own required. Local-only setups need neither.

```
query
  ├─ rewrite          clean the message into a retrieval query   (optional)
  ├─ fan out          local BM25 · remote catalog · host sources¹
  ├─ fuse             weighted RRF (K = 60), deduplicated
  ├─ hydrate          fetch bodies for metadata-only hits
  ├─ resolve (local)  {baseDir} and links, before the gate judges them
  ├─ gate             drop what this agent cannot run here       (optional)
  └─ render           extract remote bundles, resolve their paths
→ text to inject
```

¹ Two sources ship. A host with one of its own — recall over self-evolved
skills, a private library, a second catalog — writes it against the
`SkillSource` protocol (a name, a weight, one `search`) and passes it in:
`SkillSearch(extra_sources=[...])` in Python, `EngineParts.sources` in
TypeScript. Neither engine learns what it is.

## Two implementations

| | [`engine-python/`](engine-python) | [`engine-typescript/`](engine-typescript) |
| --- | --- | --- |
| Entry | `SkillSearch.retrieve(query) -> str` | `SkillSearchEngine.retrieve(query)` |
| Install | `pip install -e engine-python` | copy into `packages/skill/skill-search/` |
| Ships a host plugin | [`plugin-hermes/`](plugin-hermes) · [`plugin-raven/`](plugin-raven) | [`plugin-openclaw/`](plugin-openclaw) |
| Also drives | any host over HTTP, via `adapters/http_server.py` | DeepSeek Harness, via `src/index.ts` |
| Host-supplied sources | `SkillSearch(extra_sources=...)` | `EngineParts.sources` |

The two are ports of one design, not a shared core with bindings: each is idiomatic in its own runtime and neither imports the other. What keeps them equal is pinned by tests, not prose — the prompts are byte-identical, the tokenizer, BM25 index text and fusion arithmetic produce the same numbers, and `{baseDir}` resolution follows the same rules. [`engine-typescript/tests/parity.test.ts`](engine-typescript/tests/parity.test.ts) holds the TypeScript side to values the Python suite produces; CI runs both on every push.

## Layout

```
engine-python/       the pipeline in Python
engine-typescript/   the pipeline in TypeScript, plus the DeepSeek Harness entry
plugin-hermes/       a Hermes plugin   over engine-python
plugin-raven/        a Raven plugin    over engine-python
plugin-openclaw/     an OpenClaw plugin over engine-typescript
```

Prefixed by role rather than by language, because the languages do not line
up with the roles: the Hermes plugin is Python and the OpenClaw plugin is
TypeScript.

One host integration has no directory of its own. `engine-typescript/`
*is* the package `@deepseek-ai/dsh-skill-search`, and `src/index.ts` plus
`src/invariant.ts` are the two files in it that bind to the host. The
Harness does not install a package — it takes a copy of a workspace
directory, type-checks it through `src/`, and holds `packages/*/*/src` to
per-file coverage. So the entry cannot move out without turning "copy this
directory" into "merge two directories", and cannot be bundled the way the
OpenClaw plugin bundles the engine without breaking all three of those.

The other three are ordinary plugin packages over an engine.

## Four hosts

Each plugin is a thin adapter over one of the two implementations — the
pipeline is the same either way, and only the seam differs.

| Plugin | Host | Seam | How the host finds it | Host change |
| --- | --- | --- | --- | --- |
| [`plugin-hermes/`](plugin-hermes#install) | Hermes | the memory provider's `prefetch` | a directory in `$HERMES_HOME/plugins/` | none |
| [`plugin-raven/`](plugin-raven#install) | Raven | a context segment claiming the `skills` stage | a `raven.plugins` entry point, or a directory in `~/.raven/plugins/` | **yes** — a `context_segments` slot, upstream |
| [`plugin-openclaw/`](plugin-openclaw#install) | OpenClaw | the `before_prompt_build` hook | `plugins.load.paths` naming the plugin root | none |
| [`engine-typescript/`](engine-typescript#where-this-goes) | DeepSeek Harness | the `agent/pre-step` waterfall | a `cordis.yml` row naming the package | none |

Each seam is the same moment — after the user's message, before the model —
which is what lets one pipeline serve four hosts that agree on nothing else.
A host offering only session-level hooks could not carry this.

Only Raven needs a change: the others already expose a per-turn extension
point, while Raven has no contribution kind for a prompt stage. That slot
is going upstream rather than being carried here as a patch — a fork of the
host is a fork to maintain, and the one this repository used to ship had
already drifted from `main` and wired one of the three `AgentLoop`
construction sites. Until it lands the plugin installs cleanly and simply
never gets a stage to claim. Raven's own built-in retrieval is unaffected.

Every install command is in the linked section, and every one of the four
is loaded through its host's own mechanism and driven end to end — see
[Working on this repository](#working-on-this-repository).

## Quick start

**Python** — a local directory, nothing else configured:

```python
from skillsearch import SearchConfig, SkillSearch

search = SkillSearch(SearchConfig(skills_dir="~/.agent/skills"))
block = await search.retrieve("extract tables from a scanned PDF invoice")
```

**TypeScript** — mounted in a DeepSeek Harness deployment:

```yaml
- id: skill-search
  name: '@deepseek-ai/dsh-skill-search'
  config:
    skillsDirs: ['.dsh/skills']
    provider: deepseek-official
    model: deepseek-v4-flash
```

Per-runtime configuration, host wiring and known limitations live in each directory's README: [`engine-python/README.md`](engine-python/README.md) · [`engine-typescript/README.md`](engine-typescript/README.md).

## The remote catalog

The `hub` source speaks a three-tier catalog API — discover (metadata), read (`skill_md`), download (zip with bundled `scripts/`) — under `/openapi/v1/skills`. That is the API [SkillHub](https://evermind.ai/skillhub) serves over SkillCorpus:

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills?q=extract+tables+from+a+PDF"
```

Point `hub_endpoint` (Python) or `hubEndpoint` (TypeScript) at it, or at any service of your own that answers the same envelope. Every SkillCorpus skill keeps its upstream license, and every source repository is license-audited; how much retrieval over the corpus helps real agent tasks is measured in the [SkillCorpus paper](https://arxiv.org/abs/2607.15557), across three benchmarks and two harnesses.

## What both implementations guarantee

**Configure a model if you can.** Fusion ranks by position, not by score — that is what lets sources with different scoring scales be compared — so a source's best hit enters the shortlist even when it is a weak match. Filtering those out is the gate's job, and without a model there is no gate: ask about the weather with a PDF skill installed and the PDF skill still shows up.

**Retrieval never raises.** It sits on the turn's hot path in every host, so a failure returns nothing to inject — the turn loses its skills, not its response. Every model call on that path is bounded (rewrite 5s, gate 20s), and a deadline that passes hangs up the call rather than letting it stream on unread.

**Capability is presence, not flags.** No catalog endpoint means no remote source; no model means no rewrite and no gate. A configuration can never say two contradictory things.

## Part of the EverMind agent stack

[Raven](https://github.com/EverMind-AI/Raven), the terminal-native agent harness · [EverOS](https://github.com/EverMind-AI/EverOS), the memory substrate · [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus), the community skill corpus. skillsearch is the retrieval layer that connects hosts — these and hosts that are none of these — to the skills.

## Working on this repository

Each directory carries its own suite, and they are independent — nothing
below needs the others to have run.

```bash
pip install -e './engine-python[dev,hub]' -e ./plugin-raven
pytest engine-python/tests plugin-hermes/tests plugin-raven/tests -q
ruff check engine-python/skillsearch engine-python/tests plugin-raven
cd engine-typescript && npx tsx --test tests/parity.test.ts
cd plugin-openclaw   && npm install && npm run ci
```

### What a suite without a host cannot check

Each suite stands in for its host, and a stand-in cannot fail on the host
refusing to load the plugin. Two checks close part of that gap:

```bash
# The Hermes plugin declares a fallback base class so it imports without the
# host. That fallback verifies nothing — an unimplemented abstract method
# passes. Against the real ABC it does not:
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git
PYTHONPATH=hermes-agent pytest plugin-hermes/tests -q

# `plugin-openclaw/src/openclaw-types.ts` is a hand-copy of the host's types,
# and a hand-copy drifts. This compiles the copies against the originals:
git clone --depth 1 https://github.com/openclaw/openclaw.git ../openclaw-host
npm --prefix plugin-openclaw run check:host
```

Only a real checkout closes the rest. Every plugin here has been installed
into its host and driven through that host's own loader — the same four
queries each, expecting three specific skills and one empty result:

| Host | Loaded by | Driven to |
|---|---|---|
| Hermes | `discover_memory_providers` → `load_memory_provider` | `prefetch()` |
| Raven | `build_plugin_registry` → `build_plugin_segments` | the assembled system prompt, through the host's own `ContextAssembler` |
| DeepSeek Harness | a `cordis.yml` row naming the package | the `agent/pre-step` waterfall |
| OpenClaw | `discoverOpenClawPlugins` → the built `dist/index.js` | the `before_prompt_build` hook |

Three defects came out of doing that, none of which any suite here could
have caught: the Raven segment was missing the class attributes the host
sorts builders on, so the agent failed to start rather than retrieving
badly; the OpenClaw entry imported a helper that does not exist before host
version 2026.6.10, so the host discovered the plugin and then could not
import it; and the Raven patch landed its machinery without the two lines
that call it.
```

CI runs the first set on every push, and clones the Hermes host so its job
uses the real ABC too. The OpenClaw host check is not in CI: it needs a
full checkout of a large repository for a check that only moves when
upstream changes.

## Citation

If skill retrieval over SkillCorpus is part of your work, please cite the corpus paper:

```bibtex
@article{wang2026skillcorpus,
  title         = {SkillCorpus: Consolidating and Evaluating the Open Skill Ecosystem for Real-World LLM Agents},
  author        = {Wang, Yanze and Yao, Pengfei and Sun, Tianyi and Hu, Chuanrui and Xiao, Yan and Luo, Xiaotian and Han, Yunyun and Chen, Yifan and Sun, Jun and Deng, Yafeng},
  year          = {2026},
  eprint        = {2607.15557},
  archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2607.15557}
}
```

## License

Apache-2.0 for this repository and the Python package. The TypeScript package declares MIT in its `package.json`, matching the harness it embeds in. See [`CHANGELOG.md`](CHANGELOG.md) for release history.
