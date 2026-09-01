/**
 * `skill_search`, the on-demand mode's whole surface.
 *
 * In auto mode a bad block costs a turn some tokens. Here the tool *is* the
 * discovery mechanism: an agent only searches if the description told it to,
 * and only keeps working if a miss and a failure both read as "carry on".
 * So those three things are what this pins — the description, the miss, and
 * the failure — alongside the contract the host reads.
 */

import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { DEFAULTS } from '../src/config.ts'
import { buildEngine, register } from '../src/register.ts'
import { skillSearchTool } from '../src/tool.ts'
import type { AgentTool, OpenClaw2PluginApi, ToolResult } from '../src/openclaw2-types.ts'

/**
 * Every catalog endpoint blanked.
 *
 * 0.2.0 turns EverMind, ClawHub and skillhub.cn on by default, so a test that
 * only sets `skillsDirs` reaches the live network — and then asserts on
 * whatever a public catalog happened to return that minute. These tests are
 * about the tool's own behaviour, so the sources are pinned to the local
 * fixture directory and nothing else.
 */
const OFFLINE = { hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '' } as const

async function skillsDir(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'openclaw2-tool-'))
  await mkdir(join(root, 'pdf-forms'), { recursive: true })
  await writeFile(
    join(root, 'pdf-forms', 'SKILL.md'),
    '---\nname: pdf-forms\ndescription: Fill PDF acroforms\n---\n\nRun `pdftk` with an FDF to fill an acroform.\n',
  )
  return root
}

/** The tool the plugin actually registers, through `register`. */
async function registeredTool(over: Record<string, unknown> = {}): Promise<AgentTool> {
  const tools = new Map<string, AgentTool>()
  const api: OpenClaw2PluginApi = {
    id: 'skillsearch',
    name: 'Skill Search',
    pluginConfig: { ...OFFLINE, skillsDirs: [await skillsDir()], ...over },
    logger: { info: () => {}, warn: () => {} },
    registerContextEngine: () => {},
    registerTool: tool => { tools.set(tool.name, tool) },
  }
  register(api)
  const tool = tools.get('skill_search')
  assert.ok(tool, 'the plugin registered no skill_search tool')
  return tool
}

function textOf(result: ToolResult): string {
  return result.content.map(part => part.text).join('\n')
}

async function call(tool: AgentTool, query: unknown): Promise<string> {
  return textOf(await tool.execute('call-1', { query }, undefined, undefined))
}

// ── the contract the host reads ──────────────────────────────────────

test('the tool declares the shape the host validates', async () => {
  const tool = await registeredTool()
  assert.equal(tool.name, 'skill_search')
  assert.equal(typeof tool.execute, 'function')

  const parameters = tool.parameters as {
    type: string
    required: string[]
    additionalProperties: boolean
    properties: Record<string, { type: string }>
  }
  assert.equal(parameters.type, 'object')
  assert.deepEqual(parameters.required, ['query'])
  assert.equal(parameters.properties.query?.type, 'string')
  // The model invents fields it was not offered; refusing them keeps a
  // malformed call a validation error rather than a silently ignored one.
  assert.equal(parameters.additionalProperties, false)
})

test('the description tells the agent when to reach for it', async () => {
  // The only thing standing between on-demand mode and never retrieving at
  // all. A description that merely names the tool is the failure mode.
  const { description } = await registeredTool()
  assert.match(description, /skill/i)
  assert.match(description, /before improvising|multi-step/i, 'it must say when to call')
  assert.match(description, /Returns nothing|no fit/i, 'it must say what a miss means')
  assert.ok(description.length > 200, 'too short to steer a model')
})

// ── behaviour ────────────────────────────────────────────────────────

