/**
 * The cross-language contract, as a test rather than a claim in a README.
 *
 * The two implementations in this repository are independent ports, so
 * nothing structural keeps them equal — only these numbers and these exact
 * strings do. Every value pinned below is what `engine-python/` produces for the
 * same input; the Python suite pins its side against the same literals.
 *
 * Written against `node:test` and importing only the pipeline modules,
 * which depend on nothing outside this directory. That is what lets CI run
 * it in a bare checkout, without a harness and without an install.
 */

import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { BM25Okapi, STOPWORD_MIN_CORPUS, tokenize } from '../src/bm25.ts'
import { bounded } from '../src/deadline.ts'
import { RRF_K, rrfMergeWeighted } from '../src/fusion.ts'
import { LLMGateFilter } from '../src/gate.ts'
import { LocalSkillSource, formatSkillText } from '../src/local-source.ts'
import { resolveRefs } from '../src/refs.ts'
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

test('the tokenizer cuts CJK into bigrams and keeps latin words whole', () => {
  assert.deepEqual(tokenize('git 分支 rebase'), ['git', '分支', 'rebase'])
  // Single latin characters are not tokens; a lone ideograph is, because a
  // one-character run has no bigram to stand in for it.
  assert.deepEqual(tokenize('a pdf 表'), ['pdf', '表'])
})

test('BM25 returns the scores the Python implementation returns', () => {
  const corpus = [
    'fill a pdf acroform with pdftk',
    'find the commit that broke a test 分支',
  ].map((text) => tokenize(text))

  const scores = new BM25Okapi(corpus).getScores(tokenize('pdftk acroform'))

  assert.equal(scores.length, 2)
  // Regenerated with the bigram tokenizer; 1.546938 was the unigram value.
  assert.equal(scores[0]!.toFixed(6), '1.498697')
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
  const model = capturing('{"rewritten_query": "q"}')
  await new QueryRewriter(model).analyze('QUERY')

  // 596 before the veto came out; the retrieval-or-not half of the prompt
  // went with it.
  assert.equal(Buffer.byteLength(model.prompt), 289)
  assert.ok(model.prompt.includes('Return JSON: {"rewritten_query": "..." or null}'))
  assert.ok(!model.prompt.includes('need_retrieval'), 'the veto is not asked for')
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
    rewrittenQuery: '',
  })
})

test('a model still emitting the old veto no longer stops the search', async () => {
  // A deployed prompt outlives the code change: models keep answering with
  // `need_retrieval` for as long as anything caches the old one, and
  // honouring it was the bug being removed.
  const refusing = { complete: async () => '{"need_retrieval": false, "rewritten_query": null}' }
  assert.deepEqual(await new QueryRewriter(refusing).analyze('fill in the acroform'), {
    rewrittenQuery: '',
  })
})

test('a query full of replacement patterns reaches the prompt verbatim', async () => {
  // String.replace reads $&, $' and $` in a *string* replacement as pattern
  // references; a user quoting shell's `$'...'` syntax must not splice
  // pieces of the prompt into itself.
  const query = "why does bash print $'\\n' here, and what does $& mean?"
  const model = capturing('{"need_retrieval": true}')
  await new QueryRewriter(model).analyze(query)

  assert.ok(model.prompt.endsWith(`\n${query}`))
})

test('the BM25 index text is byte-identical to the Python formatting', () => {
  // Python: " ".join([name, name, description]) — the name twice so a query
  // naming a skill outweighs a description mentioning the same words, and
  // no body. The index text decides the ranking, and the two
  // implementations promise the same ranking over the same directory.
  const skill = { name: 'pdf-tables', description: 'Extract tables.', content: 'x'.repeat(5000) }
  assert.equal(formatSkillText(skill), 'pdf-tables pdf-tables Extract tables.')
})

test('indexBody restores the capped body, as the Python flag does', () => {
  // For a corpus with thin descriptions. Python: parts.append(content[:4000]).
  const skill = { name: 'pdf-tables', description: 'Extract tables.', content: 'x'.repeat(5000) }
  assert.equal(
    formatSkillText(skill, true),
    `pdf-tables pdf-tables Extract tables. ${'x'.repeat(4000)}`,
  )
})

test('a nameless skill is named by its own directory, as in Python', async () => {
  // Python names a frontmatter-less skill after SKILL.md's parent directory.
  // Naming it after the first path segment under the root would instead
  // collapse every nameless skill below one grouping directory into one.
  const root = await mkdtemp(join(tmpdir(), 'skillsearch-'))
  await mkdir(join(root, 'group', 'my-skill'), { recursive: true })
  await writeFile(join(root, 'group', 'my-skill', 'SKILL.md'), 'Just a body.\n')

  const skills = await new LocalSkillSource([{ path: root, name: 'local' }]).listAll()

  assert.deepEqual(skills.map(s => s.name), ['my-skill'])
})

