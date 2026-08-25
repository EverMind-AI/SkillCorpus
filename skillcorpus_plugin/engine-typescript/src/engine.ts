/**
 * The retrieval pipeline: query in, prompt block out.
 *
 * Rewrite, fan out across sources, fuse by weighted RRF, fetch bodies for
 * candidates that arrived as metadata, gate the pool down, render. The
 * rewriter and the gate are optional and absent without a model; the rest runs
 * regardless, so a directory of skills and nothing else still yields a block.
 *
 * `retrieve` never rejects. It runs before the model answers the user, so a
 * broken source, an unparseable gate reply or a slow catalog costs the turn
 * its skills and nothing more.
 *
 * @module
 */

import { rrfMergeWeighted, type SourceResult } from './fusion.js'
import { LLMGateFilter } from './gate.js'
import { resolveRefs } from './refs.js'
import { QueryRewriter } from './rewriter.js'
import type { RouterHit, SkillSource } from './types.js'

/** Deployment-fixed retrieval settings, chosen once when the engine is built. */
export interface EngineOptions {
  /** Final size of the injected set. */
  readonly topK?: number
  /** Candidates shown to the gate. Larger gives it more to reject. */
  readonly gatePool?: number
  /** Multiplier on what each source is asked for before fusion narrows. */
  readonly overFetch?: number
  /** Hard upper bound requested from each source before fusion. */
  readonly perSourceMax?: number
  /** Rank-damping offset for fusion. Defaults to the paper's 60. */
  readonly rrfK?: number
  /** Collapse key across sources. */
  readonly dedupBy?: 'name' | 'qualifiedId'
  /** Heading of the rendered block. */
  readonly heading?: string
  /**
   * Rewrite `{baseDir}` and bundled-file links in selected bodies to absolute
   * paths under the skill's directory. On by default; turn it off when the
   * host and the skills do not share a filesystem, where an absolute path
   * would promise files the model cannot open.
   */
  readonly resolveRefs?: boolean
}

/** Per-turn inputs, which vary by agent and by cancellation. */
export interface RetrieveOptions {
  /** Abort in flight when the turn is cancelled. */
  readonly signal?: AbortSignal | undefined
  /**
   * The tools this agent can call, for the gate's environment check. Omitted,
   * the gate still judges relevance but cannot reject a skill whose workflow
   * needs something this agent lacks.
   */
  readonly availableTools?: readonly string[] | undefined
}

/** The pieces the pipeline runs on. Only `sources` is required. */
export interface EngineParts {
  readonly sources: readonly SkillSource[]
  readonly rewriter?: QueryRewriter
  readonly gate?: LLMGateFilter
  /** Loads a remote body once the gate has kept its hit. */
  /**
   * Load a remote body before the gate judges it.
   *
   * Returning the catalog record alongside the body lets `materialise` reuse
   * it: the install would otherwise fetch the same detail a second time, one
   * request per selected skill. Returning a bare string still works.
   */
  readonly fetchBody?: (
    hit: RouterHit,
    signal?: AbortSignal,
  ) => Promise<string | { body?: string; record?: Record<string, unknown> } | undefined>
  /**
   * Put a selected remote skill's own files on disk and say where.
   *
   * A catalog skill's body says `run scripts/x.py`; until its bundle is
   * extracted that path names nothing, so a skill arrives readable and not
   * runnable. Given this, retrieval extracts after the gate — one download
   * per skill that is actually going in, never per candidate — and resolves
   * the body's references against the result.
   *
   * Absent, remote skills keep their unresolved bodies. So does a hit whose
   * install fails: the agent loses the absolute paths, not the instructions.
   */
  readonly materialise?: (
    hit: RouterHit,
    signal?: AbortSignal,
  ) => Promise<{ dir: string; body?: string } | undefined>
}

