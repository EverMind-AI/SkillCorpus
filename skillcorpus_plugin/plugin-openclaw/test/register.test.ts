/**
 * The plugin against a fake host driving the real OpenClaw contract.
 *
 * The host is not installed here, so what is pinned instead is the contract
 * itself: the hook name, the event fields the plugin reads, and the result
 * field the host honors — each copied from OpenClaw's `src/plugins/types.ts`.
 * A drift on either side fails here rather than at a user's first turn.
 *
 * `register.ts` imports no runtime value from the host, which is what lets
 * this file drive it directly.
 */

import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { DEFAULTS, loadConfig } from '../src/config.ts'
import { buildEngine, expandHome, recentUserText, register } from '../src/register.ts'
import type {
  AgentTool,
  BeforePromptBuildEvent,
  BeforePromptBuildResult,
  OpenClawPluginApi,
  PluginHookAgentContext,
} from '../src/openclaw-types.ts'

type Handler = (
  event: BeforePromptBuildEvent,
  ctx: PluginHookAgentContext,
) => BeforePromptBuildResult | void | Promise<BeforePromptBuildResult | void>

/** A host that records what the plugin registered and what it logged. */
function fakeApi(pluginConfig?: Record<string, unknown>): {
  api: OpenClawPluginApi
  hooks: Map<string, Handler>
  tools: Map<string, AgentTool>
  warnings: string[]
} {
  const hooks = new Map<string, Handler>()
  const tools = new Map<string, AgentTool>()
  const warnings: string[] = []
  const api: OpenClawPluginApi = {
    id: 'skillsearch',
    name: 'Skill Search',
    pluginConfig: { hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '', ...(pluginConfig ?? {}) },
    logger: {
      info: () => {},
      warn: (message: string) => { warnings.push(message) },
    },
    on: (event, handler) => { hooks.set(event, handler as Handler) },
    registerTool: tool => { tools.set(tool.name, tool) },
  }
  return { api, hooks, tools, warnings }
}

/** A skills directory with one obvious match and one distractor. */
async function skillsDir(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'openclaw-skillsearch-'))
  await mkdir(join(root, 'pdf-forms'), { recursive: true })
  await writeFile(
    join(root, 'pdf-forms', 'SKILL.md'),
    '---\nname: pdf-forms\ndescription: Fill PDF acroforms\n---\n\nRun `pdftk` with an FDF to fill an acroform.\n',
  )
  await mkdir(join(root, 'git-bisect'), { recursive: true })
  await writeFile(
    join(root, 'git-bisect', 'SKILL.md'),
    '---\nname: git-bisect\ndescription: Find the commit that broke a test\n---\n\nRun `git bisect start`.\n',
  )
  return root
}

const NO_CTX: PluginHookAgentContext = {}

test('registers exactly the hook the host calls before a prompt is built', async () => {
  const { api, hooks } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()] })
  register(api)
  assert.deepEqual([...hooks.keys()], ['before_prompt_build'])
})

test('injects the ranked skills through prependContext', async () => {
  const { api, hooks } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()], topK: 1 })
  register(api)

  const result = await hooks.get('before_prompt_build')!(
    { prompt: 'fill in the acroform with pdftk', messages: [] },
    NO_CTX,
  )

  assert.ok(result, 'the hook returned nothing')
  const block = (result as BeforePromptBuildResult).prependContext ?? ''
  assert.match(block, /### Skill: pdf-forms {2}\[local\/pdf-forms\]/)
  assert.match(block, /pdftk/)
  // `prependContext`, not a system-context field: those are for static
  // guidance a provider can cache, and this selection changes every turn.
  assert.equal((result as BeforePromptBuildResult).prependSystemContext, undefined)
  assert.equal((result as BeforePromptBuildResult).systemPrompt, undefined)
})

test('returns nothing when the corpus shares no term with the query', async () => {
  // No model is configured here, so there is no gate. Retrieval is a keyword
  // match and nothing else: a query with no shared term returns nothing, and
  // a query that merely brushes one does return a weak hit. Removing those
  // is the gate's job, which is why the README calls a model near-required.
  const { api, hooks } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()] })
  register(api)
  const hook = hooks.get('before_prompt_build')!

  assert.equal(await hook({ prompt: 'kubernetes ingress', messages: [] }, NO_CTX), undefined)

  const brushed = await hook({ prompt: 'which commit broke it', messages: [] }, NO_CTX)
  assert.match((brushed as BeforePromptBuildResult).prependContext ?? '', /git-bisect/)
})

test('falls back to the last user message when the prompt arrives empty', async () => {
  const { api, hooks } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()], topK: 1 })
  register(api)

  const result = await hooks.get('before_prompt_build')!(
    {
      prompt: '',
      messages: [
        { role: 'assistant', content: 'sure' },
        { role: 'user', content: 'fill the acroform with pdftk' },
      ],
    },
    NO_CTX,
  )
  assert.match((result as BeforePromptBuildResult).prependContext ?? '', /pdf-forms/)
})

test('registers no hook at all when nothing is configured to search', async () => {
  const { api, hooks } = fakeApi({ mode: 'auto', skillsDirs: [] })
  register(api)
  assert.equal(hooks.size, 0)
})

test('the manifest accepts the placeholder-resolution switch', async () => {
  const manifest = JSON.parse(await readFile(new URL('../openclaw.plugin.json', import.meta.url), 'utf8'))
  assert.equal(manifest.configSchema.properties.resolvePlaceholders.type, 'boolean')
  assert.equal(manifest.configSchema.properties.resolvePlaceholders.default, false)
})

