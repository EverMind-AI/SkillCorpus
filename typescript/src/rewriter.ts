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

import { withTimeout } from './deadline.js'
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
const TIMEOUT_MS = 120_000

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

    const prompt = REWRITE_PROMPT.replace('{query}', truncated)
    let content: string
    try {
      content = await withTimeout(this.model.complete(prompt, { signal }), this.timeoutMs)
    } catch {
      return { needRetrieval: true, rewrittenQuery: '' }
    }
    return parse(content)
  }
}

function parse(content: string): RewriteResult {
  let text = content.trim()
  if (text.startsWith('```')) {
    const nl = text.indexOf('\n')
    text = nl === -1 ? '' : text.slice(nl + 1)
    if (text.endsWith('```')) text = text.slice(0, -3)
    text = text.trim()
  }
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return { needRetrieval: true, rewrittenQuery: '' }
  }
  if (typeof data !== 'object' || data === null) {
    return { needRetrieval: true, rewrittenQuery: '' }
  }
  const record = data as { need_retrieval?: unknown; rewritten_query?: unknown }
  const need = record.need_retrieval === undefined ? true : Boolean(record.need_retrieval)
  if (!need) return { needRetrieval: false, rewrittenQuery: '' }
  const rewritten =
    typeof record.rewritten_query === 'string' ? record.rewritten_query.trim() : ''
  return { needRetrieval: true, rewrittenQuery: rewritten }
}

