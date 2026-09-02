/**
 * The plugin against a fake OpenClaw 2.0 host driving the real contract.
 *
 * The host is not installed here, so what is pinned instead is the contract
 * itself: the registration call, the four members the host requires of a
 * context engine, and what `assemble` is allowed to do to the messages it was
 * handed. Each was read off OpenClaw 2026.8.1's own type declarations; a
 * drift on either side fails here rather than at a user's first turn.
 *
 * `register.ts` imports no runtime value from the host, which is what lets
 * this file drive it directly.
 */

import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { DEFAULTS } from '../src/config.ts'
import { buildEngine, expandHome, recentUserText, register } from '../src/register.ts'
import { VERSION } from '../src/version.ts'
import type {
  AgentMessage,
  AgentTool,
  ContextEngine,
  ContextEngineFactory,
  OpenClaw2PluginApi,
} from '../src/openclaw2-types.ts'

/** A host that records what the plugin registered and what it logged. */
function fakeApi(pluginConfig?: Record<string, unknown>): {
  api: OpenClaw2PluginApi
  engines: Map<string, ContextEngineFactory>
  tools: Map<string, AgentTool>
  warnings: string[]
  infos: string[]
} {
  const engines = new Map<string, ContextEngineFactory>()
  const tools = new Map<string, AgentTool>()
  const warnings: string[] = []
  const infos: string[] = []
  const api: OpenClaw2PluginApi = {
    id: 'skillsearch',
    name: 'Skill Search',
    // 0.2.0 ships EverMind, ClawHub and skillhub.cn on by default; these
    // tests are about the plugin, not about what a public catalog returned
    // this minute, so every remote source is blanked unless a case asks.
    pluginConfig: { hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '', ...(pluginConfig ?? {}) },
    logger: {
      info: (message: string) => { infos.push(message) },
      warn: (message: string) => { warnings.push(message) },
    },
    registerContextEngine: (id, factory) => { engines.set(id, factory) },
    registerTool: tool => { tools.set(tool.name, tool) },
  }
  return { api, engines, tools, warnings, infos }
}

/** A skills directory with one obvious match and one distractor. */
async function skillsDir(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'openclaw2-skillsearch-'))
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

/** Auto mode, since these pin what the context engine does. */
async function engineFor(config: Record<string, unknown>, workspaceDir?: string): Promise<ContextEngine> {
  const { api, engines } = fakeApi({ mode: 'auto', ...config })
  register(api)
  const factory = engines.get('skillsearch')
  assert.ok(factory, 'the plugin registered no context engine')
  return await factory(workspaceDir ? { workspaceDir } : {})
}

// ── the registration surface ─────────────────────────────────────────

// ── the two modes ────────────────────────────────────────────────────

test('the default is on demand: a tool, and no context engine', async () => {
  // The exclusive slot is the reason the two are mutually exclusive rather
  // than additive — holding it to inject nothing would deny it to whatever
  // else could have used it.
  const { api, engines, tools, infos } = fakeApi({ skillsDirs: [await skillsDir()] })
  register(api)

  assert.deepEqual([...tools.keys()], ['skill_search'])
  assert.equal(engines.size, 0)
  assert.match(infos.join(' '), /on-demand mode/)
})

test('auto mode is the mirror image: a context engine, and no tool', async () => {
  const { api, engines, tools, infos } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()] })
  register(api)

  assert.deepEqual([...engines.keys()], ['skillsearch'])
  assert.equal(tools.size, 0)
  assert.match(infos.join(' '), /auto mode/)
})

test('an unrecognised mode falls back to the default, not to nothing', async () => {
  // A typo should cost the deployment the mode it wanted, not its retrieval.
  const { api, tools } = fakeApi({ mode: 'atuo', skillsDirs: [await skillsDir()] })
  register(api)
  assert.deepEqual([...tools.keys()], ['skill_search'])
})

// ── the registration surface ─────────────────────────────────────────

test('nothing configured to search registers neither, in either mode', async () => {
  for (const mode of ['on_demand', 'auto']) {
    const { api, engines, tools, infos } = fakeApi({ mode, skillsDirs: [] })
    register(api)
    assert.equal(engines.size, 0, mode)
    assert.equal(tools.size, 0, mode)
    assert.match(infos.join(' '), /no sources configured/)
  }
})

