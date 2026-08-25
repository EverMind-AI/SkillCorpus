/** Lightweight lexical guard for catalogs that always return Top K. */
import type { RouterHit } from './types.js'

export interface RelevanceCheck {
  readonly passed: boolean
  readonly matchedTerms: readonly string[]
  readonly requiredMatched: boolean
  readonly matchRatio: number
}

const STOP = new Set([
  'a', 'an', 'the', 'to', 'for', 'with', 'using', 'use', 'create', 'make', 'help', 'please',
  'and', 'or', 'of', 'in', 'on', 'my', 'me', 'i', 'want', 'need', 'how', 'can',
  'from', 'this', 'that', 'these', 'those', 'such', 'no',
  '帮我', '请', '一个', '一下', '如何', '怎么', '使用', '需要', '想要', '进行',
])
const ALIASES: Readonly<Record<string, readonly string[]>> = {
  k8s: ['kubernetes'], pr: ['pull', 'request'], ppt: ['powerpoint'], pptx: ['powerpoint'],
  postgres: ['postgresql'], transcription: ['transcribe'],
}
const GENERIC = new Set([
  'extract', 'review', 'deploy', 'deployment', 'generate', 'generator', 'analysis',
  'optimize', 'optimization', 'process', 'processing', 'data', 'code', 'task',
])

export function queryTerms(query: string): string[] {
  const chunks = query.toLowerCase().match(/[a-z0-9+#.-]+|[\p{Script=Han}]+/gu) ?? []
  const raw = chunks.flatMap(chunk => {
    if (!/^[\p{Script=Han}]+$/u.test(chunk) || chunk.length < 2) return [chunk]
    return Array.from({ length: chunk.length - 1 }, (_, index) => chunk.slice(index, index + 2))
  })
  const terms: string[] = []
  for (const token of raw) {
    if (STOP.has(token) || token.length < 2) continue
    const normalized = token.replace(/^[.-]+|[.-]+$/g, '')
    const expanded = ALIASES[normalized] ?? [stem(normalized)]
    for (const term of expanded) {
      if (term && !STOP.has(term) && !terms.includes(term)) terms.push(term)
    }
  }
  return terms
}

export function checkKeywordRelevance(
  query: string,
  hit: Pick<RouterHit, 'name' | 'meta'>,
): RelevanceCheck {
  const terms = queryTerms(query)
  if (terms.length === 0) {
    return { passed: false, matchedTerms: [], requiredMatched: false, matchRatio: 0 }
  }
  const tags = Array.isArray(hit.meta.tags) ? hit.meta.tags.join(' ') : ''
  const haystack = `${hit.name} ${String(hit.meta.description ?? '')} ${tags}`.toLowerCase()
  const matched = terms.filter(term => containsTerm(haystack, term))
  const required = terms.filter(term => !GENERIC.has(term))
  const requiredMatched = required.length === 0 || required.some(term => matched.includes(term))
  const minimum = terms.length >= 4 ? 2 : 1
  return {
    passed: requiredMatched && matched.length >= minimum,
    matchedTerms: matched,
    requiredMatched,
    matchRatio: matched.length / terms.length,
  }
}

function stem(token: string): string {
  if (token.endsWith('ies') && token.length > 4) return `${token.slice(0, -3)}y`
  if (token.endsWith('ing') && token.length > 5) return token.slice(0, -3)
  if (token.endsWith('ed') && token.length > 4) return token.slice(0, -2)
  if (token.endsWith('s') && token.length > 4 && !/(ss|us|is|es)$/.test(token)) {
    return token.slice(0, -1)
  }
  return token
}

function containsTerm(text: string, term: string): boolean {
  if (/^[a-z0-9+#.-]+$/.test(term)) {
    const special = '\\^$.*+?()[]{}|'
    const escaped = Array.from(term, char => special.includes(char) ? `\\\\${char}` : char).join('')
    return new RegExp(`(^|[^a-z0-9])${escaped}([^a-z0-9]|$)`, 'i').test(text)
  }
  return text.includes(term)
}
