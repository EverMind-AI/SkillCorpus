/**
 * The plugin over a real Loader tree and the real `llm` service.
 *
 * The unit suite drives the pipeline directly; this boots it the way a
 * deployment does — a `cordis.yml` row, the runtime's own `agent/pre-step`
 * dispatcher, the real `LlmRuntime` behind a scripted adapter — and asserts on
 * the message the model would actually receive.
 */

import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import { afterEach, describe, expect, it } from 'vitest'
import { LlmAdapter, LlmRuntime, type LlmResolvedModelInfo, type StreamChunk } from '@deepseek-ai/dsh-llm'
import { agentEvents } from '@deepseek-ai/dsh-agent'
import type { Agent, PreStepDecision } from '@deepseek-ai/dsh-agent'
import type { UserMessage } from '@deepseek-ai/dsh-session'
import * as skillSearch from '@deepseek-ai/dsh-skill-search/src/index.ts'

const PROVIDER = 'scripted'
const MODEL = 'scripted-model'

/** Replies from a queue, so one boot can script the rewriter then the gate. */
class ScriptedAdapter extends LlmAdapter {
  readonly prompts: string[] = []

  constructor(private readonly replies: string[]) {
    super()
  }

  override resolveModel(provider: string, model: string): Promise<LlmResolvedModelInfo> {
    return Promise.resolve({ provider, id: model, name: model })
  }

