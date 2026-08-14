import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { BM25Okapi, tokenize } from '@deepseek-ai/dsh-skill-search/src/bm25.ts'
import { rrfMergeWeighted } from '@deepseek-ai/dsh-skill-search/src/fusion.ts'
import { LLMGateFilter } from '@deepseek-ai/dsh-skill-search/src/gate.ts'
import { QueryRewriter } from '@deepseek-ai/dsh-skill-search/src/rewriter.ts'
import { LocalSkillSource } from '@deepseek-ai/dsh-skill-search/src/local-source.ts'
import { SkillSearchEngine } from '@deepseek-ai/dsh-skill-search/src/engine.ts'
import type { RouterHit, SkillSource } from '@deepseek-ai/dsh-skill-search/src/types.ts'

/** A model that answers with fixed text, and records what it was asked. */
function scriptedModel(reply: string | (() => Promise<string>)): {
  complete(prompt: string, options: { signal?: AbortSignal | undefined }): Promise<string>
  prompts: string[]
} {
  const prompts: string[] = []
  return {
    prompts,
    async complete(prompt) {
      prompts.push(prompt)
      return typeof reply === 'string' ? reply : reply()
    },
  }
}

function hit(id: string, name: string, extra: Partial<RouterHit> = {}): RouterHit {
  return { qualifiedId: id, name, content: `${name} body`, score: 1, meta: {}, ...extra }
}

function fixedSource(name: string, weight: number, hits: RouterHit[]): SkillSource {
  return { name, weight, search: async () => hits }
}

describe('tokenizer', () => {
  it('splits CJK per character while keeping latin words whole', () => {
    expect(tokenize('git 分支 rebase')).toEqual(['git', '分', '支', 'rebase'])
  })
})

describe('bm25', () => {
  const corpus = [
    'resolve a git merge conflict in a rebase',
    'generate a pdf invoice from a template',
  ].map(text => tokenize(text))

  it('scores the document whose rare terms match the query higher', () => {
    const scores = new BM25Okapi(corpus).getScores(tokenize('rebase conflict'))
    expect(scores[0]!).toBeGreaterThan(scores[1]!)
  })

  it('scores every document zero when no query term appears anywhere', () => {
    expect(new BM25Okapi(corpus).getScores(tokenize('kubernetes'))).toEqual([0, 0])
  })
})

describe('weighted RRF', () => {
  it('ranks by position, not by each source’s own score scale', () => {
    // `far` carries a hugely larger raw score but sits second in its source.
    const merged = rrfMergeWeighted(
      [
        { name: 'x', weight: 1, hits: [hit('x/near', 'near'), hit('x/far', 'far', { score: 999 })] },
      ],
      2,
      'name',
    )
    expect(merged.map(h => h.name)).toEqual(['near', 'far'])
  })

  it('lifts a skill that two sources both rank highly', () => {
    const merged = rrfMergeWeighted(
      [
        { name: 'a', weight: 1, hits: [hit('a/solo', 'solo'), hit('a/both', 'both')] },
        { name: 'b', weight: 1, hits: [hit('b/both', 'both'), hit('b/other', 'other')] },
      ],
      3,
      'name',
    )
    expect(merged[0]?.name).toBe('both')
  })

  it('keeps the higher-scoring copy when two sources carry the same skill', () => {
    const merged = rrfMergeWeighted(
      [
        { name: 'a', weight: 1, hits: [hit('a/dup', 'dup', { score: 1, content: 'thin' })] },
        { name: 'b', weight: 1, hits: [hit('b/dup', 'dup', { score: 5, content: 'full' })] },
      ],
      2,
      'name',
    )
    expect(merged).toHaveLength(1)
    expect(merged[0]?.content).toBe('full')
  })
})

describe('LLM gate', () => {
  it('keeps only the ids the model selected, in the model’s order', async () => {
    const model = scriptedModel('{"plan": "p", "skills": ["local/b"]}')
    const kept = await new LLMGateFilter(model).filter('task', [hit('local/a', 'a'), hit('local/b', 'b')])
    expect(kept.map(h => h.qualifiedId)).toEqual(['local/b'])
  })

  it('injects nothing when the model rejects every candidate', async () => {
    const model = scriptedModel('{"plan": "unrelated", "skills": []}')
    expect(await new LLMGateFilter(model).filter('weather', [hit('local/a', 'a')])).toEqual([])
  })

  it('lists the agent’s tools so the model can drop what it cannot run', async () => {
    const model = scriptedModel('{"plan": "p", "skills": []}')
    await new LLMGateFilter(model).filter('task', [hit('local/a', 'a')], ['read_file', 'exec'])
    expect(model.prompts[0]).toContain("The agent's ONLY available tools are: exec, read_file.")
  })

  it('falls back to the top candidates rather than to none when the model fails', async () => {
    const broken = scriptedModel(() => Promise.reject(new Error('transport down')))
    const kept = await new LLMGateFilter(broken, { fallbackTopK: 2 }).filter('task', [
      hit('local/a', 'a'),
      hit('local/b', 'b'),
      hit('local/c', 'c'),
    ])
    expect(kept.map(h => h.name)).toEqual(['a', 'b'])
  })

  it('reads a selection out of a reply wrapped in reasoning and a fence', async () => {
    const model = scriptedModel('<think>weighing</think>\n```json\n{"plan":"p","skills":["local/a"]}\n```')
    const kept = await new LLMGateFilter(model).filter('task', [hit('local/a', 'a')])
    expect(kept.map(h => h.qualifiedId)).toEqual(['local/a'])
  })
})