test('the engine reports the four members the host requires', async () => {
  const engine = await engineFor({ skillsDirs: [await skillsDir()] })
  for (const member of ['ingest', 'assemble', 'compact'] as const) {
    assert.equal(typeof engine[member], 'function', member)
  }
  assert.equal(engine.info.id, 'skillsearch')
})

test('it does not claim to own compaction', async () => {
  // The host stops running its own compaction for an engine that owns it.
  // This one appends to an assembled context and keeps no transcript, so
  // claiming ownership would hand a growing session to nobody.
  const engine = await engineFor({ skillsDirs: [await skillsDir()] })
  assert.equal(engine.info.ownsCompaction, false)

  const result = await engine.compact({ sessionId: 's', sessionKey: 'k' })
  assert.equal(result.ok, true)
  assert.equal(result.compacted, false)
  assert.match(result.reason ?? '', /does not own compaction/)
})

test('ingest reports honestly that nothing was stored', async () => {
  const engine = await engineFor({ skillsDirs: [await skillsDir()] })
  assert.deepEqual(await engine.ingest({ sessionId: 's', message: { role: 'user', content: 'x' } }), {
    ingested: false,
  })
})

// ── assemble ─────────────────────────────────────────────────────────

test('assemble appends the turn’s skills and leaves the host’s messages alone', async () => {
  const engine = await engineFor({ skillsDirs: [await skillsDir()], topK: 1 })
  const before: AgentMessage[] = [
    { role: 'user', content: 'earlier turn' },
    { role: 'assistant', content: 'earlier reply' },
  ]
  const { messages, estimatedTokens } = await engine.assemble({
    sessionId: 's',
    messages: [...before],
    prompt: 'fill the acroform with pdftk',
  })

  assert.deepEqual(messages.slice(0, 2), before, 'the host’s own messages must pass through unchanged')
  assert.equal(messages.length, 3)
  assert.match(String(messages[2]?.content ?? ''), /### Skill: pdf-forms/)
  assert.ok(estimatedTokens > 0)
})

test('a turn matching nothing adds no message', async () => {
  const engine = await engineFor({ skillsDirs: [await skillsDir()] })
  const before: AgentMessage[] = [{ role: 'user', content: 'x' }]
  const { messages } = await engine.assemble({
    sessionId: 's',
    messages: [...before],
    prompt: 'kubernetes ingress annotations',
  })
  assert.deepEqual(messages, before)
})

test('an empty prompt falls back to the most recent user message', async () => {
  const engine = await engineFor({ skillsDirs: [await skillsDir()], topK: 1 })
  const { messages } = await engine.assemble({
    sessionId: 's',
    messages: [{ role: 'user', content: 'fill the acroform with pdftk' }],
    prompt: '',
  })
  assert.match(String(messages.at(-1)?.content ?? ''), /pdf-forms/)
})

test('a failing engine costs the turn its skills, not the turn', async () => {
  const { api, engines, warnings } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()] })
  register(api, {
    buildEngineFn: () => ({
      enabled: true,
      retrieve: () => Promise.reject(new Error('engine down')),
    }) as never,
  })
  const engine = await engines.get('skillsearch')!({})
  const before: AgentMessage[] = [{ role: 'user', content: 'anything' }]
  const { messages } = await engine.assemble({ sessionId: 's', messages: [...before], prompt: 'anything' })

  assert.deepEqual(messages, before)
  assert.equal(warnings.length, 1)
})

// ── what the 2.0 contract gives that the 1.x hook did not ────────────

test('the host’s real tool surface reaches the gate', async () => {
  // Under 1.x the host reported no tool names to the hook, so the gate could
  // judge relevance but never "this agent lacks the tool this skill needs".
  // `assemble` is handed the turn's actual tools, so the check runs for free
  // and the `availableTools` setting is only a fallback.
  const seen: (readonly string[] | undefined)[] = []
  const { api, engines } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()], availableTools: ['fallback'] })
  register(api, {
    buildEngineFn: () => ({
      enabled: true,
      retrieve: (_query: string, options: { availableTools?: readonly string[] }) => {
        seen.push(options.availableTools)
        return Promise.resolve('')
      },
    }) as never,
  })
  const engine = await engines.get('skillsearch')!({})

  await engine.assemble({
    sessionId: 's',
    messages: [],
    prompt: 'anything',
    availableTools: new Set(['exec', 'read_file']),
  })
  await engine.assemble({ sessionId: 's', messages: [], prompt: 'anything' })

  assert.deepEqual(seen, [['exec', 'read_file'], ['fallback']])
})

