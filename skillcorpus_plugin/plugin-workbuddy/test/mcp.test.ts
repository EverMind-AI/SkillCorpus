/**
 * The MCP server WorkBuddy's on-demand mode is built on.
 *
 * Two layers, because they fail differently. `handle` is the protocol as a
 * pure function — the shape of every answer, and the three replies a model
 * must be able to act on (a hit, a miss, an empty query). The last test runs
 * the shipped bundle as a real process and speaks JSON-RPC to it over a pipe,
 * because everything above it can be right while the entry point never starts.
 */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { DEFAULTS } from '../src/config.ts'
import { handle, SKILL_SEARCH_TOOL, type Message } from '../src/mcp.ts'

const PLUGIN_ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

/**
 * Every catalog endpoint blanked.
 *
 * 0.2.0 turns EverMind, ClawHub and skillhub.cn on by default, so a test that
 * only sets `skillsDirs` reaches the live network and then asserts on whatever
 * a public catalog returned that minute.
 */
const OFFLINE = { hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '' } as const

async function skillsDir(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'workbuddy-mcp-'))
  await mkdir(join(root, 'pdf-forms'), { recursive: true })
  await writeFile(
    join(root, 'pdf-forms', 'SKILL.md'),
    '---\nname: pdf-forms\ndescription: Fill PDF acroforms\n---\n\nRun `pdftk` with an FDF to fill an acroform.\n',
  )
  return root
}

function ask(message: Message, search?: (query: string) => Promise<string>): Promise<unknown> {
  return handle(message, { ...DEFAULTS, ...OFFLINE }, search ?? (() => Promise.resolve('')))
}

// ── the protocol ─────────────────────────────────────────────────────

test('initialize echoes a protocol version the host offered', async () => {
  const result = (await ask({
    id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18' },
  })) as { result: { protocolVersion: string; capabilities: unknown; serverInfo: unknown } }

  assert.equal(result.result.protocolVersion, '2025-06-18')
  assert.deepEqual(result.result.capabilities, { tools: {} })
  assert.ok(result.result.serverInfo)
})

test('an unknown protocol version gets the newest this server speaks', async () => {
  // Not an error: which versions a client can live with is the client's to
  // decide, and answering with one it did not ask for lets it say no.
  const result = (await ask({
    id: 1, method: 'initialize', params: { protocolVersion: '1999-01-01' },
  })) as { result: { protocolVersion: string } }
  assert.equal(result.result.protocolVersion, '2025-11-25')
})

test('a notification produces no output at all', async () => {
  // A response to a notification is a protocol error, not a spare line.
  assert.equal(await ask({ method: 'notifications/initialized' }), undefined)
})

test('tools/list offers exactly skill_search', async () => {
  const result = (await ask({ id: 2, method: 'tools/list' })) as {
    result: { tools: { name: string; inputSchema: { required: string[]; additionalProperties: boolean } }[] }
  }
  assert.deepEqual(result.result.tools.map(t => t.name), ['skill_search'])
  assert.deepEqual(result.result.tools[0]?.inputSchema.required, ['query'])
  // The model invents fields it was not offered.
  assert.equal(result.result.tools[0]?.inputSchema.additionalProperties, false)
})

test('an unknown method and an unknown tool are both errors, not silence', async () => {
  const method = (await ask({ id: 3, method: 'nope' })) as { error: { code: number } }
  assert.equal(method.error.code, -32601)
  const tool = (await ask({ id: 4, method: 'tools/call', params: { name: 'nope', arguments: {} } })) as {
    error: { code: number }
  }
  assert.equal(tool.error.code, -32602)
})

// ── what the model gets back ─────────────────────────────────────────

function textOf(response: unknown): string {
  return ((response as { result: { content: { text: string }[] } }).result.content ?? [])
    .map(part => part.text)
    .join('\n')
}

