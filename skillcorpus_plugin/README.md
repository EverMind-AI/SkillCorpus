# SkillCorpus Plugins

English | [简体中文](README.zh.md)

**The official agent-host plugins for [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus): your agent, automatically briefed with the right skills — every turn.** SkillCorpus Plugins watches what the user just asked, retrieves the matching `SKILL.md` skills from a local directory and an optional remote catalog, and puts their bodies in front of the model before it answers. No tool call, no skill name the model has to already know.

A real turn, on WorkBuddy: ask *“帮我生成一个二维码，内容是 https://evermind.ai，存到桌面”*. No QR skill exists on the machine — but the catalog has one, so before the model answers, its context gains:

```markdown
# Skills

### Skill: fireflylan-qr-code  [hub/24493cfe-c3cc-4dbe-9f6c-dfeac945b4c1]
**Skill directory**: `~/.workbuddy-ai/skillsearch-bundles/fireflylan-qr-code@<version>`
Relative refs (e.g. `references/x.md`, `./scripts/y.sh`) resolve under
this directory — use the absolute form for read_file / exec.

生成二维码/条形码，支持文本、URL、WiFi 配置等内容，可自定义尺寸、颜色并指定保存路径 …
```

The skill's bundled script is already extracted next to it; the model runs it and the QR code lands on the desktop. Without retrieval, the model improvises — `pip install qrcode` and hope.