/** The retrieval pipeline: rewrite, fan out, fuse, hydrate, gate, render. */
export class SkillSearchEngine {
  private readonly sources: readonly SkillSource[]
  private readonly rewriter: QueryRewriter | undefined
  private readonly gate: LLMGateFilter | undefined
  private readonly fetchBody: EngineParts['fetchBody'] | undefined
  private readonly materialise: EngineParts['materialise'] | undefined
  private readonly topK: number
  private readonly rrfK: number | undefined
  private readonly gatePool: number
  private readonly overFetch: number
  private readonly perSourceMax: number
  private readonly dedupBy: 'name' | 'qualifiedId'
  private readonly heading: string
  private readonly refs: boolean

  constructor(parts: EngineParts, options: EngineOptions = {}) {
    this.sources = parts.sources
    this.rewriter = parts.rewriter
    this.gate = parts.gate
    this.fetchBody = parts.fetchBody
    this.materialise = parts.materialise
    this.topK = options.topK ?? 2
    this.rrfK = options.rrfK
    this.gatePool = options.gatePool ?? 10
    this.overFetch = options.overFetch ?? 2
    this.perSourceMax = options.perSourceMax ?? 2
    this.dedupBy = options.dedupBy ?? 'name'
    this.heading = options.heading ?? '# Skills'
    this.refs = options.resolveRefs ?? true
  }

  /** Whether anything is configured to search. */
  get enabled(): boolean {
    return this.sources.length > 0
  }

  /**
   * Search for `query` and render what survives.
   * @param query - the user's words for this turn.
   * @param options - this turn's cancellation and tool list.
   * @returns the block to inject, or `''` when this turn gets no skills.
   */
  async retrieve(query: string, options: RetrieveOptions = {}): Promise<string> {
    const hits = await this.hits(query, options)
    return hits.length === 0 ? '' : this.render(hits)
  }

  /**
   * Run the pipeline and return the selection unrendered.
   * @param query - the user's words for this turn.
   * @param options - this turn's cancellation and tool list.
   * @returns the selected skills, empty on any failure; never rejects.
   */
  async hits(query: string, options: RetrieveOptions = {}): Promise<RouterHit[]> {
    if (!this.enabled || !query.trim()) return []
    try {
      return await this.run(query, options)
    } catch {
      return []
    }
  }

  private async run(query: string, options: RetrieveOptions): Promise<RouterHit[]> {
    const signal = options.signal
    let searchQuery = query
    if (this.rewriter) {
      // Only a cleaner query comes back. Deciding that a turn wants no
      // skills belongs to the gate, which sees the shortlist and the
      // agent's tools; the rewriter sees neither.
      const { rewrittenQuery } = await this.rewriter.analyze(query, signal)
      if (rewrittenQuery) searchQuery = rewrittenQuery
    }

    const poolSize = this.gate ? this.gatePool : this.topK
    const perSource = Math.min(this.perSourceMax, poolSize * this.overFetch)
    const results = await Promise.all(
      this.sources.map(async (source): Promise<SourceResult> => {
        try {
          const hits = await source.search(searchQuery, signal ? { signal } : {}, perSource)
          return { name: source.name, weight: source.weight, hits }
        } catch {
          // One source being down leaves the others usable.
          return { name: source.name, weight: source.weight, hits: [] }
        }
      }),
    )

    let hits = this.rrfK === undefined
      ? rrfMergeWeighted(results, poolSize, this.dedupBy)
      : rrfMergeWeighted(results, poolSize, this.dedupBy, this.rrfK)
    if (hits.length === 0) return []

    hits = await this.hydrateBodies(hits, signal)
    hits = hits.filter(hit => !['clawhub', 'skillhub_cn'].includes(String(hit.meta.source)) || Boolean(hit.content))
    if (hits.length === 0) return []

    // Before the gate, and only for skills already on disk. The gate is
    // told to reject a skill whose files it cannot see, and an unresolved
    // `{baseDir}/scripts/x.py` reads exactly like one — so a local skill
    // that ships its own files was rejected for shipping them. A few stats
    // and no network.
    hits = this.resolveLocalRefs(hits)

    if (this.gate) {
      hits = await this.gate.filter(query, hits, options.availableTools, signal)
    }
    // The remote half stays here: extracting a bundle is a download, so it
    // waits until the gate has decided what is actually going in.
    return this.resolveHitRefs(hits.slice(0, this.topK), signal)
  }

