# skillsearch

**Per-turn skill retrieval for agent hosts: given what the user just said, decide which skills the model should see — and put them in front of it.**

Agent skills (`SKILL.md` files packaging reusable procedural knowledge) only help if the right ones reach the prompt at the right moment. Publishing a full catalog to the model wastes context and asks it to know names in advance; injecting everything is worse. skillsearch retrieves instead: every turn it searches the skills you have — a local directory, a remote catalog, an agent's own accumulated skills — fuses the rankings, asks a model to keep only what this agent can actually run here, and injects the survivors.

Pairs naturally with [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus), the open corpus of 96,401 vetted, permissively-licensed skills, whose hosted [SkillHub](https://evermind.ai/skillhub) endpoint is a ready-made remote catalog for the `hub` source below — no server of your own required. Local-only setups need neither.

```
query
  ├─ rewrite          turn the message into a retrieval query   (optional)
  ├─ fan out          local BM25 · remote catalog · agent recall
  ├─ fuse             weighted RRF (K = 60), deduplicated
  ├─ hydrate          fetch bodies for metadata-only hits
  ├─ gate             drop what this agent cannot run here      (optional)
  └─ render           resolve {baseDir} and links to real paths
→ text to inject
```

## Two implementations

| | [`engine-python/`](engine-python) | [`engine-typescript/`](engine-typescript) |
| --- | --- | --- |
| Entry | `SkillSearch.retrieve(query) -> str` | `SkillSearchEngine.retrieve(query)` |
| Install | `pip install -e engine-python` | copy into `packages/skill/skill-search/` |
| Ships a host plugin | [`plugin-hermes/`](plugin-hermes) | [`plugin-openclaw/`](plugin-openclaw) |
| Also drives | Raven, and any host over HTTP | DeepSeek Harness |

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

| Plugin | Host | Seam | Host change needed |
| --- | --- | --- | --- |
| [`plugin-hermes/`](plugin-hermes) | Hermes | the memory provider's `prefetch` | none |
| [`plugin-openclaw/`](plugin-openclaw) | OpenClaw | the `before_prompt_build` hook | none |
| [`engine-typescript/src/index.ts`](engine-typescript/src/index.ts) | DeepSeek Harness | the `agent/pre-step` waterfall | none |
| [`plugin-raven/`](plugin-raven) | Raven | a context segment claiming the `skills` stage | yes — [`plugin-raven/host-patches/`](plugin-raven/host-patches) |

They are ports of one design, not a shared core with bindings: each is idiomatic in its own runtime and neither imports the other. What keeps them equal is pinned by tests, not prose — the prompts are byte-identical, the tokenizer, BM25 index text and fusion arithmetic produce the same numbers, and `{baseDir}` resolution follows the same rules. [`engine-typescript/tests/parity.test.ts`](engine-typescript/tests/parity.test.ts) holds the TypeScript side to values the Python suite produces; CI runs both on every push.

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

Two of the suites check more when a host is on disk, and say so rather than
quietly checking less:

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
