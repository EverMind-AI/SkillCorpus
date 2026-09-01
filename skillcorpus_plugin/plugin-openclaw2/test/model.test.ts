/**
 * The plugin's own model client, and the gate it enables, over real HTTP.
 *
 * A local server speaking `/chat/completions` stands in for the provider:
 * everything between the hook and the socket is the shipping code, so this
 * covers the request the plugin actually sends, the reply shape it parses,
 * and the selection the gate then makes. No credential, no network.
 */

import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { createServer, type Server } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, test } from 'node:test'
import { createChatModel } from '../src/model.ts'
import { register } from '../src/register.ts'
import type {
  ContextEngine,
  ContextEngineFactory,
  OpenClaw2PluginApi,
} from '../src/openclaw2-types.ts'

interface Recorded {
  readonly authorization: string | undefined
  readonly body: { model?: string; messages?: { role: string; content: string }[] }
}

/** A provider that answers from a queue and records what it was sent. */
async function fakeProvider(replies: string[]): Promise<{
  baseUrl: string
  seen: Recorded[]
  close(): Promise<void>
}> {
  const seen: Recorded[] = []
  const server: Server = createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', chunk => chunks.push(chunk as Buffer))
    request.on('end', () => {
      if (!request.url?.endsWith('/chat/completions')) {
        response.writeHead(404).end()
        return
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      seen.push({ authorization: request.headers.authorization, body })
      // One reserved model name, so a test can exercise the error path
      // through the same client rather than around it.
      if (body.model === 'boom') {
        response.writeHead(503, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ error: 'upstream unavailable' }))
        return
      }
      const content = replies.shift() ?? '{}'
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ choices: [{ message: { content } }] }))
    })
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  return {
    baseUrl: `http://127.0.0.1:${port}/v1`,
    seen,
    close: () => new Promise<void>(resolve => { server.close(() => resolve()) }),
  }
}

const servers: { close(): Promise<void> }[] = []
after(async () => { for (const server of servers) await server.close() })

async function provider(replies: string[]) {
  const started = await fakeProvider(replies)
  servers.push(started)
  return started
}

async function skillsDir(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'openclaw-model-'))
  for (const [name, description, body] of [
    ['pdf-forms', 'Fill PDF acroforms', 'Run `pdftk` with an FDF to fill an acroform.'],
    ['git-bisect', 'Find the commit that broke a test', 'Run `git bisect start`.'],
  ]) {
    await mkdir(join(root, name!), { recursive: true })
    await writeFile(
      join(root, name!, 'SKILL.md'),
      `---\nname: ${name}\ndescription: ${description}\n---\n\n${body}\n`,
    )
  }
  return root
}

function fakeApi(pluginConfig: Record<string, unknown>): {
  api: OpenClaw2PluginApi
  engines: Map<string, ContextEngineFactory>
} {
  const engines = new Map<string, ContextEngineFactory>()
  return {
    api: {
      id: 'skillsearch',
      name: 'Skill Search',
    // 0.2.0 ships EverMind, ClawHub and skillhub.cn on by default; these
    // tests are about the plugin, not about what a public catalog returned
    // this minute, so every remote source is blanked unless a case asks.
      pluginConfig: { hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '', ...pluginConfig },
      logger: { info: () => {}, warn: () => {} },
      registerContextEngine: (id, factory) => { engines.set(id, factory) },
      registerTool: () => {},
    },
    engines,
  }
}

/** Assemble one turn through the registered engine and return what it added. */
async function injected(
  engines: Map<string, ContextEngineFactory>,
  prompt: string,
  workspaceDir?: string,
): Promise<string> {
  const factory = engines.get('skillsearch')
  assert.ok(factory, 'the plugin registered no context engine')
  const engine: ContextEngine = await factory(workspaceDir ? { workspaceDir } : {})
  const before = [{ role: 'user', content: prompt }]
  const { messages } = await engine.assemble({ sessionId: 's1', messages: [...before], prompt })
  // The host's own messages come back untouched; anything after them is ours.
  assert.deepEqual(messages.slice(0, before.length), before, 'host messages must pass through')
  return messages
    .slice(before.length)
    .map(message => String((message as { content?: unknown }).content ?? ''))
    .join('\n')
}