  /** Rewrite refs for hits that already know their directory. */
  private resolveLocalRefs(hits: RouterHit[]): RouterHit[] {
    if (!this.refs) return hits
    return hits.map((hit) => {
      const skillDir = hit.meta.skillDir
      if (typeof skillDir !== 'string' || !skillDir || !hit.content) return hit
      const { body } = resolveRefs(hit.content, skillDir)
      return body === hit.content ? hit : { ...hit, content: body }
    })
  }

  /**
   * Give each survivor a directory, then rewrite its refs against it.
   *
   * A local hit was already resolved before the gate; this pass exists for
   * the remote ones, whose bundle is extracted first when the host
   * supplied a way to. A failure there leaves the body unresolved.
   */
  private async resolveHitRefs(hits: RouterHit[], signal?: AbortSignal): Promise<RouterHit[]> {
    if (!this.refs) return hits
    return Promise.all(hits.map(async (hit) => {
      let current = hit
      if (typeof current.meta.skillDir !== 'string' && this.materialise) {
        try {
          const installed = await this.materialise(current, signal)
          if (installed) {
            current = {
              ...current,
              content: installed.body || current.content,
              meta: { ...current.meta, skillDir: installed.dir },
            }
          }
        } catch {
          // Unresolved paths, not a lost skill. The body still instructs.
          return current
        }
      }
      const skillDir = current.meta.skillDir
      if (typeof skillDir !== 'string' || !skillDir || !current.content) return current
      const { body } = resolveRefs(current.content, skillDir)
      return body === current.content ? current : { ...current, content: body }
    }))
  }

  /** Fill in bodies for hits a source returned as metadata only. */
  private async hydrateBodies(hits: RouterHit[], signal?: AbortSignal): Promise<RouterHit[]> {
    const fetchBody = this.fetchBody
    if (!fetchBody) return hits
    return Promise.all(
      hits.map(async (hit) => {
        if (hit.content) return hit
        try {
          const out = await fetchBody(hit, signal)
          if (!out) return hit
          if (typeof out === 'string') return { ...hit, content: out }
          // The record rides in meta so `materialise` can hand it to the
          // install instead of fetching the same detail again. Mirrors the
          // Python engine's `meta["_fetched"]`.
          const next = { ...hit }
          if (out.body) next.content = out.body
          if (out.record) next.meta = { ...hit.meta, _fetched: out.record }
          return next
        } catch {
          return hit
        }
      }),
    )
  }

  /**
   * Render hits into the injected block.
   *
   * A hit whose files are on disk gets its directory named and a sentence
   * telling the model how to reach them; a body saying `scripts/x.sh` is
   * otherwise read as relative to the agent's cwd.
   *
   * @param hits - the selection, in the order the model should see it.
   * @returns the model-facing block, or `''` when no hit carried a body.
   */
  render(hits: readonly RouterHit[]): string {
    const parts: string[] = []
    for (const hit of hits) {
      const skillDir = hit.meta.skillDir as string | undefined
      const header = skillDir
        ? `### Skill: ${hit.name}  [${hit.qualifiedId}]\n` +
          `**Skill directory**: \`${skillDir}\`\n` +
          'Relative refs (e.g. `references/x.md`, `./scripts/y.sh`) ' +
          'resolve under this directory — use the absolute form for ' +
          'read_file / exec.\n'
        : `### Skill: ${hit.name}  [${hit.qualifiedId}]\n`
      parts.push(header)
      const content = hit.content.trim()
      if (content) parts.push(content)
    }
    const body = parts.join('\n\n')
    return body ? `${this.heading}\n\n${body}` : ''
  }
}
