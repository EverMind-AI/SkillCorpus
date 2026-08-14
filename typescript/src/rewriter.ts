/**
 * Query rewriting, and the decision to search at all.
 *
 * One call answers both: whether this turn wants skills, and what to search
 * for if it does. A greeting or a follow-up needs nothing, and searching for
 * it spends a fan-out to rank noise. When the user does want something, their
 * words carry paths, ids and boilerplate that dilute a keyword match, so the
 * rewrite keeps the task, the domain and the capabilities and drops the rest.
 *
 * Every failure resolves to "search anyway" with the original words. Missing a
 * rewrite costs precision; skipping retrieval on a turn that wanted it costs
 * the skill entirely.
 *
 * @module
 */

import { bounded } from './deadline.js'
import { extractJsonObject } from './replies.js'
import type { RewriteResult } from './types.js'

const REWRITE_PROMPT = `Given a user query, first decide if it needs external skill/tool retrieval. \
Casual chat, greetings, simple follow-ups, and general knowledge tasks do not. \
Specialized tools, domain-specific workflows, or specific frameworks/APIs do.

If retrieval is needed, rewrite the query for skill retrieval. \
Remove noise (paths, IDs, timestamps, boilerplate). \
Keep task type, domain, required capabilities, and key technical details. \
Do NOT answer or solve the query — only rewrite it.

When in doubt, choose retrieval.

Return JSON: {"need_retrieval": true/false, "rewritten_query": "..." or null}

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
   * Judge whether `query` wants skills, and rewrite it for retrieval.
   *
   * Returns `needRetrieval: false` only when the model says so about a
   * non-empty query, or when the query is blank. Any transport or parse
   * failure returns `needRetrieval: true` with an empty rewrite.
   *
   * @param query - the user's words for this turn.
   * @param signal - aborts the call when the turn is cancelled.
   * @returns the verdict, and the rewrite when there is one.
   */
  async analyze(query: string, signal?: AbortSignal): Promise<RewriteResult> {
    const truncated = query.trim().slice(0, QUERY_MAX_LENGTH)
    if (!truncated) return { needRetrieval: false, rewrittenQuery: '' }

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
      return { needRetrieval: true, rewrittenQuery: '' }
    }
    return parse(content)
  }
}

function parse(content: string): RewriteResult {
  const data = extractJsonObject(content)
  if (data === undefined) return { needRetrieval: true, rewrittenQuery: '' }
  const record = data as { need_retrieval?: unknown; rewritten_query?: unknown }
  const need = record.need_retrieval === undefined ? true : Boolean(record.need_retrieval)
  if (!need) return { needRetrieval: false, rewrittenQuery: '' }
  const rewritten =
    typeof record.rewritten_query === 'string' ? record.rewritten_query.trim() : ''
  return { needRetrieval: true, rewrittenQuery: rewritten }
}