test('a hit comes back as the rendered block', async () => {
  const response = await ask(
    { id: 5, method: 'tools/call', params: { name: 'skill_search', arguments: { query: 'fill an acroform' } } },
    () => Promise.resolve('# Skills\n\n### Skill: pdf-forms  [local/pdf-forms]\nRun pdftk.'),
  )
  assert.match(textOf(response), /### Skill: pdf-forms/)
})

test('a miss and an empty query both read as "carry on"', async () => {
  // An empty string would read to a model as a broken tool rather than as
  // "the library has no fit".
  const miss = await ask(
    { id: 6, method: 'tools/call', params: { name: 'skill_search', arguments: { query: 'kubernetes ingress' } } },
    () => Promise.resolve(''),
  )
  assert.match(textOf(miss), /No skill/i)
  assert.match(textOf(miss), /Proceed without/i)

  const blank = await ask({
    id: 7, method: 'tools/call', params: { name: 'skill_search', arguments: { query: '  ' } },
  })
  assert.match(textOf(blank), /needs a query/i)
})

test('the description says when to reach for it', async () => {
  // The only thing standing between on-demand mode and never retrieving.
  // Measured on a real host: a question about an in-house template went
  // unanswered until the description named that case explicitly.
  const { description } = SKILL_SEARCH_TOOL
  assert.match(description, /multi-step/i)
  assert.match(description, /internal convention|template|standard/i)
  assert.match(description, /Returns nothing|no fit/i)
  assert.ok(description.length > 200, 'too short to steer a model')
})

// ── the manifest, and the shipped process ────────────────────────────

test('the plugin manifest points the host at the built server', async () => {
  // `mcpServers` is how a WorkBuddy plugin offers a model-callable tool at
  // all — its hook slot can inject text but cannot be called.
  const manifest = JSON.parse(
    await readFile(join(PLUGIN_ROOT, '.codebuddy-plugin', 'plugin.json'), 'utf8'),
  ) as { mcpServers?: string }
  assert.equal(manifest.mcpServers, './mcp/servers.json')

  const servers = JSON.parse(await readFile(join(PLUGIN_ROOT, 'mcp', 'servers.json'), 'utf8')) as {
    mcpServers: Record<string, { type: string; command: string; args: string[] }>
  }
  const entry = servers.mcpServers.skillsearch
  assert.equal(entry?.type, 'stdio')
  assert.equal(entry?.command, 'node')
  assert.match(entry?.args?.[0] ?? '', /dist\/mcp\.mjs$/)
})

test('the shipped bundle answers JSON-RPC as a real process', async () => {
  const dir = await skillsDir()
  const child = spawn(process.execPath, [join(PLUGIN_ROOT, 'dist', 'mcp.mjs')], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: {
      ...process.env,
      SKILLSEARCH_MODE: 'on_demand',
      SKILLSEARCH_SKILLS_DIRS: dir,
      SKILLSEARCH_HUB_ENDPOINT: '',
      SKILLSEARCH_CLAWHUB_ENDPOINT: '',
      SKILLSEARCH_SKILLHUB_CN_ENDPOINT: '',
    },
  })

  const lines: string[] = []
  let buffer = ''
  child.stdout.on('data', chunk => {
    buffer += String(chunk)
    for (const line of buffer.split('\n').slice(0, -1)) if (line.trim()) lines.push(line)
    buffer = buffer.slice(buffer.lastIndexOf('\n') + 1)
  })

  const send = (message: Message): void => { child.stdin.write(`${JSON.stringify(message)}\n`) }
  send({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-11-25' } })
  send({ jsonrpc: '2.0', id: 2, method: 'tools/list' })
  send({
    jsonrpc: '2.0', id: 3, method: 'tools/call',
    params: { name: 'skill_search', arguments: { query: 'fill the acroform with pdftk' } },
  })

  const deadline = Date.now() + 20_000
  while (lines.length < 3 && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  // Killing is not enough on its own: the parent still holds the child's
  // stdio pipes, and the runner's event loop stays alive on them — the suite
  // passes and then never exits, which in CI is a timeout rather than a
  // failure. Close the pipes and wait for the process to actually go.
  child.stdin.end()
  child.kill()
  await once(child, 'exit')
  child.stdout.destroy()
  child.stderr.destroy()

  assert.equal(lines.length, 3, `expected three answers, got ${lines.length}`)
  const [init, list, call] = lines.map(line => JSON.parse(line) as unknown)
  assert.equal((init as { result: { protocolVersion: string } }).result.protocolVersion, '2025-11-25')
  assert.deepEqual(
    (list as { result: { tools: { name: string }[] } }).result.tools.map(t => t.name),
    ['skill_search'],
  )
  // End to end through the real engine: the fixture skill, retrieved.
  assert.match(textOf(call), /pdf-forms/)
})
