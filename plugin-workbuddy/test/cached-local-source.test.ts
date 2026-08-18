/**
 * The disk cache: it must serve the same skills the parent scan would, miss
 * when a `SKILL.md` changes, and never let a broken cache file cost a turn.
 */

import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { CachedLocalSkillSource } from '../src/cached-local-source.ts'

async function skillDir(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'skillsearch-cache-'))
  await mkdir(join(root, 'skills', 'alpha'), { recursive: true })
  await writeFile(
    join(root, 'skills', 'alpha', 'SKILL.md'),
    '---\nname: alpha\ndescription: extract tables from a scanned invoice\n---\n\nbody\n',
  )
  return root
}

function source(root: string, cachePath: string): CachedLocalSkillSource {
  return new CachedLocalSkillSource([{ path: join(root, 'skills'), name: 'local' }], { cachePath })
}

test('the first scan writes the cache and the second reads it', async () => {
  const root = await skillDir()
  const cachePath = join(root, 'cache', 'index.json')

  const first = await source(root, cachePath).listAll()
  assert.equal(first.length, 1)
  assert.equal(first[0]?.name, 'alpha')

  // A second source shares nothing in memory with the first, so an equal
  // answer here can only have come off disk.
  const second = await source(root, cachePath).listAll()
  assert.deepEqual(second, first)

  const onDisk: unknown = JSON.parse(await readFile(cachePath, 'utf8'))
  assert.equal((onDisk as { version: number }).version, 1)
})

test('an edited skill invalidates it', async () => {
  const root = await skillDir()
  const cachePath = join(root, 'cache', 'index.json')
  const before = source(root, cachePath)
  const stale = before.fingerprint()
  await before.listAll()

  // mtime has second-level granularity on some filesystems; write a distinct
  // one rather than racing it.
  const file = join(root, 'skills', 'alpha', 'SKILL.md')
  await writeFile(file, '---\nname: alpha\ndescription: now about something else\n---\n\nbody\n')
  const { utimes } = await import('node:fs/promises')
  const later = new Date(Date.now() + 5_000)
  await utimes(file, later, later)

  const after = source(root, cachePath)
  assert.notEqual(after.fingerprint(), stale)
  const skills = await after.listAll()
  assert.match(String(skills[0]?.description), /something else/)
})

test('a new skill invalidates it', async () => {
  const root = await skillDir()
  const cachePath = join(root, 'cache', 'index.json')
  await source(root, cachePath).listAll()

  await mkdir(join(root, 'skills', 'beta'), { recursive: true })
  await writeFile(
    join(root, 'skills', 'beta', 'SKILL.md'),
    '---\nname: beta\ndescription: second skill\n---\n\nbody\n',
  )

  const skills = await source(root, cachePath).listAll()
  assert.equal(skills.length, 2)
})

test('a corrupt cache file falls back to scanning', async () => {
  const root = await skillDir()
  const cachePath = join(root, 'cache', 'index.json')
  await mkdir(join(root, 'cache'), { recursive: true })
  await writeFile(cachePath, '{ not json')

  const skills = await source(root, cachePath).listAll()
  assert.equal(skills.length, 1)
})

test('an unwritable cache path costs a rescan, not the search', async () => {
  const root = await skillDir()
  // A path under a file, which cannot be created as a directory.
  const blocked = join(root, 'skills', 'alpha', 'SKILL.md', 'index.json')

  const skills = await source(root, blocked).listAll()
  assert.equal(skills.length, 1)
})

test('an empty cache path runs the parent uncached', async () => {
  const root = await skillDir()
  const plain = new CachedLocalSkillSource(
    [{ path: join(root, 'skills'), name: 'local' }],
    { cachePath: '' },
  )
  assert.equal((await plain.listAll()).length, 1)
  await rm(root, { recursive: true, force: true })
})
