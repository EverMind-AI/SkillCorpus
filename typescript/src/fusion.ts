/**
 * Weighted reciprocal-rank fusion across sources.
 *
 * Sources score on incomparable scales — BM25 magnitudes against cosine
 * similarities — so fusion reads position instead of value. A hit's
 * contribution is `weight / (RRF_K + rank)`, which makes a source's own
 * ordering the only thing that transfers.
 *
 * The consequence is worth knowing before tuning weights: a source's
 * top-ranked hit lands high in the fused list even when it matched the query
 * poorly, because rank 1 in a weak list scores the same as rank 1 in a strong
 * one. Removing those is the relevance gate's job, not fusion's.
 *
 * @module
 */

import type { RouterHit } from './types.js'

/**
 * Rank offset damping the head of each list. 60 is the value from the original
 * RRF paper and the default everywhere it is implemented; changing it shifts
 * how much a rank-1 hit outweighs a rank-2 hit.
 */
export const RRF_K = 60

/** One source's contribution to a fusion: its name, its weight, its ranking. */
export interface SourceResult {
  readonly name: string
  readonly weight: number
  readonly hits: readonly RouterHit[]
}

/**
 * Fuse per-source rankings into one list of at most `k` hits.
 *
 * Hits collapse on `dedupBy` — `name` by default, so one skill surfaced by
 * three sources becomes one entry whose score sums all three contributions.
 * The surviving record is the colliding hit with the highest source score, so
 * the prompt shows the best instance rather than whichever arrived first.
 *
 * Each returned hit carries `rrfScore` and `contributingSources` in `meta`.
 * Ties keep encounter order, making the output deterministic for a given
 * input.
 *
 * @param sourceResults - each source's ranking, its name and its weight.
 * @param k - upper bound on the fused list.
 * @param dedupBy - the field two hits must share to collapse into one.
 * @returns the fused hits, best first, at most `k` long.
 */
export function rrfMergeWeighted(
  sourceResults: readonly SourceResult[],
  k: number,
  dedupBy: 'name' | 'qualifiedId' = 'name',
): RouterHit[] {
  /** One accumulator per dedup key: the fused score, the best hit, its sources. */
  const merged = new Map<string, { score: number; best: RouterHit; sources: string[] }>()

  for (const { name: sourceName, weight, hits } of sourceResults) {
    for (const [i, hit] of hits.entries()) {
      const rank = i + 1
      const key = hit[dedupBy]
      const contribution = weight / (RRF_K + rank)
      const seen = merged.get(key)
      if (seen === undefined) {
        merged.set(key, { score: contribution, best: hit, sources: [sourceName] })
        continue
      }
      seen.score += contribution
      seen.sources.push(sourceName)
      if (hit.score > seen.best.score) seen.best = hit
    }
  }

  // Map iteration preserves insertion order, so a stable sort leaves ties in
  // the order their keys were first encountered.
  return [...merged.values()]
    .sort((a, b) => b.score - a.score)
    .slice(0, k)
    .map(({ score, best, sources }) => ({
      ...best,
      meta: { ...best.meta, rrfScore: score, contributingSources: [...sources] },
    }))
}