test('each workspace gets its own engine', async () => {
  // The 1.x plugin had to pass the workspace per turn, because one engine
  // served every session. Here the host builds one engine per workspace, so
  // the factory is the right place — this pins that it actually varies.
  const seen: (string | undefined)[] = []
  const { api, engines } = fakeApi({ mode: 'auto', skillsDirs: [await skillsDir()] })
  register(api, {
    buildEngineFn: (_config, workspaceDir) => {
      seen.push(workspaceDir)
      return { enabled: true, retrieve: () => Promise.resolve('') } as never
    },
  })
  const factory = engines.get('skillsearch')!
  await factory({ workspaceDir: '/ws/a' })
  await factory({ workspaceDir: '/ws/b' })

  // The first is the `enabled` probe, which is deliberately workspace-less.
  assert.deepEqual(seen, [undefined, '/ws/a', '/ws/b'])
})

// ── the manifest ─────────────────────────────────────────────────────

test('the manifest declares the context-engine kind', async () => {
  // Without `kind`, 2.0 treats the plugin as hook-only — the shape whose
  // `before_prompt_build` result this host drops on the floor.
  const manifest = JSON.parse(
    await readFile(new URL('../openclaw.plugin.json', import.meta.url), 'utf8'),
  ) as { kind?: string; configSchema: { properties: Record<string, unknown> } }
  assert.equal(manifest.kind, 'context-engine')
})

test('every setting the plugin reads is declared in the manifest', async () => {
  // `configSchema` is `additionalProperties: false`, so the host rejects the
  // *whole* config object over one key it does not know — the plugin then
  // runs on defaults with nothing logged, which reads as "my setting is
  // ignored". A key added to `config.ts` and forgotten here is silent.
  const manifest = JSON.parse(
    await readFile(new URL('../openclaw.plugin.json', import.meta.url), 'utf8'),
  ) as { configSchema: { properties: Record<string, unknown> } }

  const declared = new Set(Object.keys(manifest.configSchema.properties))
  const missing = Object.keys(DEFAULTS).filter(key => !declared.has(key))
  assert.deepEqual(missing, [], `settings read but not declared: ${missing.join(', ')}`)

  const read = new Set(Object.keys(DEFAULTS))
  const unread = [...declared].filter(key => !read.has(key))
  assert.deepEqual(unread, [], `settings declared but never read: ${unread.join(', ')}`)
})

// ── helpers shared with the 1.x plugin ───────────────────────────────

test('a leading ~ expands, and other paths are left alone', () => {
  assert.equal(expandHome('~', '/home/x'), '/home/x')
  assert.equal(expandHome('~/skills', '/home/x'), '/home/x/skills')
  assert.equal(expandHome('/abs/skills', '/home/x'), '/abs/skills')
})

test('the most recent user text is found through mixed content shapes', () => {
  assert.equal(recentUserText([{ role: 'user', content: 'first' }, { role: 'assistant', content: 'a' }]), 'first')
  assert.equal(recentUserText([{ role: 'user', content: [{ text: 'block' }, { text: 'text' }] }]), 'block text')
  assert.equal(recentUserText([]), '')
})

test('no sources means the engine reports itself disabled', async () => {
  // Not just `skillsDirs: []`: 0.2.0 ships three catalog endpoints on by
  // default, and any one of them is a source.
  assert.equal(buildEngine({
    ...DEFAULTS, skillsDirs: [], hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '',
  }).enabled, false)
})

test('the version this package reports is the version it ships', async () => {
  // Both hosts announce a version to their user, and both announced a wrong
  // one — the context engine said 0.1.0 and the MCP server 0.2.0, each
  // against a package that said otherwise. A release bump edits manifests
  // and forgets a string buried in a source file, so this is the check that
  // makes the next one loud.
  const pkg = JSON.parse(
    await readFile(new URL('../package.json', import.meta.url), 'utf8'),
  ) as { version: string }
  assert.equal(VERSION, pkg.version)
})
