/**
 * `skill_search` for WorkBuddy, as an MCP server over stdio.
 *
 * ## Why this host needs a second entry point
 *
 * Every other host lets a plugin hand the agent a tool directly — OpenClaw's
 * `registerTool`, Hermes's `get_tool_schemas`, Raven's `contributes.tools`,
 * DSH's `ctx.tools.register`. WorkBuddy's plugin format has no such slot: it
 * declares hooks, and a `UserPromptSubmit` hook can inject text but cannot
 * offer something the model chooses to call.
 *
 * What it does have is `mcpServers` in the plugin manifest, which the host
 * merges into its own MCP configuration at startup. So on-demand mode here is
 * an MCP server, and the tool reaches the model as an MCP tool.
 *
 * ## Why the protocol is hand-written
 *
 * This package has no dependencies and ships a checked-in bundle that CI
 * verifies is current; `--packages=external` means a dependency would have to
 * be installed beside the bundle at runtime, which is exactly what "a hook the
 * host spawns" cannot rely on. The slice of MCP a one-tool server needs is
 * four messages, so it is written out rather than pulled in.
 *
 * Protocol versions are the ones WorkBuddy 2.143.0 accepts, newest first. The
 * client's requested version is echoed back when it is one of them, and the
 * newest is offered otherwise — an unknown version is the client's to reject,
 * not this server's to guess at.
 *
 * ## Lifecycle
 *
 * Unlike the hook, which is spawned per turn and exits, this process is
 * long-lived: the host starts it with the session and keeps it. Retrieval
 * state that the hook rebuilds every turn is therefore built once here — which
 * is a gain, not a compromise, since the local scan is what the hook's disk
 * cache exists to avoid repeating.
 *
 * @module
 */

import { argv } from 'node:process'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import { loadConfig, readConfigDocument, type SkillSearchConfig } from './config.js'
import { retrieveForTurn } from './retrieve.js'

/** Accepted by WorkBuddy 2.143.0, newest first. */
const PROTOCOL_VERSIONS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05', '2024-10-07']

export const SKILL_SEARCH_DESCRIPTION = [
  'Search the skill library for a procedure that fits the task at hand, and',
  'get back the matching skills in full.',
  '',
  'A skill is a written workflow for a specific job — filling PDF forms,',
  'building a slide deck, migrating a schema — including the exact commands,',
  'files, and in-house conventions it needs.',
  '',
  'Reach for it when:',
  '- a task needs a multi-step procedure you would otherwise improvise;',
  '- a task names a format, tool, or workflow you would have to guess at;',
  '- a question asks about an internal convention, template, standard, or',
  '  "our" way of doing something — a skill is where those are written down,',
  '  so searching here comes before answering that you do not know.',
  '',
  'Search with the words the task actually uses; the query is matched against',
  'skill names and descriptions. Returns nothing when the library has no fit,',
  'which is a normal answer and means: proceed on your own.',
].join(' ')

export const SKILL_SEARCH_TOOL = {
  name: 'skill_search',
  description: SKILL_SEARCH_DESCRIPTION,
  inputSchema: {
    type: 'object',
    properties: {
      query: {
        type: 'string',
        description:
          'What you need to do, in the task\'s own words — e.g. "extract tables from a scanned PDF invoice".',
      },
    },
    required: ['query'],
    additionalProperties: false,
  },
} as const

type Json = Record<string, unknown>

/** A JSON-RPC request or notification, as far as this server reads one. */
export interface Message {
  jsonrpc?: string
  id?: string | number
  method?: string
  params?: Json
}

function text(value: string): Json {
  return { content: [{ type: 'text', text: value }] }
}

/**
 * Answer one message.
 *
 * @param message - the decoded JSON-RPC message.
 * @param config - the resolved plugin configuration.
 * @param search - the retrieval call, injected so tests need no engine.
 * @returns the response to write, or `undefined` for a notification — which
 *   must produce no output at all: a response to a notification is a protocol
 *   error, not a harmless extra line.
 */
export async function handle(
  message: Message,
  config: SkillSearchConfig,
  search: (query: string) => Promise<string> = query => retrieveForTurn(query, config),
): Promise<Json | undefined> {
  const { id, method } = message
  if (id === undefined) return undefined

  const reply = (result: Json): Json => ({ jsonrpc: '2.0', id, result })

  if (method === 'initialize') {
    const asked = String((message.params as Json | undefined)?.protocolVersion ?? '')
    return reply({
      protocolVersion: PROTOCOL_VERSIONS.includes(asked) ? asked : PROTOCOL_VERSIONS[0],
      capabilities: { tools: {} },
      serverInfo: { name: 'skillsearch', version: '0.2.0' },
    })
  }
  if (method === 'tools/list') return reply({ tools: [SKILL_SEARCH_TOOL] })
  if (method === 'ping') return reply({})
  if (method === 'tools/call') {
    const params = (message.params ?? {}) as { name?: unknown; arguments?: Json }
    if (params.name !== SKILL_SEARCH_TOOL.name) {
      return { jsonrpc: '2.0', id, error: { code: -32602, message: `Unknown tool: ${String(params.name)}` } }
    }
    const query = String(params.arguments?.query ?? '').trim()
    if (!query) return reply(text('skill_search needs a query describing the task.'))
    // `retrieveForTurn` already swallows every failure and answers `''`, so a
    // miss and a breakage arrive here the same way. Both have to read to the
    // model as "carry on" rather than as a broken tool.
    const block = await search(query)
    return reply(text(block || `No skill in the library matches "${query}". Proceed without one.`))
  }
  return { jsonrpc: '2.0', id, error: { code: -32601, message: `Method not found: ${String(method)}` } }
}

/** Read JSON-RPC lines from stdin, write answers to stdout. */
export function serve(config: SkillSearchConfig): void {
  const lines = createInterface({ input: process.stdin })
  lines.on('line', line => {
    if (!line.trim()) return
    let message: Message
    try {
      message = JSON.parse(line) as Message
    } catch {
      // Malformed input with no id to answer against: there is nobody to tell.
      return
    }
    void handle(message, config)
      .then(response => { if (response) process.stdout.write(`${JSON.stringify(response)}\n`) })
      .catch(() => {
        if (message.id !== undefined) {
          process.stdout.write(`${JSON.stringify({
            jsonrpc: '2.0',
            id: message.id,
            error: { code: -32603, message: 'skill search failed' },
          })}\n`)
        }
      })
  })
}

/**
 * Start only when run as a program.
 *
 * `serve` holds stdin open for the life of the process, which is right for a
 * server and wrong for anything that merely imports this module: without the
 * guard, an importer — a test, or a tool reading `SKILL_SEARCH_TOOL` — has its
 * own stdin taken over and never exits.
 *
 * The mode is read even though the manifest points the host here only in
 * on-demand mode: `mode` lives in the same file a user edits, and a server
 * still answering after someone switched to auto would offer a tool whose
 * results the hook is already injecting.
 */
function main(): void {
  const config = loadConfig(readConfigDocument())
  if (config.mode === 'on_demand') serve(config)
}

if (argv[1] && fileURLToPath(import.meta.url) === argv[1]) main()
