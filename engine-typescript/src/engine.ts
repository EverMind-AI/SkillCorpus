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
  readonly fetchBody?: (hit: RouterHit, signal?: AbortSignal) => Promise<string | undefined>
}

/** The retrieval pipeline: rewrite, fan out, fuse, hydrate, gate, render. */
export class SkillSearchEngine {
  private readonly sources: readonly SkillSource[]
  private readonly rewriter: QueryRewriter | undefined
  private readonly gate: LLMGateFilter | undefined
  private readonly fetchBody: EngineParts['fetchBody'] | undefined
  private readonly topK: number
  private readonly gatePool: number
  private readonly overFetch: number
  private readonly dedupBy: 'name' | 'qualifiedId'
  private readonly heading: string
  private readonly refs: boolean

  constructor(parts: EngineParts, options: EngineOptions = {}) {
    this.sources = parts.sources
    this.rewriter = parts.rewriter
    this.gate = parts.gate
    this.fetchBody = parts.fetchBody
    this.topK = options.topK ?? 5
    this.gatePool = options.gatePool ?? 10
    this.overFetch = options.overFetch ?? 2
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
      const verdict = await this.rewriter.analyze(query, signal)
      if (!verdict.needRetrieval) return []
      if (verdict.rewrittenQuery) searchQuery = verdict.rewrittenQuery
    }

    const poolSize = this.gate ? this.gatePool : this.topK
    const perSource = poolSize * this.overFetch
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

    let hits = rrfMergeWeighted(results, poolSize, this.dedupBy)
    if (hits.length === 0) return []

    hits = await this.hydrateBodies(hits, signal)
    if (this.gate) {
      hits = await this.gate.filter(query, hits, options.availableTools, signal)
    }
    // Only the survivors: resolution stats the disk per ref, so it waits
    // until the gate has decided what is actually going in.
    return this.resolveHitRefs(hits.slice(0, this.topK))
  }

  /** Rewrite each on-disk survivor's refs to absolute paths, when enabled. */
  private resolveHitRefs(hits: RouterHit[]): RouterHit[] {
    if (!this.refs) return hits
    return hits.map((hit) => {
      const skillDir = hit.meta.skillDir
      if (typeof skillDir !== 'string' || !skillDir || !hit.content) return hit
      const { body } = resolveRefs(hit.content, skillDir)
      return body === hit.content ? hit : { ...hit, content: body }
    })
  }

  /** Fill in bodies for hits a source returned as metadata only. */
  private async hydrateBodies(hits: RouterHit[], signal?: AbortSignal): Promise<RouterHit[]> {
    const fetchBody = this.fetchBody
    if (!fetchBody) return hits
    return Promise.all(
      hits.map(async (hit) => {
        if (hit.content) return hit
        try {
          const body = await fetchBody(hit, signal)
          return body ? { ...hit, content: body } : hit
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