test('engines are cached per workspace rather than shared across agents', async () => {
  const builtFor: Array<string | undefined> = []
  const { api, hooks } = fakeApi({ mode: 'auto', skillsDirs: ['/skills'] })
  register(api, {
    buildEngineFn: (_config, workspaceDir) => {
      builtFor.push(workspaceDir)
      return { enabled: true, retrieve: () => Promise.resolve('') } as never
    },
  })
  const hook = hooks.get('before_prompt_build')!
  await hook({ prompt: 'one', messages: [] }, { workspaceDir: '/workspace/a' })
  await hook({ prompt: 'two', messages: [] }, { workspaceDir: '/workspace/b' })
  await hook({ prompt: 'three', messages: [] }, { workspaceDir: '/workspace/a' })
  assert.deepEqual(builtFor, [undefined, '/workspace/a', '/workspace/b'])
})

test('on-demand retrieval keeps an unknown workspace unknown', async () => {
  const builtFor: Array<string | undefined> = []
  const { api, tools } = fakeApi({ mode: 'on_demand', skillsDirs: ['/skills'] })
  register(api, {
    buildEngineFn: (_config, workspaceDir) => {
      builtFor.push(workspaceDir)
      return { enabled: true, retrieve: () => Promise.resolve('') } as never
    },
  })

  await tools.get('skill_search')!.execute('call', { query: 'find a skill' }, undefined, undefined)

  assert.deepEqual(builtFor, [undefined])
})

test('a retrieval that throws costs the turn its skills, not the turn', async () => {
  const { api, hooks, warnings } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()] })
  register(api, {
    buildEngineFn: () => ({
      enabled: true,
      retrieve: () => Promise.reject(new Error('engine down')),
    }) as never,
  })

  const result = await hooks.get('before_prompt_build')!(
    { prompt: 'fill the acroform', messages: [] },
    NO_CTX,
  )
  assert.equal(result, undefined)
  assert.equal(warnings.length, 1)
})

test('the gate sees the tools a deployment declared', async () => {
  const seen: (readonly string[] | undefined)[] = []
  const { api, hooks } = fakeApi({
    mode: 'auto',
    skillsDirs: [await skillsDir()],
    availableTools: 'exec, read_file',
  })
  register(api, {
    buildEngineFn: () => ({
      enabled: true,
      retrieve: (_query: string, options: { availableTools?: readonly string[] }) => {
        seen.push(options.availableTools)
        return Promise.resolve('')
      },
    }) as never,
  })

  await hooks.get('before_prompt_build')!({ prompt: 'anything', messages: [] }, NO_CTX)
  assert.deepEqual(seen, [['exec', 'read_file']])
})

test('a blank turn never reaches retrieval', async () => {
  let called = 0
  const { api, hooks } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()] })
  register(api, {
    buildEngineFn: () => ({
      enabled: true,
      retrieve: () => { called += 1; return Promise.resolve('') },
    }) as never,
  })

  const result = await hooks.get('before_prompt_build')!({ prompt: '   ', messages: [] }, NO_CTX)
  assert.equal(result, undefined)
  assert.equal(called, 0)
})

test('the environment wins over the host config document', () => {
  const config = loadConfig({ model: 'from-document', topK: 3 }, {
    SKILLSEARCH_MODEL: 'from-env',
  } as NodeJS.ProcessEnv)
  assert.equal(config.model, 'from-env')
  assert.equal(config.topK, 3, 'a key with no env var keeps the document value')
})

test('an unset configuration resolves to the documented defaults', () => {
  const config = loadConfig(undefined, {} as NodeJS.ProcessEnv)
  assert.deepEqual(config, DEFAULTS)
  assert.equal(config.hubEndpoint, 'https://skillhub.evermind.ai')
  assert.equal(config.clawhubEndpoint, 'https://clawhub.ai')
  assert.equal(config.skillhubCnEndpoint, 'https://api.skillhub.cn')
})

test('the host manifest and runtime agree on every remote-source default', async () => {
  const manifest = JSON.parse(
    await readFile(new URL('../openclaw.plugin.json', import.meta.url), 'utf8'),
  )
  const properties = manifest.configSchema.properties
  assert.equal(properties.hubEndpoint.default, DEFAULTS.hubEndpoint)
  assert.equal(properties.clawhubEndpoint.default, DEFAULTS.clawhubEndpoint)
  assert.equal(properties.skillhubCnEndpoint.default, DEFAULTS.skillhubCnEndpoint)
})

test('an explicitly empty endpoint disables each default remote source', () => {
  const config = loadConfig(
    { hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '' },
    {} as NodeJS.ProcessEnv,
  )
  assert.equal(config.hubEndpoint, '')
  assert.equal(config.clawhubEndpoint, '')
  assert.equal(config.skillhubCnEndpoint, '')
})

test('a comma-separated list is accepted wherever an array is', () => {
  const config = loadConfig({ skillsDirs: '/a, /b' }, {} as NodeJS.ProcessEnv)
  assert.deepEqual(config.skillsDirs, ['/a', '/b'])
})

test('a leading ~ expands, and other paths are left alone', () => {
  assert.equal(expandHome('~/skills', '/home/x'), '/home/x/skills')
  assert.equal(expandHome('/abs/skills', '/home/x'), '/abs/skills')
  assert.equal(expandHome('relative/skills', '/home/x'), 'relative/skills')
})

test('the last user message is found past assistant and tool turns', () => {
  assert.equal(
    recentUserText([
      { role: 'user', content: 'first' },
      { role: 'user', content: [{ type: 'text', text: 'second' }] },
      { role: 'assistant', content: 'reply' },
    ]),
    'second',
  )
  assert.equal(recentUserText([{ role: 'assistant', content: 'only' }]), '')
})

test('an engine with no sources reports itself disabled', async () => {
  assert.equal(buildEngine(loadConfig({ skillsDirs: [], hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '' }, {} as NodeJS.ProcessEnv)).enabled, false)
})