Works with a directory of your own skills, with [SkillHub](https://evermind.ai/skillhub) — the hosted endpoint over [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus)'s 114,190 vetted, permissively-licensed skills, where that QR skill came from — or both fused into one ranking.

## Install — paste this to your agent

Every supported host *is* an agent, so the fastest install is to let it install itself. Paste this into WorkBuddy, Hermes, OpenClaw, or a DeepSeek Harness session:

> Install SkillCorpus Plugins (plugin id: skillsearch) for the agent host
> you are running in.
> Clone https://github.com/EverMind-AI/SkillCorpus and follow
> `skillcorpus_plugin/INSTALL.agent.md`: detect the host first, back up any config
> file before editing and show me the diff before applying, and finish by
> verifying that a test query produces a `# Skills` block. Then report what
> you changed and the verification result.

The playbook it follows is [`INSTALL.agent.md`](INSTALL.agent.md) — human-readable, so you can audit exactly what your agent is about to do.

**Installing by hand instead** — pick your host:

| Your host | Do this | Details |
| --- | --- | --- |
| **WorkBuddy** | build `plugin-workbuddy`, register it as a marketplace, enable — file-level steps an agent does well: paste the prompt in its README | [plugin-workbuddy](plugin-workbuddy#install--paste-this-to-workbuddy) |
| **Hermes** | `pip install ./engine-python && cp -r plugin-hermes "$HERMES_HOME/plugins/skillsearch" && hermes memory setup` | [plugin-hermes](plugin-hermes#install) |
| **OpenClaw** | `npm install --prefix plugin-openclaw && npm run --prefix plugin-openclaw build`, then two keys in `openclaw.json` | [plugin-openclaw](plugin-openclaw#install) |
| **DeepSeek Harness** | copy `engine-typescript/` to `packages/skill/skill-search/`, add a `cordis.yml` row | [engine-typescript](engine-typescript#where-this-goes) |
| **Raven** | `pip install ./engine-python ./plugin-raven` — activates once Raven's `context_segments` slot lands upstream; Raven's built-in retrieval works today | [plugin-raven](plugin-raven#install) |
| **Anything else** | run `python -m skillsearch.adapters.http_server` beside it and POST `/retrieve` | [engine-python](engine-python/README.md) |

## Try it in 30 seconds

No catalog, no model, no config — one local skill and one question:

```bash
mkdir -p skills/pdf-tables && cat > skills/pdf-tables/SKILL.md <<'EOF'
---
name: pdf-tables
description: Extract tables from PDF documents, scanned or native, into CSV.
---
Use camelot for native PDFs; OCR scanned pages first.
EOF
```

Ask your (plugin-equipped) agent about extracting tables from a PDF — the block above appears in its context. Ask it about the weather — nothing is injected: queries that match no skill retrieve nothing.

## What retrieval buys, on four hosts

One case per host, from QwenClawBench — each host running its own agent, with
and without retrieval.

| Host | Task | Without skills | With skills | Retrieved |
| --- | --- | --- | --- | --- |
| OpenClaw | morning news digest skill | 0.00 | **1.00** | `news-daily`, `news-express` |
| Hermes | gateway process monitor check | 0.17 | **0.92** | `openclaw-cli` |
| Raven | memos discovery and workspace bootstrap | 0.00 | **0.74** | `caihhub-preference` |
| DeepSeek Harness | polygon arbitrage monitor check | 0.50 | **0.83** | `defi-wallet-monitor` |

**OpenClaw — 0.00 to 1.00.** The task asks for a morning news digest skill and
for the digest to be delivered. Without retrieval nothing was produced: no
skill file, no frontmatter, no message. `news-daily` and `news-express` supply
both halves — the shape of a digest skill, and the call that sends it — and all
four grading points came in.

**Hermes — 0.17 to 0.92.** `openclaw-cli` documents how to list cron jobs and
read the gateway's logs. The five points that went from zero to full are
exactly the ones needing those commands: the brief followed, the cron gap
explained, the security policy identified, the log analysis performed, the
status summary complete. The agent did not lack reasoning — it lacked the
commands.

**Raven — 0.00 to 0.74.** A workspace bootstrap, where the score is what
exists on disk afterwards. `caihhub-preference` describes the layout this
product expects, and with it the run initialised git, wrote the identity
files, tracked workspace state, and filled the documentation with real
content instead of placeholders. The one point still missing is the memos
investigation, which the skill says nothing about.

**DeepSeek Harness — 0.50 to 0.83.** The arbitrage monitor already ran either
way; what the skill changed is where its output went. `defi-wallet-monitor`
names the data directory and log convention, so the run wrote its outputs
where the check looks for them rather than beside the script.

## The seven settings you'll actually touch

Full per-host tables live in each plugin's README; these seven decide behaviour everywhere (Python / TypeScript spellings):

| Setting | Default | What it decides |
| --- | --- | --- |
| `skills_dir` / `skillsDirs` | the host's own skills directory | Where local skills are scanned. Missing directory = the source simply doesn't exist. |
| `hub_endpoint` / `hubEndpoint` | *(empty)* | EverMind-compatible catalog. Empty disables only this source. |
| `clawhub_endpoint` / `clawhubEndpoint` | `https://clawhub.ai` | ClawHub search; empty disables it. |
| `skillhub_cn_endpoint` / `skillhubCnEndpoint` | `https://api.skillhub.cn` | skillhub.cn search; empty disables it. |
| `model` (+ host-specific route) | *(empty)* | Enables the query rewriter and the gate. Empty = retrieval runs unfiltered, ranked by keywords. |
| `top_k` / `topK` | 2 | Most skills ever injected per turn. |
| `gate` | *auto* | LLM filter that drops skills this agent can't run. Auto = **off** for local-only (your own skills, ranking suffices), **on** when a catalog is configured (wild skills need vetting). Override with `true`/`false`. |

## What it costs you

Per turn, worst case, all bounded and all fail-open — a slow or broken step costs the turn its skills, never the turn:

| Step | When | Cost ceiling |
| --- | --- | --- |
| Local BM25 | always | milliseconds, in-process |
| Catalog search | remote source enabled | 5s per request |
| Query rewrite | model configured | one small model call, 5s cap |
| Gate | model + (auto) hub configured | one model call over ≤10 candidates, 20s cap |
| Bundle download | hub skill selected | 30s cap, cached by version — repeat turns are a disk stat |
| Injected text | something matched | 0 to `top_k` skill bodies (typically ≤2 with the gate; a body is commonly 1–4k tokens) |

Nothing is added to durable history — the injection is rebuilt per turn and disappears with it.

## What leaves your machine

Honest accounting, because retrieval runs on your conversation:

- **Local-only setup (after explicitly disabling the three remote endpoints)** — nothing. Scanning, ranking and injection are all in-process.
- **Default installation** — ClawHub and skillhub.cn are enabled; the retrieval query is sent to both services. Set their endpoint fields to an empty string to disable either one. With no `model`, no LLM gate runs: only the marketplaces’ own trust flags and the lexical relevance guard apply.
- **With `hub_endpoint` set** — the retrieval query (your message, or its model-cleaned rewrite) is sent to that catalog on every retrieving turn; selected skills' bodies and bundles are downloaded from it. Bundles are unzipped with path-traversal rejection, an extension allowlist, and 8 MiB/file, 64 MiB/archive caps, into a cache directory outside every scanned skills dir (`~/.workbuddy-ai/skillsearch-bundles`, `~/.skillsearch/hub`, `~/.openclaw/skillsearch-bundles`, or `~/.dsh/skillsearch-bundles` by default).
- **Marketplace body fetches** — up to two candidates per enabled marketplace are downloaded and safely extracted before the optional LLM gate, because those APIs expose the skill body through the bundle. A rejected candidate may therefore remain in the cache, but the plugin never executes it automatically.
- **With `model` set** — the rewriter sees your message (truncated to 2,000 chars); the gate sees your message plus candidate names, descriptions and 300-char body excerpts. Both go to the model *you* configured, through the host's own provider where the host offers one.

Downloaded skills are third-party content that the model is instructed to follow. ClawHub and skillhub.cn entries are not covered by SkillCorpus’s repository-license audit; review their upstream terms before redistribution. The gate can reject skills that assume unavailable tools or environments, but it only exists when a model is configured.

## Make your skills findable

Since retrieval indexes **name and description** (deliberately — the body is where stopword noise lives), the description is your skill's search surface. Write the situations, not just the topic:

```yaml
# Findable: names the task, the inputs, and the phrasings that should trigger it
description: Extract tables from PDF documents, scanned or native, into CSV
  or JSON. Use when asked to "get the tables out of this PDF", "parse this
  invoice", or "convert a PDF report to a spreadsheet".

# Invisible: matches almost nothing a user would type
description: PDF helper.
```

A skill with no description at all can only be found by its name. (`index_body: true` restores body indexing if you need it.)

## When something's off

- **Nothing gets injected** — in order: does the skills directory exist and contain `SKILL.md` files (frontmatter `name:`/`description:`)? Does your query share any informative word with a description? Is the gate on and rejecting (see next)?
- **See exactly what the gate decided** — Python hosts: set `SKILLSEARCH_GATE_LOG_PATH=/tmp/gate.jsonl` and read the per-turn records: candidates, the model's plan, selected, rejected.
- **Gate feels too strict** — it is precision-biased by design ("select none rather than something irrelevant"). Set `gate: false` for local-only use, or raise `max_select`.
- **Hub timeouts** — the catalog budget is 2s per request; a proxy in `HTTP_PROXY` that can't reach it will eat the whole budget. The source fails open: turns proceed without remote skills.
- **Upgrading from an early revision** — three behaviours changed: `memory=`/`agent_id` were removed (use `extra_sources=`), the rewriter no longer vetoes retrieval, and the index defaults to name+description. Details in [`CHANGELOG.md`](CHANGELOG.md).

## Uninstall

Reverse of install, nothing hidden: remove the plugin directory / pip packages, delete the config keys you added, and optionally the bundle cache directory listed above. Each plugin README has the exact paths, and the agent playbook has an [uninstall section](INSTALL.agent.md#uninstall) — "remove skillsearch" works too.

## How it works

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

¹ Two sources ship. A host with one of its own — recall over self-evolved skills, a private library, a second catalog — writes it against the `SkillSource` protocol (a name, a weight, one `search`) and passes it in: `SkillSearch(extra_sources=[...])` in Python, `EngineParts.sources` in TypeScript. Neither engine learns what it is.

Three properties hold everywhere: **retrieval never raises** (a failure loses the turn its skills, not its response); **fusion ranks by position, not score** (that is what lets a BM25 scale and a catalog scale merge — and why the gate, not a score threshold, is the precision filter); **capability is presence** (no endpoint = no remote source, no model = no rewrite and no gate — a config can never say two contradictory things).

Each plugin binds this pipeline to one host moment — always *after the user's message, before the model*:

| Plugin | Host | Seam | Host change needed |
| --- | --- | --- | --- |
| [`plugin-workbuddy/`](plugin-workbuddy) | WorkBuddy (5.3.13) | `UserPromptSubmit` hook — a process per turn | none |
| [`plugin-hermes/`](plugin-hermes) | Hermes | memory provider's `prefetch` | none |
| [`plugin-openclaw/`](plugin-openclaw) | OpenClaw (verified back to 2026.3.8) | `before_prompt_build` hook | none |
| [`engine-typescript/`](engine-typescript) | DeepSeek Harness | `agent/pre-step` waterfall | none |
| [`plugin-raven/`](plugin-raven) | Raven | context segment for the `skills` stage | `context_segments` slot, landing upstream |

## The remote catalog

The `hub` source speaks a three-tier API — discover (metadata), read (`skill_md`), download (bundle zip) — under `/openapi/v1/skills`, priced so a turn only pays for what it keeps: one search, body fetches for the shortlist, downloads only for gate survivors. [SkillHub](https://evermind.ai/skillhub) serves it over SkillCorpus:

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills?q=extract+tables+from+a+PDF"
```

Or point it at any service of your own answering the same envelope. Every SkillCorpus skill keeps its upstream license and every source repository is license-audited; the retrieval gain on real agent benchmarks is measured in the [SkillCorpus paper](https://arxiv.org/abs/2607.15557).

## Repository map

```
engine-python/       the pipeline in Python 3.11+   (also: HTTP adapter for any host)
engine-typescript/   the pipeline in TypeScript / Node 18+, and the DeepSeek Harness entry
plugin-workbuddy/    WorkBuddy plugin over engine-typescript — a hook process per turn
plugin-hermes/       Hermes plugin    over engine-python
plugin-raven/        Raven plugin     over engine-python
plugin-openclaw/     OpenClaw plugin  over engine-typescript
INSTALL.agent.md     the install playbook your agent follows
```

In code, the engine and the packages keep the name `skillsearch` — `import skillsearch`, plugin ids, config paths — while SkillCorpus Plugins names the product. The two engines are independent ports of one design, not a shared core with bindings. What keeps them equal is pinned by tests, not prose: byte-identical prompts, identical BM25/fusion arithmetic, identical `{baseDir}` resolution — [`parity.test.ts`](engine-typescript/tests/parity.test.ts) holds the TypeScript side to values the Python suite produces, and CI runs both on every push.

## Working on this repository

Each directory carries its own suite, and they are independent:

```bash
pip install -e './engine-python[dev,hub]' -e ./plugin-raven
pytest engine-python/tests plugin-hermes/tests plugin-raven/tests -q
ruff check engine-python/skillsearch engine-python/tests plugin-raven
cd engine-typescript && npx tsx --test tests/parity.test.ts
cd plugin-openclaw   && npm install && npm run ci
```

A suite stands in for its host, and a stand-in cannot fail on the host refusing to load the plugin. Two checks close part of that gap (CI runs the first):

```bash
# The Hermes plugin declares a fallback base class so it imports without the
# host; against the real ABC an unimplemented abstract method actually fails:
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git
PYTHONPATH=hermes-agent pytest plugin-hermes/tests -q

# plugin-openclaw's hand-copied host types, compiled against the originals:
git clone --depth 1 https://github.com/openclaw/openclaw.git ../openclaw-host
npm --prefix plugin-openclaw run check:host
```

Only a real checkout closes the rest: every plugin here has been installed into its host and driven through that host's own loader end to end — `verify-raven.py` in the root drives the Raven path through the host's own `ContextAssembler`.

**Tested against**: Python 3.11–3.13 · Node 18+ (CI on 22) · WorkBuddy 5.3.13 · hermes-agent `main` · OpenClaw (verified back to 2026.3.8) · DeepSeek Harness workspace `main` · Raven pending its upstream slot.

## Part of the EverMind agent stack

[Raven](https://github.com/EverMind-AI/Raven), the terminal-native agent harness · [EverOS](https://github.com/EverMind-AI/EverOS), the memory substrate · [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus), the community skill corpus. SkillCorpus Plugins is the retrieval layer that connects hosts — these and hosts that are none of these — to the skills.

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

Apache-2.0 for this repository and the Python package. The TypeScript package declares MIT in its `package.json`, matching the harness it embeds in. Release history: [`CHANGELOG.md`](CHANGELOG.md).
