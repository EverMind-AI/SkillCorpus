/**
 * The cross-language contract, as a test rather than a claim in a README.
 *
 * The two implementations in this repository are independent ports, so
 * nothing structural keeps them equal — only these numbers and these exact
 * strings do. Every value pinned below is what `python/` produces for the
 * same input; the Python suite pins its side against the same literals.
 *
 * Written against `node:test` and importing only the pipeline modules,
 * which depend on nothing outside this directory. That is what lets CI run
 * it in a bare checkout, without a harness and without an install.
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import { BM25Okapi, tokenize } from '../src/bm25.ts'
import { RRF_K, rrfMergeWeighted } from '../src/fusion.ts'
import { LLMGateFilter } from '../src/gate.ts'
import { QueryRewriter } from '../src/rewriter.ts'
import type { RouterHit } from '../src/types.ts'

function hit(qualifiedId: string, name: string, score: number, content = ''): RouterHit {
  return { qualifiedId, name, content, score, meta: {} }
}

/** Captures the one prompt it is sent, and answers with fixed text. */
function capturing(reply: string): {
  complete(prompt: string, options: { signal?: AbortSignal | undefined }): Promise<string>
  prompt: string
} {
  const box = {
    prompt: '',
    async complete(prompt: string) {
      box.prompt = prompt
      return reply
    },
  }
  return box
}

test('the tokenizer splits CJK per character and keeps latin words whole', () => {
  assert.deepEqual(tokenize('git 分支 rebase'), ['git', '分', '支', 'rebase'])
  // Single latin characters are not tokens; a lone ideograph is.
  assert.deepEqual(tokenize('a pdf 表'), ['pdf', '表'])
})

test('BM25 returns the scores the Python implementation returns', () => {
  const corpus = [
    'fill a pdf acroform with pdftk',
    'find the commit that broke a test 分支',
  ].map((text) => tokenize(text))

  const scores = new BM25Okapi(corpus).getScores(tokenize('pdftk acroform'))

  assert.equal(scores.length, 2)
  assert.equal(scores[0]!.toFixed(6), '1.546938')
  assert.equal(scores[1]!.toFixed(6), '0.000000')
})

test('the fusion constant is the one both implementations use', () => {
  assert.equal(RRF_K, 60)
})

test('weighted RRF returns the order and the values Python returns', () => {
  const merged = rrfMergeWeighted(
    [
      { name: 'a', weight: 1.0, hits: [hit('a/x', 'x', 9), hit('a/y', 'y', 1)] },
      { name: 'b', weight: 0.85, hits: [hit('b/y', 'y', 2), hit('b/z', 'z', 1)] },
    ],
    5,
    'name',
  )

  assert.deepEqual(
    merged.map((h) => `${h.name}:${(h.meta.rrfScore as number).toFixed(8)}`),
    ['y:0.03006346', 'x:0.01639344', 'z:0.01370968'],
  )
})

test('a collision keeps the better-ranked copy, not the higher-scored one', () => {
  // The rule that raw scores get wrong: BM25 is unbounded while a catalog
  // score sits in 0..1, so comparing them hands every collision to the
  // local source. Here the hub ranked it first and local ranked it third.
  const merged = rrfMergeWeighted(
    [
      {
        name: 'local',
        weight: 1,
        hits: [
          hit('local/a', 'a', 8),
          hit('local/b', 'b', 7),
          hit('local/shared', 'shared', 6, 'local copy'),
        ],
      },
      { name: 'hub', weight: 1, hits: [hit('hub/shared', 'shared', 0.9, 'hub copy')] },
    ],
    5,
    'name',
  )

  assert.equal(merged.find((h) => h.name === 'shared')!.content, 'hub copy')
})

test('the gate prompt is byte-identical to the Python one', async () => {
  const model = capturing('{"plan":"p","skills":[]}')
  await new LLMGateFilter(model, { maxSelect: 2 }).filter(
    'TASK',
    [hit('local/a', 'a', 1, 'body text here')].map((h) => ({
      ...h,
      meta: { description: 'desc' },
    })),
    ['exec', 'read_file'],
  )

  assert.equal(Buffer.byteLength(model.prompt), 2247)
  assert.ok(model.prompt.startsWith('You are a skill selector for an autonomous agent.\n\n# Task\n\nTASK\n\n'))
  assert.ok(model.prompt.includes("The agent's ONLY available tools are: exec, read_file."))
  assert.ok(model.prompt.endsWith('Use the EXACT qualified_id strings from the candidate list above.'))
})

test('the rewrite prompt is byte-identical to the Python one', async () => {
  const model = capturing('{"need_retrieval": true}')
  await new QueryRewriter(model).analyze('QUERY')

  assert.equal(Buffer.byteLength(model.prompt), 596)
  assert.ok(model.prompt.includes('Return JSON: {"need_retrieval": true/false, "rewritten_query": "..." or null}'))
  assert.ok(model.prompt.endsWith('\nQUERY'))
})

test('a gate that cannot answer keeps candidates rather than dropping to none', async () => {
  const broken = {
    complete: () => Promise.reject(new Error('transport down')),
  }
  const kept = await new LLMGateFilter(broken, { fallbackTopK: 2 }).filter('task', [
    hit('local/a', 'a', 1),
    hit('local/b', 'b', 1),
    hit('local/c', 'c', 1),
  ])

  assert.deepEqual(kept.map((h) => h.name), ['a', 'b'])
})

test('a rewriter that cannot answer still searches', async () => {
  const garbage = { complete: async () => 'I think you should try rebasing.' }
  assert.deepEqual(await new QueryRewriter(garbage).analyze('fix my branch'), {
    needRetrieval: true,
    rewrittenQuery: '',
  })
})
