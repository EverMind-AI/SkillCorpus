/**
 * Dependency-free Okapi BM25 over a small in-memory corpus.
 *
 * Keyword ranking for a few hundred short documents — skill descriptions and
 * bodies — with no index server and no native dependency. The tokenizer keeps
 * each CJK ideograph as its own token, so a Chinese query matches instead of
 * collapsing to nothing.
 *
 * @module
 */

/**
 * Runs of two or more alphanumerics, or a single CJK ideograph. Compiled once:
 * this runs per document on every turn.
 */
// Latin runs of two or more, or a maximal run of CJK ideographs — the run,
// not one character, because the run is what gets cut into bigrams below.
const TOKEN_RE = /[a-z0-9]{2,}|[一-鿿]+/g
const CJK_RE = /^[一-鿿]/

/**
 * Lowercase and split into scorable tokens: Latin words, and CJK bigrams.
 *
 * Byte-for-byte the same list as the Python `tokenize` for the same input.
 * Single ideographs did not carry enough meaning to rank on: measured over
 * 46 skills, "做个 PPT 讲下季度进展" ranked a stock-research skill first,
 * because 季 and 度 each matched separately in any long document holding
 * either. Bigrams put the slide-deck skill first.
 *
 * @param text - raw text from a query or a document.
 * @returns the tokens, in order, with everything unscorable dropped.
 */
export function tokenize(text: string): string[] {
  const out: string[] = []
  for (const run of text.toLowerCase().match(TOKEN_RE) ?? []) {
    if (!CJK_RE.test(run)) {
      out.push(run)
      continue
    }
    // Overlapping bigrams, so 季度进展 contributes 季度, 度进, 进展 and a query
    // naming any of those pairs matches on the pair rather than on either
    // character alone. A single ideograph has no bigram and stands alone.
    if (run.length === 1) out.push(run)
    else for (let i = 0; i < run.length - 1; i += 1) out.push(run.slice(i, i + 2))
  }
  return out
}

/**
 * Okapi BM25 with the Lucene default parameters.
 *
 * `score(D, Q) = Σ idf(qᵢ) · f(qᵢ,D) · (k1 + 1) / (f(qᵢ,D) + k1 · (1 − b + b · |D|/avgdl))`
 */
/** A term in more than this share of documents is treated as a stop word. */
export const STOPWORD_DF_RATIO = 0.5
/** Below this many documents, no term is pruned. See `BM25Okapi.stopwords`. */
export const STOPWORD_MIN_CORPUS = 10

export class BM25Okapi {
  private readonly k1: number
  private readonly b: number
  private readonly corpusSize: number
  private readonly avgdl: number
  /** Per document, its term frequencies and its length, kept together. */
  private readonly docs: { freqs: Map<string, number>; len: number }[]
  private readonly idf: Map<string, number>
  /**
   * Terms this corpus cannot distinguish on.
   *
   * A word in over half the documents carries no ranking signal here — in
   * a skills directory that is "skill", "run", "use", the vocabulary of
   * the format itself — but its idf stays just above zero, so every
   * document holding it still collects score and an unrelated query still
   * produces a confident-looking ranked list.
   *
   * Below `STOPWORD_MIN_CORPUS` documents this stays empty: on a corpus of
   * three, a term in two is over the threshold, and pruning the query down
   * to nothing is a worse answer than a weak ranking.
   */
  readonly stopwords: ReadonlySet<string>

  constructor(tokenizedCorpus: readonly (readonly string[])[], k1 = 1.5, b = 0.75) {
    this.k1 = k1
    this.b = b
    this.corpusSize = tokenizedCorpus.length
    this.avgdl = this.corpusSize
      ? tokenizedCorpus.reduce((a, d) => a + d.length, 0) / this.corpusSize
      : 0

    this.docs = []
    const df = new Map<string, number>()
    for (const doc of tokenizedCorpus) {
      const freqs = new Map<string, number>()
      for (const tok of doc) freqs.set(tok, (freqs.get(tok) ?? 0) + 1)
      this.docs.push({ freqs, len: doc.length })
      for (const tok of freqs.keys()) df.set(tok, (df.get(tok) ?? 0) + 1)
    }

    // Robertson-Spärck-Jones weighting. The leading `1 +` keeps the term
    // non-negative when a token appears in nearly every document.
    const n = this.corpusSize
    this.idf = new Map()
    const stopwords = new Set<string>()
    for (const [term, count] of df) {
      this.idf.set(term, Math.log(1 + (n - count + 0.5) / (count + 0.5)))
      if (n >= STOPWORD_MIN_CORPUS && count / n > STOPWORD_DF_RATIO) stopwords.add(term)
    }
    this.stopwords = stopwords
  }

  /** Score every document against the query. Index-aligned with the corpus. */
  /**
   * Score every document in the corpus against one query.
   * @param queryTokens - the tokenized query, from `tokenize`.
   * @returns one score per document, in corpus order; 0 where nothing matched.
   */
  getScores(queryTokens: readonly string[]): number[] {
    const scores = new Array<number>(this.corpusSize).fill(0)
    if (queryTokens.length === 0 || this.corpusSize === 0) return scores
    for (const term of queryTokens) {
      if (this.stopwords.has(term)) continue
      const idf = this.idf.get(term) ?? 0
      if (idf <= 0) continue
      for (const [i, doc] of this.docs.entries()) {
        const f = doc.freqs.get(term) ?? 0
        if (f === 0) continue
        const norm = this.k1 * (1 - this.b + (this.b * doc.len) / (this.avgdl || 1))
        scores[i] = (scores[i] ?? 0) + (idf * f * (this.k1 + 1)) / (f + norm)
      }
    }
    return scores
  }
}