  override async * stream(options: { messages: readonly { content: readonly unknown[] }[] }): AsyncIterable<StreamChunk> {
    const blocks = options.messages.at(-1)?.content ?? []
    this.prompts.push(blocks.map(block => (block as { text?: string }).text ?? '').join(''))
    const text = this.replies.shift() ?? ''
    yield { type: 'block-start', index: 0, blockType: 'text' }
    yield { type: 'text-delta', index: 0, text }
    yield { type: 'block-end', index: 0, block: { type: 'text', text } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
}

const disposers: (() => Promise<void>)[] = []

afterEach(async () => {
  for (const dispose of disposers.splice(0)) await dispose()
})

/** A directory of skills, written fresh so the scan has something to find. */
function skillsDir(): string {
  const root = mkdtempSync(join(tmpdir(), 'dsh-skill-search-'))
  mkdirSync(join(root, 'pdf-forms'))
  writeFileSync(
    join(root, 'pdf-forms', 'SKILL.md'),
    '---\nname: pdf-forms\ndescription: Fill PDF acroforms\n---\n\nRun `pdftk` with an FDF to fill an acroform.\n',
  )
  mkdirSync(join(root, 'git-bisect'))
  writeFileSync(
    join(root, 'git-bisect', 'SKILL.md'),
    '---\nname: git-bisect\ndescription: Find the commit that broke a test\n---\n\nRun `git bisect start`, then mark good and bad.\n',
  )
  return root
}

/**
 * Boot a Loader tree carrying the real `llm` service and this plugin.
 * @param replies - what the scripted model returns, rewriter call first.
 * @param configYaml - the plugin's `config:` block lines, already indented.
 * @returns the booted context and the adapter that recorded the prompts.
 */
async function boot(
  replies: string[],
  configYaml: string[],
): Promise<{ ctx: Context; adapter: ScriptedAdapter }> {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-skill-search-tree-'))
  // The Loader imports through Node's resolver, which would reach the
  // unbuilt `lib/`. This fixture row delegates to the source-plane plugin the
  // test already imported, exactly as the headless startup suite does.
  writeFileSync(join(dir, 'row.mjs'), `
export const name = '${skillSearch.name}'
export const inject = ${JSON.stringify(skillSearch.inject)}
export const Config = globalThis.__skillSearch.Config
export const apply = (ctx, config) => globalThis.__skillSearch.apply(ctx, config)
`)
  writeFileSync(join(dir, 'cordis.yml'), [
    '- id: skill-search',
    `  name: ${pathToFileURL(join(dir, 'row.mjs')).href}`,
    '  config:',
    ...configYaml,
    '',
  ].join('\n'))

  ;(globalThis as unknown as { __skillSearch: typeof skillSearch }).__skillSearch = skillSearch

  const ctx = new Context()
  const adapter = new ScriptedAdapter(replies)
  void new LlmRuntime(ctx)
  ctx.llm.registerAdapter([PROVIDER], adapter)
  ctx.provide('agents')
  ctx.set('agents', {} as never)
  await ctx.plugin(Loader)
  ctx.loader.builtins.include = Include
  await ctx.loader.create({ name: 'cordis:include', config: { path: pathToFileURL(join(dir, 'cordis.yml')).href } })
  await ctx.loader.await()
  disposers.push(async () => { await ctx.fiber.dispose() })
  return { ctx, adapter }
}

/** Run one step through the runtime's own dispatcher, as the loop does. */
async function preStep(ctx: Context, text: string): Promise<PreStepDecision> {
  const agent = { id: 'main', options: {} } as unknown as Agent
  const messages = [
    { id: 'm1', role: 'user', content: [{ type: 'text', text }], source: { kind: 'user' } } as unknown as UserMessage,
  ]
  return agentEvents(ctx, agent).waterfall(
    'agent/pre-step',
    { messages, turn: 1, step: 1, signal: new AbortController().signal },
    () => Promise.resolve<PreStepDecision>({ kind: 'enter', messages }),
  )
}

/** The text of the message this plugin appended, or `undefined`. */
function injected(decision: PreStepDecision): string | undefined {
  if (decision.kind === 'reject') return undefined
  const last = decision.messages.at(-1)
  if (last?.source.kind !== 'skill-search') return undefined
  return last.content.map(block => (block as { text?: string }).text ?? '').join('')
}

describe('skill-search over a real Loader tree', () => {
  it('injects the gated skill, tagged with the ids it injected', async () => {
    const { ctx } = await boot(
      [
        '{"need_retrieval": true, "rewritten_query": "fill pdf acroform"}',
        '{"plan": "fill the form", "skills": ["local/pdf-forms"]}',
      ],
      [`    skillsDirs: ['${skillsDir()}']`, `    provider: ${PROVIDER}`, `    model: ${MODEL}`],
    )
    const decision = await preStep(ctx, 'can you fill in /tmp/a7f2.pdf for me')
    const text = injected(decision)
    expect(text).toContain('### Skill: pdf-forms  [local/pdf-forms]')
    expect(text).toContain('pdftk')
    expect(text).not.toContain('git-bisect')

    const message = (decision as { messages: UserMessage[] }).messages.at(-1)!
    expect(message.source).toMatchObject({ kind: 'skill-search', form: 'instructions', skillIds: ['local/pdf-forms'] })
  })

  it('leaves the step untouched when the gate selects nothing', async () => {
    const { ctx } = await boot(
      ['{"need_retrieval": true, "rewritten_query": "weather"}', '{"plan": "just answer", "skills": []}'],
      [`    skillsDirs: ['${skillsDir()}']`, `    provider: ${PROVIDER}`, `    model: ${MODEL}`],
    )
    const decision = await preStep(ctx, "what's the weather in Beijing")
    expect(injected(decision)).toBeUndefined()
    expect((decision as { messages: UserMessage[] }).messages).toHaveLength(1)
  })

  it('never calls the model when the rewriter says the turn wants no skills', async () => {
    const { ctx, adapter } = await boot(
      ['{"need_retrieval": false, "rewritten_query": null}'],
      [`    skillsDirs: ['${skillsDir()}']`, `    provider: ${PROVIDER}`, `    model: ${MODEL}`],
    )
    const decision = await preStep(ctx, 'hi')
    expect(injected(decision)).toBeUndefined()
    // One call: the rewriter. The gate never runs, because nothing was searched.
    expect(adapter.prompts).toHaveLength(1)
  })

  it('injects by rank alone when no model route is configured', async () => {
    const { ctx, adapter } = await boot([], [`    skillsDirs: ['${skillsDir()}']`, '    topK: 1'])
    const decision = await preStep(ctx, 'pdftk acroform')
    expect(injected(decision)).toContain('[local/pdf-forms]')
    expect(adapter.prompts).toEqual([])
  })

  it('registers no hook at all when nothing is configured to search', async () => {
    const { ctx } = await boot([], ['    skillsDirs: []'])
    const decision = await preStep(ctx, 'pdftk acroform')
    expect(injected(decision)).toBeUndefined()
  })

  it('does not inject onto a step another listener rejected', async () => {
    const { ctx } = await boot(
      ['{"need_retrieval": true, "rewritten_query": "pdf"}', '{"plan": "p", "skills": ["local/pdf-forms"]}'],
      [`    skillsDirs: ['${skillsDir()}']`, `    provider: ${PROVIDER}`, `    model: ${MODEL}`],
    )
    const agent = { id: 'main', options: {} } as unknown as Agent
    const decision = await agentEvents(ctx, agent).waterfall(
      'agent/pre-step',
      {
        messages: [{ id: 'm1', role: 'user', content: [{ type: 'text', text: 'fill the pdf' }], source: { kind: 'user' } } as unknown as UserMessage],
        turn: 1,
        step: 1,
        signal: new AbortController().signal,
      },
      () => Promise.resolve<PreStepDecision>({ kind: 'reject' }),
    )
    expect(decision.kind).toBe('reject')
  })
})
