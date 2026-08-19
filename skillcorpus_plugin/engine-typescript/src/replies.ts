/**
 * Pulling a JSON object out of a model's reply.
 *
 * Both model calls in this package ask for "only JSON" and both get told
 * otherwise: a fenced block, a reasoning preamble, a sentence of commentary
 * after the closing fence. This is one extractor rather than two because the
 * two calls previously disagreed about how much of that to tolerate — the
 * gate handled all of it, the rewriter handled only a fence that ended the
 * string — and the difference stayed invisible until a live model appended a
 * sentence and the rewriter's verdict was silently discarded.
 *
 * @module
 */

const THINK = /<think>[\s\S]*?<\/think>/g
const FENCED = /```(?:json)?\s*\n?([\s\S]*?)\n?```/
const BRACED = /\{[\s\S]*\}/

/**
 * Find the one JSON object in a model reply.
 *
 * Tolerates, in order: a reasoning block, a fenced block anywhere in the
 * reply — including one followed by commentary — and a bare object embedded
 * in prose.
 *
 * @param content - the model's reply, verbatim.
 * @returns the decoded object, or `undefined` when the reply carries no JSON
 *   object at all. Callers decide what a missing object means; it never
 *   throws, because for both of them it is ordinary model behaviour with a
 *   defined fallback.
 */
export function extractJsonObject(content: string): Record<string, unknown> | undefined {
  const text = (content ?? '').replace(THINK, '').trim()
  if (!text) return undefined

  const candidates: string[] = []
  const fenced = FENCED.exec(text)
  if (fenced?.[1] !== undefined) candidates.push(fenced[1].trim())
  const braced = BRACED.exec(text)
  if (braced) candidates.push(braced[0])
  candidates.push(text)

  for (const candidate of candidates) {
    let data: unknown
    try {
      data = JSON.parse(candidate)
    } catch {
      continue
    }
    if (typeof data === 'object' && data !== null && !Array.isArray(data)) {
      return data as Record<string, unknown>
    }
  }
  return undefined
}