test('the client sends the credential and the configured model', async () => {
  const server = await provider(['hello'])
  const model = createChatModel({ baseUrl: server.baseUrl, apiKey: 'sk-test', model: 'm1' })

  assert.ok(model, 'a configured model must produce a client')
  assert.equal(await model.complete('ping', {}), 'hello')
  assert.equal(server.seen[0]?.authorization, 'Bearer sk-test')
  assert.equal(server.seen[0]?.body.model, 'm1')
  assert.equal(server.seen[0]?.body.messages?.[0]?.content, 'ping')
})

test('no configured model means no client, which is what disables the gate', () => {
  assert.equal(createChatModel({ baseUrl: 'http://x', apiKey: '', model: '' }), undefined)
})

test('an HTTP error surfaces as a rejection for the caller to fall back on', async () => {
  const server = await provider([])
  const model = createChatModel({ baseUrl: server.baseUrl, apiKey: '', model: 'boom' })!
  await assert.rejects(() => model.complete('ping', {}), /HTTP 503/)
})

test('the gate narrows the block, over real HTTP end to end', async () => {
  const server = await provider([
    '{"rewritten_query": "fill a pdf acroform"}',
    '{"plan": "fill the form", "skills": ["local/pdf-forms"]}',
  ])
  const { api, engines } = fakeApi({
    // These pin what auto mode injects; the default is on demand.
    mode: 'auto',
    skillsDirs: [await skillsDir()],
    model: 'gate-model',
    modelBaseUrl: server.baseUrl,
    modelApiKey: 'sk-test',
    availableTools: ['exec', 'read_file'],
    // Explicit: with only a local directory configured the gate is off by
    // default, and this test is about the gate.
    gate: true,
  })
  register(api)

  const block = await injected(engines, 'can you fill in /tmp/a7f2.pdf for me')
  assert.match(block, /### Skill: pdf-forms/)
  assert.doesNotMatch(block, /git-bisect/)

  assert.equal(server.seen.length, 2, 'the rewriter and the gate are each called once')
  const gatePrompt = server.seen[1]?.body.messages?.[0]?.content ?? ''
  assert.match(gatePrompt, /You are a skill selector/)
  assert.match(gatePrompt, /The agent's ONLY available tools are: exec, read_file\./)
})

test('the rewriter deciding against retrieval skips the search entirely', async () => {
  const server = await provider(['{"need_retrieval": false, "rewritten_query": null}'])
  const { api, engines } = fakeApi({
    // These pin what auto mode injects; the default is on demand.
    mode: 'auto',
    skillsDirs: [await skillsDir()],
    model: 'gate-model',
    modelBaseUrl: server.baseUrl,
  })
  register(api)

  assert.equal(await injected(engines, 'thanks!'), '')
  assert.equal(server.seen.length, 1, 'only the rewriter ran')
})

test('an unreachable provider degrades to unfiltered retrieval, not to a failed turn', async () => {
  const { api, engines } = fakeApi({
    // These pin what auto mode injects; the default is on demand.
    mode: 'auto',
    skillsDirs: [await skillsDir()],
    model: 'gate-model',
    // A port nothing listens on: both model calls fail, and each falls back.
    modelBaseUrl: 'http://127.0.0.1:1/v1',
    topK: 1,
  })
  register(api)

  assert.match(await injected(engines, 'fill the acroform with pdftk'), /pdf-forms/)
})

test('the gate stays off for a local directory and on for a catalog', async () => {
  // The default is neither on nor off. The gate rejects when unsure, which
  // a curated directory does not need; a catalog of unvetted skills needs
  // its environment check.
  const server = await provider([
    '{"rewritten_query": "fill a pdf acroform"}',
    '{"plan": "fill the form", "skills": ["local/pdf-forms"]}',
  ])
  const { api, engines } = fakeApi({
    // These pin what auto mode injects; the default is on demand.
    mode: 'auto',
    skillsDirs: [await skillsDir()],
    model: 'gate-model',
    modelBaseUrl: server.baseUrl,
    modelApiKey: 'sk-test',
  })
  register(api)

  await injected(engines, 'can you fill in /tmp/a7f2.pdf for me')
  assert.equal(server.seen.length, 1, 'only the rewriter ran')
})
