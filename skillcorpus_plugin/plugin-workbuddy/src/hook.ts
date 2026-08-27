#!/usr/bin/env node
/**
 * The `UserPromptSubmit` hook: read the turn from stdin, answer on stdout.
 *
 * The whole seam is two JSON documents and an exit code. Three rules follow
 * from how WorkBuddy 5.3.13 treats them, and all three were established by
 * running against the host rather than by reading its documentation:
 *
 *   1. **Never fail.** A hook that exits non-zero or writes nothing usable
 *      raises `HookBlockedError` upstream and the user's message never
 *      reaches the model. Losing this turn's skills is a cost; losing the
 *      turn is a bug. Everything below is wrapped accordingly.
 *   2. **Inject data, never instructions.** The block is presented to the
 *      model as `<system-reminder data-role="hook">`, and an imperative in
 *      there reads as an injection attempt — the model says so in its
 *      reasoning and discards the block. The engine's rendered `# Skills`
 *      section is already prose about skills, which is why it survives.
 *   3. **Say nothing when there is nothing.** Omitting
 *      `hookSpecificOutput` injects no text at all, which is cheaper than
 *      injecting an empty section and safer than injecting a weak hit.
 *
 * @module
 */

import { appendFileSync, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import type { SourceDiagnostic } from '../../engine-typescript/src/engine.js'
import { loadConfig, readConfigDocument, type SkillSearchConfig } from './config.js'
import { retrieveForTurn } from './retrieve.js'
import type { UserPromptSubmitPayload, UserPromptSubmitResult } from './workbuddy-types.js'

/** Read stdin to the end. A hook with no input still has to answer. */
export async function readStdin(stream: NodeJS.ReadableStream = process.stdin): Promise<string> {
  if ((stream as NodeJS.ReadStream).isTTY) return ''
  const chunks: string[] = []
  stream.setEncoding('utf8')
  for await (const chunk of stream) chunks.push(String(chunk))
  return chunks.join('')
}

/** The user's message, or `''` for a payload that carries none. */
export function queryOf(payload: UserPromptSubmitPayload): string {
  return typeof payload.prompt === 'string' ? payload.prompt.trim() : ''
}

/**
 * The skills a rendered block put in front of the model.
 *
 * Parsed back out of the text rather than carried alongside it, because
 * `retrieve` returns prose and the alternative — running the pipeline twice,
 * or reaching past it to `hits` — costs either latency or the guarantee that
 * what is logged is what was injected.
 */
export function selectedSkills(block: string): string[] {
  // `name[source]`, so a turn where the catalog silently failed is visible in
  // the log as an all-local line rather than as nothing at all.
  //
  // The name runs to the bracket rather than to the first space: a catalog
  // skill may well be called "PDF Tables", and `\S+` logged it as "PDF".
  return [...block.matchAll(/^### Skill: (.+?)\s+\[([^/\]]+)\//gm)]
    .map(match => `${match[1]}[${match[2]}]`)
}

/** The stdout document for a block, or for the absence of one. */
export function resultFor(block: string): UserPromptSubmitResult {
  return block
    ? {
      continue: true,
      hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: block },
    }
    : { continue: true }
}

/**
 * One line of JSON per turn.
 *
 * The host drops the injected block from the transcript once the model has
 * seen it, so this file is the only record of what was put in front of it.
 */
export function log(config: SkillSearchConfig, entry: Record<string, unknown>): void {
  if (!config.logPath) return
  try {
    mkdirSync(dirname(config.logPath), { recursive: true })
    appendFileSync(config.logPath, `${JSON.stringify({ ts: new Date().toISOString(), ...entry })}\n`)
  } catch {
    // Observability is not worth a turn.
  }
}

/**
 * Run one turn end to end.
 *
 * @param input - the raw stdin document.
 * @param deps - test seams for configuration and retrieval.
 * @returns the stdout document.
 */
export async function runTurn(
  input: string,
  deps: {
    config?: SkillSearchConfig
    retrieveFn?: typeof retrieveForTurn
  } = {},
): Promise<UserPromptSubmitResult> {
  const config = deps.config ?? loadConfig(readConfigDocument())
  const startedAt = Date.now()

  let payload: UserPromptSubmitPayload = {}
  try {
    const parsed: unknown = JSON.parse(input || '{}')
    if (parsed && typeof parsed === 'object') payload = parsed as UserPromptSubmitPayload
  } catch {
    // Not JSON: nothing to search for, and nothing to complain to.
  }

  const query = queryOf(payload)
  let block = ''
  let failure: string | null = null
  const sourceDiagnostics: SourceDiagnostic[] = []
  if (query) {
    try {
      block = await (deps.retrieveFn ?? retrieveForTurn)(
        query, config, {}, diagnostic => { sourceDiagnostics.push(diagnostic) }, payload.cwd,
      )
    } catch (error) {
      // `retrieveForTurn` already promises never to reject. This is the
      // guarantee held where it matters rather than where it is made: a
      // future source, or a caller passing its own `retrieveFn`, must not be
      // able to turn a failed search into a failed turn.
      failure = error instanceof Error ? error.message : String(error)
    }
  }

  log(config, {
    prompt: query.slice(0, 120),
    model: payload.model ?? null,
    agent_type: payload.agent_type ?? null,
    skills: selectedSkills(block),
    injected_chars: block.length,
    elapsed_ms: Date.now() - startedAt,
    sources: sourceDiagnostics,
    error: failure,
  })

  return resultFor(block)
}

/* c8 ignore start -- the process shell, exercised by the host and by hand */
async function main(): Promise<void> {
  let result: UserPromptSubmitResult = { continue: true }
  try {
    result = await runTurn(await readStdin())
  } catch {
    // Rule 1: an unexpected failure still owes the host a usable answer.
  }
  // Exit inside the write callback, never after the call: past 64 KiB a
  // pipe write goes asynchronous, and `process.exit` right after it truncates
  // the document — the host reads unusable JSON and blocks the whole turn.
  // Reachable: two catalog bodies can exceed 64 KiB once JSON-escaped.
  process.stdout.write(JSON.stringify(result), () => { process.exit(0) })
}

const invokedDirectly = process.argv[1] !== undefined
  && /hook\.(mjs|ts|js)$/.test(process.argv[1])
if (invokedDirectly) void main()
/* c8 ignore stop */
