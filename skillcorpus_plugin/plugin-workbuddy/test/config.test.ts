/**
 * Configuration: the file states the deployment, the environment states the
 * secret, and the defaults are a working install with neither.
 */

import assert from 'node:assert/strict'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { dataDirectory, DEFAULTS, MAX_TIMEOUT_MS, loadConfig, marketplaceName, readConfigDocument, unknownMode } from '../src/config.ts'

test('the defaults search the two directories WorkBuddy keeps skills in', () => {
  const config = loadConfig(undefined, {})
  assert.deepEqual(config.skillsDirs, [
    '~/.workbuddy-ai/skills',
    '~/.workbuddy-ai/plugins/cache',
  ])
  assert.equal(config.hubEndpoint, 'https://skillhub.evermind.ai')
  assert.equal(config.gate, undefined)
})

test('an explicitly empty endpoint disables each default remote source', () => {
  const config = loadConfig(
    { hubEndpoint: '', clawhubEndpoint: '', skillhubCnEndpoint: '' },
    {},
  )
  assert.equal(config.hubEndpoint, '')
  assert.equal(config.clawhubEndpoint, '')
  assert.equal(config.skillhubCnEndpoint, '')
})

test('the default deadline allows the measured public hubs but stays below the host timeout', () => {
  assert.equal(DEFAULTS.timeoutMs, MAX_TIMEOUT_MS)
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

test('an explicit marketplace environment override wins', () => {
  assert.equal(marketplaceName('/repo/plugin-workbuddy/src/hook.ts', {
    SKILLSEARCH_MARKETPLACE: 'team-market',
  }), 'team-market')
})

test('missing or malformed launch paths use the neutral fallback', () => {
  assert.equal(marketplaceName('/repo/plugin-workbuddy/src/hook.ts', {}), 'skillcorpus-marketplace')
  assert.equal(marketplaceName('', {}), 'skillcorpus-marketplace')
  assert.equal(marketplaceName('/x/plugins/cache/../skillsearch/x/hook.mjs', {}), 'skillcorpus-marketplace')
  assert.equal(marketplaceName('', { SKILLSEARCH_MARKETPLACE: '../bad' }), 'skillcorpus-marketplace')
})

test('the data directory supports an explicit environment override', () => {
  assert.equal(dataDirectory('', { SKILLSEARCH_DATA_DIR: '/state/skillsearch' }, '/home/u'), '/state/skillsearch')
  assert.equal(dataDirectory('', { SKILLSEARCH_DATA_DIR: '~/state' }, '/home/u'), '/home/u/state')
})

test('an existing marketplace install keeps using its marketplace data directory', () => {
  const legacyMarket = ['me', 'mmy-marketplace'].join('')
  const script = `/home/u/.workbuddy-ai/plugins/cache/${legacyMarket}/skillsearch/0.1.0/dist/hook.mjs`
  assert.equal(
    dataDirectory(script, {}, '/home/u'),
    `/home/u/.workbuddy-ai/plugins/data/skillsearch-${legacyMarket}`,
  )
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

test('an unrecognised mode is narrowed, and reportable', () => {
  // Narrowing is deliberate: a typo must not cost the deployment its
  // retrieval. Narrowing *silently* is the problem — it lands on the opposite
  // mode from the one that was typed, and 0.3.0 changed which mode that is.
  assert.equal(loadConfig({ mode: 'atuo' }, {}).mode, 'on_demand')
  assert.equal(unknownMode({ mode: 'atuo' }, {}), 'atuo')

  // The hook writes this into its log line; nothing to report for a value
  // that is valid or absent.
  assert.equal(unknownMode({ mode: 'auto' }, {}), undefined)
  assert.equal(unknownMode({ mode: 'on_demand' }, {}), undefined)
  assert.equal(unknownMode({}, {}), undefined)
  assert.equal(unknownMode(undefined, {}), undefined)
})

test('the environment is what gets reported when the environment is wrong', () => {
  // Same precedence as `loadConfig`: the variable wins, so a typo there must
  // be the one named even when the config file holds a valid value.
  const env = { SKILLSEARCH_MODE: 'aut0' }
  assert.equal(loadConfig({ mode: 'auto' }, env).mode, 'on_demand')
  assert.equal(unknownMode({ mode: 'auto' }, env), 'aut0')
})
