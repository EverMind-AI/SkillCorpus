/**
 * The hook against the contract WorkBuddy 5.3.13 speaks on the wire.
 *
 * The host is not installed here, so what is pinned instead is the exchange:
 * the stdin payload copied from a real turn, the stdout shape the host reads,
 * and the rule that neither a broken payload nor a broken retrieval may cost
 * the turn.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { DEFAULTS, type SkillSearchConfig } from '../src/config.ts'
import { queryOf, resultFor, runTurn, selectedSkills } from '../src/hook.ts'

/** A payload as observed on stdin, fields and all. */
const REAL_PAYLOAD = {
  session_id: '0be9b0a6-8ce4-40fc-afe3-40b7c6f15f8a',
  transcript_path: '/Users/x/.workbuddy-ai/projects/p/0be9b0a6.jsonl',
  cwd: '/Users/x/WorkBuddy AI/2026-08-18-15-02-45',
  hook_event_name: 'UserPromptSubmit',
  prompt: '把这个设计稿转成前端代码',
  permission_mode: 'bypassPermissions',
  client: 'WorkBuddy',
  version: '5.3.13',
  model: 'fast-model',
}

async function config(overrides: Partial<SkillSearchConfig> = {}): Promise<SkillSearchConfig> {
  const dir = await mkdtemp(join(tmpdir(), 'skillsearch-hook-'))
  return { ...DEFAULTS, logPath: join(dir, 'log.jsonl'), ...overrides }
}

test('a hit becomes additionalContext under the event name the host matches on', async () => {
  const result = await runTurn(JSON.stringify(REAL_PAYLOAD), {
    config: await config(),
    retrieveFn: async () => '# Skills\n\nardot-design-to-code',
  })

  assert.equal(result.continue, true)
  assert.equal(result.hookSpecificOutput?.hookEventName, 'UserPromptSubmit')
  assert.match(String(result.hookSpecificOutput?.additionalContext), /ardot-design-to-code/)
})

test('no hit injects nothing at all, rather than an empty section', async () => {
  const result = await runTurn(JSON.stringify({ ...REAL_PAYLOAD, prompt: '今天天气怎么样' }), {
    config: await config(),
    retrieveFn: async () => '',
  })

  assert.equal(result.continue, true)
  assert.equal(result.hookSpecificOutput, undefined)
})

test('a turn with no prompt never reaches retrieval', async () => {
  let called = false
  const result = await runTurn(JSON.stringify({ ...REAL_PAYLOAD, prompt: '   ' }), {
    config: await config(),
    retrieveFn: async () => { called = true; return 'x' },
  })

  assert.equal(called, false)
  assert.deepEqual(result, { continue: true })
})

test('unparseable stdin still answers the host', async () => {
  const result = await runTurn('not json at all', {
    config: await config(),
    retrieveFn: async () => 'unreachable',
  })

  assert.deepEqual(result, { continue: true })
})

test('a retrieval that rejects costs the skills, not the turn', async () => {
  const result = await runTurn(JSON.stringify(REAL_PAYLOAD), {
    config: await config(),
    retrieveFn: async () => { throw new Error('hub is down') },
  })

  // The host raises HookBlockedError on a failed hook and the user's message
  // never reaches the model, so this must resolve rather than throw.
  assert.deepEqual(result, { continue: true })
})

test('every turn leaves one line behind, because the transcript keeps none', async () => {
  const resolved = await config()
  await runTurn(JSON.stringify(REAL_PAYLOAD), {
    config: resolved,
    retrieveFn: async () => '# Skills\n\nbody',
  })

  const line: unknown = JSON.parse(readFileSync(resolved.logPath, 'utf8').trim())
  const entry = line as Record<string, unknown>
  assert.equal(entry.prompt, REAL_PAYLOAD.prompt)
  assert.equal(entry.model, 'fast-model')
  assert.equal(entry.injected_chars, '# Skills\n\nbody'.length)
})

test('queryOf and resultFor hold the two ends of the contract', () => {
  assert.equal(queryOf({ prompt: '  x  ' }), 'x')
  assert.equal(queryOf({}), '')
  assert.deepEqual(resultFor(''), { continue: true })
  assert.equal(resultFor('b').hookSpecificOutput?.additionalContext, 'b')
})

test('the log names the skills, because the transcript will not', async () => {
  const resolved = await config()
  await runTurn(JSON.stringify(REAL_PAYLOAD), {
    config: resolved,
    retrieveFn: async () => '# Skills\n\n### Skill: alpha  [local/alpha]\nbody\n### Skill: beta  [local/beta]\nbody',
  })

  const entry = JSON.parse(readFileSync(resolved.logPath, 'utf8').trim()) as Record<string, unknown>
  assert.deepEqual(entry.skills, ['alpha[local]', 'beta[local]'])
})

test('a skill name containing a space is logged whole', () => {
  // `\\S+` stopped at the first space, so a catalog skill called
  // "PDF Tables" was logged as "PDF".
  const block = '### Skill: PDF Tables  [hub/abc-123/x]\n\nbody\n'
  assert.deepEqual(selectedSkills(block), ['PDF Tables[hub]'])
})