test('a hit comes back rendered the same way auto mode injects it', async () => {
  const tool = await registeredTool({ topK: 1 })
  const out = await call(tool, 'fill the acroform with pdftk')
  assert.match(out, /### Skill: pdf-forms/)
  assert.match(out, /pdftk/)
})

test('a miss says so, and says to carry on', async () => {
  // Returning an empty string would read to a model as a broken tool.
  const tool = await registeredTool()
  const out = await call(tool, 'kubernetes ingress annotations')
  assert.match(out, /No skill/i)
  assert.match(out, /Proceed without/i)
})

test('an empty query is answered, not thrown', async () => {
  const tool = await registeredTool()
  assert.match(await call(tool, '   '), /needs a query/i)
  assert.match(await call(tool, undefined), /needs a query/i)
})

test('a failing engine degrades to "carry on", and is logged once', async () => {
  const warnings: unknown[] = []
  const tool = skillSearchTool(
    { enabled: true, retrieve: () => Promise.reject(new Error('engine down')) } as never,
    DEFAULTS,
    { warn: (...args: unknown[]) => { warnings.push(args) } },
  )
  const out = textOf(await tool.execute('c', { query: 'anything' }, undefined, undefined))
  assert.match(out, /unavailable/i)
  assert.match(out, /Proceed without/i)
  assert.equal(warnings.length, 1)
})

test('the caller’s abort signal reaches the search', async () => {
  let seen: AbortSignal | undefined
  const tool = skillSearchTool(
    {
      enabled: true,
      // Rejects on abort, as the real engine does — a fake that ignored the
      // signal would let this test pass while the tool leaked the call.
      retrieve: (_query: string, options: { signal?: AbortSignal }) => {
        seen = options.signal
        return new Promise<string>((_resolve, reject) => {
          options.signal?.addEventListener('abort', () => { reject(new Error('aborted')) }, { once: true })
        })
      },
    } as never,
    { ...DEFAULTS, timeoutMs: 5_000 },
    {},
  )
  const outer = new AbortController()
  const pending = tool.execute('c', { query: 'anything' }, outer.signal, undefined)
  await new Promise(resolve => setTimeout(resolve, 5))
  outer.abort()

  // The caller's abort, not the tool's own deadline: that is set far out here
  // so a passing test cannot be the timeout firing instead.
  assert.match(textOf(await pending), /unavailable/i)
  assert.equal(seen?.aborted, true, 'aborting the tool call must abort the search')
})

test('the host’s tool surface reaches the gate, with the config as fallback', async () => {
  const seen: (readonly string[] | undefined)[] = []
  const tool = skillSearchTool(
    {
      enabled: true,
      retrieve: (_query: string, options: { availableTools?: readonly string[] }) => {
        seen.push(options.availableTools)
        return Promise.resolve('')
      },
    } as never,
    { ...DEFAULTS, availableTools: ['fallback'] },
    {},
  )
  await tool.execute('c', { query: 'x' }, undefined, undefined, { availableTools: ['exec', 'read_file'] })
  await tool.execute('c', { query: 'x' }, undefined, undefined)
  assert.deepEqual(seen, [['exec', 'read_file'], ['fallback']])
})

test('no sources means no tool to register at all', async () => {
  // Not just `skillsDirs: []`: 0.2.0 ships three catalog endpoints on by
  // default, and any one of them is a source. "Nothing configured" means
  // nothing, which is what a deployment that blanks them all is asking for.
  assert.equal(buildEngine({ ...DEFAULTS, ...OFFLINE, skillsDirs: [] }).enabled, false)
})

test('the tool the plugin registers is declared in the manifest', async () => {
  // `contracts.tools` is how the host learns a plugin owns a tool name. A
  // tool registered without it is not offered to the model — the plugin
  // loads, logs that on-demand mode is on, and the agent never sees a
  // `skill_search` to call. Nothing anywhere says why.
  const manifest = JSON.parse(
    await readFile(new URL('../openclaw.plugin.json', import.meta.url), 'utf8'),
  ) as { contracts?: { tools?: string[] } }
  assert.deepEqual(manifest.contracts?.tools, ['skill_search'])
})