test('refs resolve exactly as the Python implementation resolves them', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'skillsearch-refs-'))
  await mkdir(join(dir, 'references'), { recursive: true })
  await mkdir(join(dir, 'scripts'), { recursive: true })
  await writeFile(join(dir, 'references', 'x.md'), 'ref')
  await writeFile(join(dir, 'scripts', 'y.sh'), 'run')

  const body = [
    'See [the notes](references/x.md#part) and run {baseDir}/scripts/y.sh.',
    'Missing: [gone](references/gone.md) and {baseDir}/scripts/gone.sh.',
    '```\n[fenced](references/x.md) stays literal\n```',
  ].join('\n')
  const { body: resolved, anyResolved } = resolveRefs(body, dir)

  assert.ok(anyResolved)
  assert.ok(resolved.includes(`[the notes](${dir}/references/x.md#part)`))
  assert.ok(resolved.includes(`run ${dir}/scripts/y.sh.`))
  // A ref whose target is not on disk stays literal, never a confident 404.
  assert.ok(resolved.includes('[gone](references/gone.md)'))
  assert.ok(resolved.includes('{baseDir}/scripts/gone.sh'))
  // Code fences are never rewritten.
  assert.ok(resolved.includes('[fenced](references/x.md) stays literal'))

  // Without a directory, placeholders are stripped to bare relative paths.
  const stripped = resolveRefs('run {baseDir}/scripts/y.sh', undefined)
  assert.deepEqual(stripped, { body: 'run scripts/y.sh', anyResolved: false })
})

test('a timed-out model call is hung up on, not just abandoned', async () => {
  let seen: AbortSignal | undefined
  const never = (signal: AbortSignal) => {
    seen = signal
    return new Promise<string>(() => {})
  }

  await assert.rejects(bounded(never, 10), /timed out after 10ms/)
  assert.equal(seen?.aborted, true)
})

test('the envelope is judged on both fields, as the Python client judges it', async () => {
  // Found by driving a fake catalog through both engines: checking only
  // `status` accepted a reply Python rejects, so one catalog answered
  // differently depending on which host asked.
  const { createServer } = await import('node:http')
  const { SkillHubClient } = await import('../src/hub-source.ts')

  const verdicts: Record<string, string> = {}
  for (const envelope of [
    { error: 'ok', status: 0, result: { items: [] } },
    { error: 'success', status: 0, result: { items: [] } },
    { error: '', status: 0, result: { items: [] } },
    { error: 'boom', status: 0, result: { items: [] } },
    { error: 'ok', status: 1, result: { items: [] } },
  ]) {
    const server = createServer((_request, response) => {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(envelope))
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    const port = typeof address === 'object' && address ? address.port : 0
    const key = `${envelope.error}/${envelope.status}`
    try {
      await new SkillHubClient(`http://127.0.0.1:${port}`).search('anything')
      verdicts[key] = 'accepted'
    } catch {
      verdicts[key] = 'rejected'
    }
    await new Promise<void>((resolve) => { server.close(() => resolve()) })
  }

  assert.deepEqual(verdicts, {
    'ok/0': 'accepted',
    'success/0': 'accepted',
    '/0': 'rejected',
    'boom/0': 'rejected',
    'ok/1': 'rejected',
  })
})

test('the corpus-adaptive stop words are the ones Python prunes', () => {
  // Python: {term | df/N > 0.5}, and nothing at all below ten documents.
  // Both numbers are shared, because the two implementations promise the
  // same ranking over the same directory — and a term pruned on one side
  // and scored on the other is a silent divergence in what gets injected.
  const index = new BM25Okapi(
    Array.from({ length: 12 }, (_, i) => tokenize(`skill for handling case ${i}`)),
  )
  assert.deepEqual([...index.stopwords].sort(), ['case', 'for', 'handling', 'skill'])
  assert.equal(Math.max(...index.getScores(tokenize('skill'))), 0)
})

test('a corpus under the guard prunes nothing, as in Python', () => {
  const docs = Array.from({ length: STOPWORD_MIN_CORPUS - 1 }, (_, i) =>
    tokenize(`shared term appears everywhere ${i}`))
  const index = new BM25Okapi(docs)
  assert.equal(index.stopwords.size, 0)
  assert.ok(Math.max(...index.getScores(tokenize('shared'))) > 0)
})

test('a distinguishing term still ranks after pruning', () => {
  const docs = Array.from({ length: 12 }, (_, i) => tokenize(`skill for handling case ${i}`))
  docs.push(tokenize('skill for parsing pdf acroforms'))
  const scores = new BM25Okapi(docs).getScores(tokenize('acroforms'))
  assert.equal(scores.indexOf(Math.max(...scores)), docs.length - 1)
})

test('a CJK run tokenizes to the same bigrams Python produces', () => {
  // Single ideographs did not carry enough meaning to rank on: 季度 matched
  // any document containing 季 or 度 anywhere, which is how a stock-research
  // skill outranked a slide-deck skill for "做个 PPT 讲下季度进展" over a
  // 46-skill corpus. The tokenizer is the one divergence no later step can
  // correct, so the lists are pinned side by side rather than described.
  assert.deepEqual(tokenize('季度进展'), ['季度', '度进', '进展'])
  assert.deepEqual(tokenize('做个 PPT 讲下季度进展'), [
    '做个', 'ppt', '讲下', '下季', '季度', '度进', '进展',
  ])
})

test('a lone ideograph stands alone, as in Python', () => {
  // A one-character run has no bigram, and dropping it would lose the only
  // token a one-character query has.
  assert.deepEqual(tokenize('图'), ['图'])
  assert.deepEqual(tokenize('看图 说话'), ['看图', '说话'])
})

test('a Latin run under two characters is still dropped', () => {
  assert.deepEqual(tokenize('a'), [])
  assert.deepEqual(tokenize('ab'), ['ab'])
})
