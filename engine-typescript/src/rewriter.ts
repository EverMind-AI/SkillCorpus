/**
 * Query rewriting: a cleaning step, and nothing more.
 *
 * A user's words carry paths, ids and boilerplate that dilute a keyword
 * match, so the rewrite keeps the task, the domain and the capabilities and
 * drops the rest.
 *
 * It used to also decide whether to search at all, and that veto is gone.
 * The verdict was measured unstable — one query, six runs, true and false
 * three each — and it was reached without sight of a single candidate. The
 * gate sees the shortlist *and* the agent's tools, so it is the only step
 * with the standing to decide that nothing should be injected.
 *
 * Every failure resolves to "search the original words".
 *
 * @module
 */

import { bounded } from './deadline.js'
import { extractJsonObject } from './replies.js'
import type { RewriteResult } from './types.js'

const REWRITE_PROMPT = `Rewrite the following user query for skill retrieval. \
Remove noise (paths, IDs, timestamps, boilerplate). \
Keep task type, domain, required capabilities, and key technical details. \
Do NOT answer or solve the query — only rewrite it.

Return JSON: {"rewritten_query": "..." or null}

{query}`

const QUERY_MAX_LENGTH = 2000
/**
 * Tight on purpose. This is the first model call of the turn and it runs
 * before the gate, so a provider that stalls here stalls the whole turn.
 * Missing a rewrite costs precision; the raw query still searches.
 */
const TIMEOUT_MS = 5_000

/** How the rewriter reaches a model. Implemented by the plugin over `ctx.llm`. */
export interface RewriteModel {
  complete(prompt: string, options: { signal?: AbortSignal | undefined }): Promise<string>
}

/** The retrieval verdict and query rewrite, from one model call. */
export class QueryRewriter {
  private readonly model: RewriteModel
  private readonly timeoutMs: number

  constructor(model: RewriteModel, options: { timeoutMs?: number } = {}) {
    this.model = model
    this.timeoutMs = options.timeoutMs ?? TIMEOUT_MS
  }

  /**
   * Rewrite `query` for retrieval.
   *
   * @param query - the user's words for this turn.
   * @param signal - aborts the call when the turn is cancelled.
   * @returns the rewrite, or an empty one meaning "search the raw query".
   *   A blank query, a transport failure and an unparsable reply all land
   *   there; none of them stops the search.
   */
  async analyze(query: string, signal?: AbortSignal): Promise<RewriteResult> {
    const truncated = query.trim().slice(0, QUERY_MAX_LENGTH)
    if (!truncated) return { rewrittenQuery: '' }

    // A function replacement, never the string: `truncated` is the user's
    // text, and String.replace reads `$&`/`$'`/$` in a string replacement
    // as pattern references — a query containing shell's `$'` would splice
    // pieces of this prompt into itself.
    const prompt = REWRITE_PROMPT.replace('{query}', () => truncated)
    let content: string
    try {
      content = await bounded(
        s => this.model.complete(prompt, { signal: s }),
        this.timeoutMs,
        signal,
      )
    } catch {
      return { rewrittenQuery: '' }
    }
    return parse(content)
  }
}

function parse(content: string): RewriteResult {
  const data = extractJsonObject(content)
  if (data === undefined) return { rewrittenQuery: '' }
  const record = data as { rewritten_query?: unknown }
  const rewritten =
    typeof record.rewritten_query === 'string' ? record.rewritten_query.trim() : ''
  return { rewrittenQuery: rewritten }
}

