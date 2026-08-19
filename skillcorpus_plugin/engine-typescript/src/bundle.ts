/**
 * Materialising a catalog skill's own files on disk.
 *
 * A skill body routinely says `run scripts/x.py` or `see references/y.md`.
 * For a local skill those paths already exist. For one that arrived from a
 * catalog they mean nothing until its bundle is extracted — so without
 * this, a remote skill is readable and not runnable, and the model is told
 * to open files that are not there.
 *
 * The archive is untrusted. Every limit here mirrors the Python
 * implementation's, so one bundle extracts to the same files on either
 * runtime: a path that escapes the destination fails the whole install, a
 * suffix outside the allowlist is skipped, and per-file and total size caps
 * bound what a crafted archive can write.
 *
 * @module
 */

import { mkdir, rename, rm, writeFile } from 'node:fs/promises'
import { readdir } from 'node:fs/promises'
import { isAbsolute, join, relative, resolve } from 'node:path'
import { readZipEntries } from './zip.js'

/** 8 MiB per file. */
const MAX_ENTRY_BYTES = 8 * 1024 * 1024
/** 64 MiB uncompressed across the archive. */
const MAX_TOTAL_BYTES = 64 * 1024 * 1024

/**
 * Suffixes an extracted bundle may contain, and the identical set the Python
 * implementation allows — divergence here would mean one bundle producing
 * different files on two hosts.
 *
 * An extension-less file is allowed, which is how a bundle ships a
 * `LICENSE` or a `Makefile`. `.env` is allowed for the same reason it is
 * there: a skill may legitimately ship a sample. Neither is executed by
 * this package; a deployment that will not tolerate either should reject
 * the bundle upstream, at the catalog.
 */
const ALLOWED_SUFFIXES = new Set([
  '', '.md', '.txt', '.json', '.jsonl', '.yaml', '.yml', '.toml', '.csv', '.tsv',
  '.cfg', '.ini', '.xml', '.html', '.htm', '.sql', '.env', '.sh', '.py', '.js',
  '.mjs', '.cjs', '.ts', '.rb', '.pl', '.lua', '.ps1', '.bat', '.svg', '.png',
  '.jpg', '.jpeg', '.gif', '.webp', '.pdf',
])

function suffixOf(name: string): string {
  const base = name.slice(name.lastIndexOf('/') + 1)
  const dot = base.lastIndexOf('.')
  return dot <= 0 ? '' : base.slice(dot).toLowerCase()
}

/**
 * Extract an archive into `destination`, atomically.
 *
 * Staged and renamed rather than written in place, because `destination`
 * existing is what tells a later call the bundle is cached: an entry that
 * fails partway would otherwise leave a half-written directory that every
 * later install reads as a hit, feeding the agent a truncated skill.
 *
 * @param archive - the downloaded zip bytes.
 * @param destination - the cache directory this bundle should become.
 * @throws Error on a path that escapes the destination, or an archive over
 *   the total budget. Both leave nothing behind.
 */
export async function extractBundle(archive: Buffer, destination: string): Promise<void> {
  const staging = `${destination}.incoming-${process.pid}-${Math.random().toString(16).slice(2, 10)}`
  const root = resolve(staging)
  let total = 0
  try {
    await mkdir(staging, { recursive: true })
    for (const entry of readZipEntries(archive)) {
      const target = resolve(root, entry.name)
      // Containment by path, not by string prefix: `..` inside the name is
      // what this rejects, and resolve() is what makes it visible.
      const inside = relative(root, target)
      if (inside.startsWith('..') || isAbsolute(inside)) {
        throw new Error(`unsafe zip path: ${entry.name}`)
      }
      if (!ALLOWED_SUFFIXES.has(suffixOf(entry.name))) continue
      if (entry.declaredSize > MAX_ENTRY_BYTES) continue
      if (total + entry.declaredSize > MAX_TOTAL_BYTES) {
        throw new Error('zip uncompressed total too large')
      }
      const data = entry.read()
      total += data.length
      await mkdir(join(target, '..'), { recursive: true })
      await writeFile(target, data)
    }
    try {
      await rename(staging, destination)
    } catch (error) {
      // A concurrent installer that won the rename extracted the same
      // version, so either copy is correct; anything else is a real failure.
      const { access } = await import('node:fs/promises')
      await access(destination).catch(() => { throw error })
      await rm(staging, { recursive: true, force: true })
    }
  } catch (error) {
    await rm(staging, { recursive: true, force: true })
    throw error
  }
}

/**
 * The directory the skill's own paths are relative to.
 *
 * Catalog zips usually wrap the whole skill in one `<skill>/` directory, so
 * `SKILL.md` and `scripts/` sit a level below the extraction root. A lone
 * wrapper is collapsed; a flat archive keeps the root.
 *
 * @param destination - the extracted bundle directory.
 * @returns the directory to resolve the body's references against.
 */
export async function bundleRoot(destination: string): Promise<string> {
  let entries
  try {
    entries = await readdir(destination, { withFileTypes: true })
  } catch {
    return destination
  }
  const visible = entries.filter(entry => !entry.name.startsWith('.'))
  const only = visible[0]
  if (visible.length === 1 && only?.isDirectory()) return join(destination, only.name)
  return destination
}
