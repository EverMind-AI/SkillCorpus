# @deepseek-ai/dsh-skill-search

English | [中文](README.zh.md)

Per-turn skill retrieval. Every turn, this searches local directories and an optional remote catalog — such as [SkillHub](https://evermind.ai/skillhub), the hosted endpoint over [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus)'s 96,401 vetted skills — against what the user just wrote, and puts the matching skill bodies in front of the model before it is called.

`dsh-tool-skill` solves the same problem the other way: it publishes a catalog of every skill and lets the model load one by name. The two are alternatives — running both publishes the same skills twice, once as a tool schema and once as injected text. A deployment mounting this plugin disables `dsh-tool-skill` (and any other plugin that publishes the same skill catalog).

## Where this goes

This is a DeepSeek Harness package, not a standalone npm package: its dependencies are `workspace:^` and its `tsconfig.json` references resolve from one exact location. In a harness checkout:

1. Copy this directory to `packages/skill/skill-search/`, leaving `docs/` and `tests/parity.test.ts` behind — the first documents the decision, the second pins cross-language equality and belongs to this repository.
2. Add `{ "path": "./packages/skill/skill-search" }` to `tsconfig.host.json`.
3. Copy `docs/harness-design-note.md` and its `.zh.md` counterpart into `.agents/notes/implemented/architecture/`; the harness requires an Agent Note for a change of this size.
4. `pnpm install`, then `pnpm run verify-translation-pairing --write packages/skill/skill-search/README.md` — the bilingual gate records a hash pair per document, and `doc-sync` fails without it.
5. Mount it, and disable `dsh-tool-skill`: see [Mounting it in place of the catalog](#mounting-it-in-place-of-the-catalog).

## Pipeline

1. **Rewrite** — one model call cleans the message into a retrieval query, dropping paths, ids and boilerplate. It cannot decide that a turn wants no skills: that verdict was measured at a coin flip and was reached without sight of a single candidate, so it belongs to the gate, which sees the shortlist and the agent's tools.
2. **Fan out** — every source ranks the rewritten query on its own scale. A source that throws or times out contributes nothing; the others still answer.
3. **Fuse** — weighted Reciprocal Rank Fusion (K = 60) merges the lists *by position*, because a local BM25 score and a catalog quality score are not comparable numbers. Hits colliding on name collapse to the better-ranked copy.
4. **Hydrate** — a candidate that arrived as catalog metadata gets its body fetched, one request per candidate that survived fusion.
5. **Gate** — one model call selects at most `maxSelect` skills, and is instructed to return none rather than something irrelevant.
6. **Resolve** — `{baseDir}` placeholders and links to bundled files (`references/`, `scripts/`, …) in each on-disk survivor become absolute paths under the skill's directory, each substitution existence-checked. Without this a body saying `scripts/x.sh` reads as relative to the agent's cwd — the wrong place.
7. **Inject** — the selection is rendered and appended to the step's messages.

The gate is not an optimization. Fusion ranks by position, so each source's best hit reaches the shortlist however weakly it matched: without the gate, "what's the weather" injects whatever the local directory ranked first. The gate is also the only step that can reject a skill the agent *cannot run* — one whose body assumes a vendor API, a `{baseDir}` placeholder, or a slash-command dispatcher — which no ranking function can see. Configured without `provider`/`model`, retrieval runs unfiltered and injects the top `topK` by rank.

Retrieval never throws. A failed source, an unparseable gate reply, or a slow catalog costs the turn its skills and nothing else; the model still answers.

## Configuration

| Key | Default | Meaning |
| --- | --- | --- |
| `skillsDirs` | `['.dsh/skills']` | Directories scanned for `SKILL.md`, up to 5 levels deep. Relative paths resolve against cwd. |
| `hubEndpoint` | `''` | EverMind-compatible catalog; empty disables this source only. |
| `clawhubEndpoint` | `https://clawhub.ai` | ClawHub API; empty disables it. |
| `skillhubCnEndpoint` | `https://api.skillhub.cn` | skillhub.cn API; empty disables it. |
| `hubApiKey` | `''` | Bearer token for the catalog. |
| `hubTimeoutMs` | `5000` | Per-request deadline for the catalog. |
| `hubMinSafety` | `0.7` | Drop catalog entries whose safety score falls below this; only bites on catalogs that put per-skill safety in the search payload. |
| `weightLocal` | `1.0` | Fusion weight for local skills. |
| `weightHub` | `0.85` | Fusion weight for catalog skills — local skills are curated, so they outrank. |
| `topK` | `2` | Upper bound on skills injected per turn. |
| `gatePool` | `10` | Candidates the gate judges. Larger gives it more to reject. |
| `maxSelect` | `2` | Upper bound on what the gate keeps. |
| `provider` / `model` | `''` | Route for the rewriter and the gate. Configure both or neither; one alone fails at load. |
| `rewriteTimeoutMs` | `5000` | Deadline for the rewrite, the turn's first model call. Tight because it precedes everything else; on timeout the raw query is searched. |
| `gateTimeoutMs` | `20000` | Deadline for the gate, which runs before the user sees a reply. |
| `resolveRefs` | `true` | Rewrite `{baseDir}` and bundled-file links in selected bodies to absolute paths. Turn off when the agent does not share a filesystem with the skills it retrieves. |

With no `skillsDirs` and no `hubEndpoint` there is nothing to search: the plugin logs that retrieval is off and registers no hook.

## Mounting it in place of the catalog

The base bundle ships the catalog path. Swap it in a later patch layer: disable the tool that publishes the catalog, and add this row.

```yaml
- id: tool-skill
  name: '@deepseek-ai/dsh-tool-skill'
  disabled: true

- id: skill-search
  name: '@deepseek-ai/dsh-skill-search'
  config:
    skillsDirs: ['.dsh/skills']
    provider: deepseek-official
    model: deepseek-v4-flash
```

Leave `skill` and `skill-filesystem` mounted if anything else reads `ctx.skills` — the user-invocable path, the skill badge, the web UI. This plugin does not use that registry; it scans `skillsDirs` itself, so the two coexist without publishing anything twice. Disable them as well when nothing else needs them.

## Extension points

`SkillSource` is the seam. Implement `name`, `weight` and `search(query, options, k)` and a source joins the fusion; the two that ship (`LocalSkillSource`, `HubSkillSource`) hold no privileged position. `SkillSearchEngine` is exported for a consumer that wants the pipeline without the `agent/pre-step` binding — `hits()` returns records, `render()` produces the text.

## Model Experience

### Retrieved skills injected before each step

#### What the model sees

An extra user message appended to the step's messages, carrying the heading `# Skills` followed by one section per selected skill: `### Skill: <name>  [<qualified id>]`, then the skill's `SKILL.md` body with frontmatter stripped. A skill whose files are on disk also gets its directory named, because a body saying `scripts/x.sh` otherwise resolves against the agent's cwd. The bodies are third-party content selected by a model call; they are instructions the model is expected to follow, which the message's `skill-search` source records as `form: 'instructions'` alongside the injected ids.

##### Verbatim directory note, emitted for an on-disk skill

```markdown
**Skill directory**: `<absolute path>`
Relative refs (e.g. `references/x.md`, `./scripts/y.sh`) resolve under this directory — use the absolute form for read_file / exec.
```

#### Token effect

Conditional and unretained. A turn injects between zero and `maxSelect` skill bodies, in full, and nothing at all when the rewriter says the turn wants no skills or the gate selects none. The injection is built per step and is not appended to durable history, so it does not accumulate across turns. Two auxiliary model calls per retrieving turn — the rewriter, then the gate over `gatePool` candidates with a 300-character body excerpt each — spend tokens outside the conversation.

#### KV Cache effect

Replacing. The injection is appended after the derived history, and its text changes whenever the selection changes, so the suffix from that message onward is not reusable between steps; the prefix before it is untouched. A turn that injects nothing leaves the request identical to one without this plugin.

## Known Limitations and Deferred Work

- **A bundle is extracted with no dependency, by a hand-written ZIP reader.** Node ships `zlib` but nothing that parses the ZIP container, so `src/zip.ts` reads the central directory itself. It enforces the same limits as the Python implementation — containment, an 8 MiB per-file and 64 MiB total cap, a suffix allowlist — and its tests read archives written by Python's `zipfile` rather than by itself. A deployment that would rather trust an audited library than 130 lines here should say so; the seam is one function.
- **Retrieval is not recorded as a session event** — the injected message carries its ids in `source.skillIds`, but which candidates were considered and rejected, and what the rewriter decided, exist only in the model calls. A deployment that needs to audit *why* a skill was or was not injected has to add that event.
- **The local scan is cached for the process lifetime** — `LocalSkillSource.invalidate()` exists but nothing calls it, so a `SKILL.md` written after the first retrieval is invisible until restart. Wiring it to a file watcher is deferred until a deployment edits skills in a live session.
- **The rewriter and the gate share one route** — one `provider`/`model` pair serves both, though the rewriter's job is far cheaper. Splitting them is deferred until a deployment shows the cost difference matters.
