/**
 * Data shapes shared by the sources, the fusion step and the renderer.
 *
 * @module
 */

/** One ranked skill from one source, carrying everything the prompt needs. */
export interface RouterHit {
  /**
   * Globally unique, `<source>/<native id>` — `local/git-resolver`,
   * `hub/9f4a…`. The prefix is what lets a consumer route a hit back to the
   * backend it came from; source names never contain a slash, so the split is
   * unambiguous.
   */
  readonly qualifiedId: string
  /**
   * Display name, and the cross-source dedup key: two hits sharing a name are
   * one logical skill however many sources surfaced it.
   */
  readonly name: string
  /**
   * The SKILL.md body, frontmatter already stripped. Empty when a source has
   * metadata but no body yet — a catalog listing before its body is fetched.
   */
  readonly content: string
  /**
   * Relevance on the source's own scale — BM25 here, cosine there. Not
   * comparable across sources, which is exactly why fusion ranks by position
   * instead. Used only to pick the better of two hits that collide on name.
   */
  readonly score: number
  /** Source-specific extras: physical origin, native id, fusion telemetry. */
  readonly meta: Record<string, unknown>
}

/** Options a source receives for one lookup. */
export interface SearchOptions {
  /** Conversation so far, for sources that use it. Most ignore it. */
  readonly history?: readonly unknown[] | undefined
  /** Abort in flight when the turn is cancelled. */
  readonly signal?: AbortSignal | undefined
}

/**
 * One place skills come from.
 *
 * Implement `name`, `weight` and `search` and a source joins the fusion. Three
 * ship here — a local directory, a remote catalog, an agent's own accumulated
 * skills — and a host is free to add its own.
 */
export interface SkillSource {
  /** Stable identifier, used as the `qualifiedId` prefix and in telemetry. */
  readonly name: string
  /**
   * Rank weight in fusion. Higher means more pull when the same skill appears
   * in more than one source.
   */
  weight: number
  /** Return this source's own ranked list, best first, at most `k` long. */
  search(query: string, options: SearchOptions, k: number): Promise<RouterHit[]>
}

/** What the rewriter decides about a turn. */
export interface RewriteResult {
  /** False when the turn wants no skills at all, which skips the fan-out. */
  readonly needRetrieval: boolean
  /** The query to search with; empty means use the user's words unchanged. */
  readonly rewrittenQuery: string
}
