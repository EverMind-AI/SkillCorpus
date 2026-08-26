import assert from 'node:assert/strict'
import { access, mkdir, mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, test } from 'node:test'
import { MarketplaceClient, MarketplaceSkillSource } from '../../engine-typescript/src/marketplace-source.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

test('ClawHub maps trusted search results and caps one source at two', async () => {
  let seen = ''
  globalThis.fetch = async input => {
    seen = String(input)
    return new Response(JSON.stringify({ results: [
      { id: '1', slug: 'one', displayName: 'One', summary: 'first', score: 0.9, ownerHandle: 'alice', trust: { installability: 'installable' } },
      { id: '2', slug: 'two', displayName: 'Two', summary: 'second', score: 0.8, ownerHandle: 'bob', trust: { installability: 'installable' } },
      { id: '3', slug: 'three', displayName: 'Three', summary: 'third', score: 0.7, trust: { installability: 'installable' } },
    ] }), { status: 200 })
  }
  const client = new MarketplaceClient('clawhub', 'https://clawhub.test', { cacheDir: await mkdtemp(join(tmpdir(), 'market-')) })
  const hits = await new MarketplaceSkillSource(client).search(
    'Please help me extract text from a PDF invoice', {}, 9,
  )
  assert.equal(hits.length, 2)
  assert.equal(hits[0]?.qualifiedId, 'clawhub/1')
  assert.equal(
    new URL(seen).searchParams.get('q'),
    'Please help me extract text from a PDF invoice',
  )
  assert.match(seen, /nonSuspiciousOnly=true/)
  assert.match(seen, /limit=2/)
})

test('skillhub.cn uses its public API and drops malicious entries', async () => {
  globalThis.fetch = async input => {
    assert.match(String(input), /\/api\/skills\?keyword=design/)
    return new Response(JSON.stringify({ code: 0, data: { skills: [
      { slug: 'bad', name: 'Bad', score: 99, securityReports: { scan: { status: 'malicious' } } },
      { slug: 'good', name: 'Good', description: 'design helper', score: 8, version: '1.0.0' },
    ] } }), { status: 200 })
  }
  const client = new MarketplaceClient('skillhub_cn', 'https://api.skillhub.test', { cacheDir: await mkdtemp(join(tmpdir(), 'market-')) })
  const hits = await new MarketplaceSkillSource(client).search('design', {}, 2)
  assert.deepEqual(hits.map(hit => hit.name), ['Good'])
  assert.equal(hits[0]?.meta.version, '1.0.0')
})

test('a pre-aborted turn reaches fetch already cancelled', async () => {
  const controller = new AbortController()
  controller.abort()
  let called = false
  globalThis.fetch = async (_input, init) => {
    called = true
    assert.equal(init?.signal?.aborted, true)
    throw new DOMException('aborted', 'AbortError')
  }
  const client = new MarketplaceClient('clawhub', 'https://clawhub.test', { cacheDir: await mkdtemp(join(tmpdir(), 'market-')) })
  await assert.rejects(() => client.search('design', controller.signal), /aborted/)
  assert.equal(called, true)
})

test('Marketplace install identifies download failures', async () => {
  globalThis.fetch = async () => new Response('down', { status: 503 })
  const cacheDir = await mkdtemp(join(tmpdir(), 'market-'))
  const client = new MarketplaceClient('clawhub', 'https://clawhub.test', { cacheDir })
  await assert.rejects(
    () => client.install({
      qualifiedId: 'clawhub/one', name: 'One', content: '', score: 1,
      meta: { id: 'one', slug: 'one', version: 'v0' },
    }),
    /download failed: clawhub returned HTTP 503/,
  )
})

test('Marketplace install identifies invalid archives as extraction failures', async () => {
  globalThis.fetch = async () => new Response('not a zip', { status: 200 })
  const cacheDir = await mkdtemp(join(tmpdir(), 'market-'))
  const client = new MarketplaceClient('clawhub', 'https://clawhub.test', { cacheDir })
  await assert.rejects(
    () => client.install({
      qualifiedId: 'clawhub/one', name: 'One', content: '', score: 1,
      meta: { id: 'one', slug: 'one', version: 'v0' },
    }),
    /extract failed:/,
  )
})

test('a cached bundle without SKILL.md is removed for a future retry', async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), 'market-'))
  const destination = join(cacheDir, 'clawhub-one@v0')
  await mkdir(destination)
  const client = new MarketplaceClient('clawhub', 'https://clawhub.test', { cacheDir })
  await assert.rejects(
    () => client.install({
      qualifiedId: 'clawhub/one', name: 'One', content: '', score: 1,
      meta: { id: 'one', slug: 'one', version: 'v0' },
    }),
    /read skill failed:/,
  )
  await assert.rejects(() => access(destination))
})
