/**
 * Configuration: the file states the deployment, the environment states the
 * secret, and the defaults are a working install with neither.
 */

import assert from 'node:assert/strict'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { DEFAULTS, MAX_TIMEOUT_MS, loadConfig, marketplaceName, readConfigDocument } from '../src/config.ts'

test('the defaults search the two directories WorkBuddy keeps skills in', () => {
  const config = loadConfig(undefined, {})
  assert.deepEqual(config.skillsDirs, [
    '~/.workbuddy-ai/skills',
    '~/.workbuddy-ai/plugins/cache',
  ])
  assert.equal(config.hubEndpoint, '')
  assert.equal(config.gate, undefined)
})

test('the deadline is tighter than the shared default, because it is visible silence', () => {
  assert.ok(DEFAULTS.timeoutMs <= 3000, `${DEFAULTS.timeoutMs}ms is too long to hold a turn`)
  assert.equal(DEFAULTS.rewrite, false)
})

test('the environment wins over the document', () => {
  const config = loadConfig(
    { hubEndpoint: 'https://from-file', topK: 9 },
    { SKILLSEARCH_HUB_ENDPOINT: 'https://from-env' },
  )
  assert.equal(config.hubEndpoint, 'https://from-env')
  assert.equal(config.topK, 9)
})

test('a false in the environment is read as false, not as a non-empty string', () => {
  assert.equal(loadConfig({ rewrite: true }, { SKILLSEARCH_REWRITE: 'false' }).rewrite, false)
})

test('a missing or unreadable document is the normal case', async () => {
  assert.deepEqual(readConfigDocument(join(tmpdir(), 'no-such-skillsearch.json')), {})

  const dir = await mkdtemp(join(tmpdir(), 'skillsearch-config-'))
  const path = join(dir, 'config.json')
  await writeFile(path, '{ "topK": 7 }')
  assert.deepEqual(readConfigDocument(path), { topK: 7 })

  await writeFile(path, 'not json')
  assert.deepEqual(readConfigDocument(path), {})
})

test('the local directory outranks the catalog, which is a seating order not a score', () => {
  const config = loadConfig(undefined, {})
  assert.ok(config.localWeight > config.hubWeight,
    'local must outweigh hub: installed skills run here, catalog skills only might')
  assert.equal(loadConfig({ hubWeight: 1.0 }, {}).hubWeight, 1.0)
})

test('fusion damping defaults to 10 here, not the engine paper-default 60', () => {
  // At 60 with topK 3, the weight gap between sources exceeds every rank gap
  // within one, and fusion degenerates into whole-source blocks.
  assert.equal(loadConfig(undefined, {}).rrfK, 10)
  assert.equal(loadConfig(undefined, { SKILLSEARCH_RRF_K: '60' }).rrfK, 60)
})

test('the data directory follows the marketplace the hook was installed from', () => {
  // The host allots `skillsearch-<market>`, and nothing hands a hook the
  // name — but its own path carries it. Hard-coding one marketplace meant a
  // copy installed from any other read no `config.json` at all and wrote its
  // log and index cache into a directory the host does not allot it.
  assert.equal(
    marketplaceName('/home/u/.workbuddy-ai/plugins/cache/acme-store/skillsearch/0.1.0/dist/hook.mjs'),
    'acme-store',
  )
})

test('a Windows install path resolves the same name', () => {
  assert.equal(
    marketplaceName(String.raw`C:\Users\u\.workbuddy-ai\plugins\cache\win-market\skillsearch\0.1.0\dist\hook.mjs`),
    'win-market',
  )
})

test('a path that is not a marketplace install keeps the historical name', () => {
  // A dev checkout run directly, and the empty argv of an embedded import.
  assert.equal(marketplaceName('/repo/plugin-workbuddy/src/hook.ts'), 'memmy-marketplace')
  assert.equal(marketplaceName(''), 'memmy-marketplace')
})

test('timeoutMs is clamped below the host\'s own hook timeout', async () => {
  // `hooks.json` gives the hook 10s; past that the host kills the process,
  // and a killed hook fails the user's turn rather than costing it its
  // skills. Two settings that have to stay ordered.
  const dir = await mkdtemp(join(tmpdir(), 'wb-clamp-'))
  const path = join(dir, 'config.json')
  await writeFile(path, JSON.stringify({ timeoutMs: 30_000 }), 'utf8')
  assert.equal(loadConfig(readConfigDocument(path), {}).timeoutMs, MAX_TIMEOUT_MS)
})

test('a timeout under the ceiling is left alone', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'wb-clamp-'))
  const path = join(dir, 'config.json')
  await writeFile(path, JSON.stringify({ timeoutMs: 1500 }), 'utf8')
  assert.equal(loadConfig(readConfigDocument(path), {}).timeoutMs, 1500)
})