describe('query rewriter', () => {
  it('reports the turn wants no skills when the model says so', async () => {
    const model = scriptedModel('{"need_retrieval": false, "rewritten_query": null}')
    expect(await new QueryRewriter(model).analyze('hello')).toEqual({
      needRetrieval: false,
      rewrittenQuery: '',
    })
  })

  it('searches anyway when the reply cannot be parsed', async () => {
    const model = scriptedModel('I think you should try rebasing.')
    expect(await new QueryRewriter(model).analyze('fix my branch')).toEqual({
      needRetrieval: true,
      rewrittenQuery: '',
    })
  })

  it('skips the model entirely for a blank query', async () => {
    const model = scriptedModel('{"need_retrieval": true}')
    expect(await new QueryRewriter(model).analyze('   ')).toEqual({
      needRetrieval: false,
      rewrittenQuery: '',
    })
    expect(model.prompts).toEqual([])
  })
})

describe('local source', () => {
  it('finds a skill by its body and reports where its files live', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skill-search-'))
    await mkdir(join(root, 'pdf-fill'), { recursive: true })
    await writeFile(
      join(root, 'pdf-fill', 'SKILL.md'),
      '---\nname: pdf-fill\ndescription: Fill PDF forms\n---\n\nUse pdftk to fill an acroform.\n',
    )
    const hits = await new LocalSkillSource([{ path: root, name: 'local' }]).search('acroform pdftk', {}, 5)
    expect(hits).toHaveLength(1)
    expect(hits[0]?.qualifiedId).toBe('local/pdf-fill')
    expect(hits[0]?.meta.skillDir).toBe(join(root, 'pdf-fill'))
    expect(hits[0]?.content).not.toContain('---')
  })
})

describe('engine', () => {
  const twoSources = [
    fixedSource('local', 1, [hit('local/a', 'a')]),
    fixedSource('hub', 0.85, [hit('hub/b', 'b')]),
  ]

  it('renders the gate’s selection under the configured heading', async () => {
    const engine = new SkillSearchEngine(
      { sources: twoSources, gate: new LLMGateFilter(scriptedModel('{"plan":"p","skills":["hub/b"]}')) },
      { heading: '# Skills' },
    )
    const block = await engine.retrieve('anything')
    expect(block).toBe('# Skills\n\n### Skill: b  [hub/b]\n\n\nb body')
  })

  it('names a skill’s directory so relative refs in its body resolve', async () => {
    const engine = new SkillSearchEngine({
      sources: [fixedSource('local', 1, [hit('local/a', 'a', { meta: { skillDir: '/skills/a' } })])],
    })
    expect(await engine.retrieve('anything')).toContain('**Skill directory**: `/skills/a`')
  })

  it('skips the fan-out entirely when the rewriter says the turn wants no skills', async () => {
    let searched = false
    const engine = new SkillSearchEngine({
      sources: [
        {
          name: 'local',
          weight: 1,
          search: async () => {
            searched = true
            return [hit('local/a', 'a')]
          },
        },
      ],
      rewriter: new QueryRewriter(scriptedModel('{"need_retrieval": false}')),
    })
    expect(await engine.retrieve('hi')).toBe('')
    expect(searched).toBe(false)
  })

  it('searches with the rewritten words rather than the user’s', async () => {
    const queries: string[] = []
    const engine = new SkillSearchEngine({
      sources: [
        {
          name: 'local',
          weight: 1,
          search: async (q) => {
            queries.push(q)
            return [hit('local/a', 'a')]
          },
        },
      ],
      rewriter: new QueryRewriter(
        scriptedModel('{"need_retrieval": true, "rewritten_query": "pdf form filling"}'),
      ),
    })
    await engine.retrieve('can you fill in /tmp/x9f2.pdf for me')
    expect(queries).toEqual(['pdf form filling'])
  })

  it('keeps the other sources usable when one throws', async () => {
    const engine = new SkillSearchEngine({
      sources: [
        { name: 'hub', weight: 1, search: async () => { throw new Error('catalog down') } },
        fixedSource('local', 1, [hit('local/a', 'a')]),
      ],
    })
    expect(await engine.retrieve('anything')).toContain('[local/a]')
  })

  it('loads a body for a hit that arrived as metadata only', async () => {
    const engine = new SkillSearchEngine({
      sources: [fixedSource('hub', 1, [hit('hub/b', 'b', { content: '' })])],
      fetchBody: async () => 'downloaded body',
    })
    expect(await engine.retrieve('anything')).toContain('downloaded body')
  })

  it('passes this turn’s tools to the gate, which judges what the agent can run', async () => {
    const model = scriptedModel('{"plan":"p","skills":[]}')
    const engine = new SkillSearchEngine({
      sources: [fixedSource('local', 1, [hit('local/a', 'a')])],
      gate: new LLMGateFilter(model),
    })
    await engine.retrieve('anything', { availableTools: ['exec'] })
    expect(model.prompts[0]).toContain("The agent's ONLY available tools are: exec.")
  })

  it('injects nothing, and does not throw, when every source is empty', async () => {
    const engine = new SkillSearchEngine({ sources: [fixedSource('local', 1, [])] })
    expect(await engine.retrieve('anything')).toBe('')
  })
})
