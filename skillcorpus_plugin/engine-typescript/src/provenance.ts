/**
 * Where an installed skill came from, recorded beside the skill itself.
 *
 * The counterpart of `engine-python/skillsearch/provenance.py`. Both ports
 * read and write these markers in the same shared directory on one machine,
 * so the marker format and the identity string are a contract, not a
 * convention; `tests/parity.test.ts` pins them.
 *
 * Installing into the shared directory — rather than a cache excluded from
 * scanning — recreates the problem the exclusion existed to avoid: the skill
 * is now both a local hit (it is on disk) and a remote hit (the catalogue
 * still returns it), so one skill takes two slots in the ranking.
 *
 * Neither existing defence catches that. Fusion collapses on `qualifiedId`,
 * and `local/pdf-tables` and `hub/pdf-tables` are different ids; the exact-body
 * dedup compares a digest, so a trailing newline or a bumped version misses.
 *
 * The fix is identity rather than coincidence. An install writes a marker
 * inside the skill's own directory; the scanner reads it and carries the
 * identity on the hit; fusion collapses on that. A skill installed from `hub`
 * and the same skill offered by `hub` are one thing by construction.
 *
 * The marker doubles as the ledger. Installing puts files on a user's disk, so
 * they must be able to see what is there and remove it — and one file per
 * skill, inside the skill, cannot drift out of sync with the directory the way
 * a central index can.
 *
 * Everything here fails open: an unreadable marker leaves the skill looking
 * hand-written, which costs deduplication for that one skill and never a turn.
 *
 * @module
 */

import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Inside the skill's own directory. Dotted so a host's own scanner ignores it,
 * and named for this plugin so its owner is obvious to someone browsing.
 */
export const MARKER = '.skillsearch-origin.json'

const NOTE = 'Written by the skillsearch plugin. Delete the directory to uninstall.'

/** What an installed skill is, and where it came from. */
export interface Origin {
  /** `<source>/<slug>`. The identity fusion collapses on. */
  readonly origin: string
  readonly source: string
  readonly slug: string
  readonly version: string
  readonly sha256: string
  readonly installedAt: string
}

/**
 * The cross-source identity of one skill.
 *
 * Deliberately not the `qualifiedId`: that is `<source>/<native id>` where
 * source is the *retrieval* source, so one skill reached two ways has two of
 * them. This is what the skill *is*.
 */
export function identity(source: string, slug: string): string {
  return `${String(source).trim()}/${String(slug).trim()}`
}

/**
 * SHA-256 of a skill body, for the ledger.
 *
 * Recorded so an update can say what changed. Not used for deduplication —
 * that is what the identity is for, precisely because a digest misses a
 * bumped version.
 */
export function bodyDigest(body: string): string {
  return createHash('sha256').update(body ?? '', 'utf8').digest('hex')
}

/** An install timestamp, UTC and second resolution. */
export function now(clock: () => Date = () => new Date()): string {
  return `${clock().toISOString().slice(0, 19)}+00:00`
}

/**
 * Where a skill from `source` lands under `root`.
 *
 * One directory per identity, not per version: an update replaces what is
 * there rather than accumulating copies, which is what keeps the shared
 * directory from growing a second ranked copy of everything.
 *
 * The name is sanitised because a slug comes from a catalogue and reaches the
 * filesystem — anything outside the allow-list becomes `_`, so a slug of
 * `../../etc` cannot escape `root`.
 */
export function slugDir(root: string, source: string, slug: string): string {
  const safeSource = String(source).replace(/[^A-Za-z0-9\-_]/g, '_').slice(0, 40)
  const safeSlug = String(slug).replace(/[^A-Za-z0-9\-_.@]/g, '_').slice(0, 120)
  return join(root, `${safeSource}__${safeSlug || 'skill'}`)
}

/**
 * Record provenance inside an installed skill. `false` on failure.
 *
 * Written atomically for the same reason the bundle is: a reader walking the
 * shared directory must never see half a marker.
 */
export function writeMarker(skillDir: string, origin: Origin): boolean {
  const payload = {
    _note: NOTE,
    version: 1,
    origin: origin.origin,
    source: origin.source,
    slug: origin.slug,
    skill_version: origin.version,
    sha256: origin.sha256,
    installed_at: origin.installedAt,
  }
  let staging: string | undefined
  try {
    mkdirSync(skillDir, { recursive: true })
    staging = mkdtempSync(join(skillDir, '.origin-'))
    const scratch = join(staging, 'marker.json')
    writeFileSync(scratch, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
    renameSync(scratch, join(skillDir, MARKER))
    return true
  } catch {
    return false
  } finally {
    if (staging) {
      try {
        rmSync(staging, { recursive: true, force: true })
      } catch {
        // A scratch directory left behind is not worth a thrown error.
      }
    }
  }
}

/**
 * Provenance for one skill, or `undefined` when it was not installed here.
 *
 * `undefined` is the ordinary answer, not an error: a hand-written skill has
 * no marker and must keep working exactly as it did.
 */
export function readMarker(skillDir: string): Origin | undefined {
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(join(skillDir, MARKER), 'utf8'))
  } catch {
    return undefined
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined
  const record = raw as Record<string, unknown>
  const source = String(record.source ?? '').trim()
  const slug = String(record.slug ?? '').trim()
  if (!source || !slug) return undefined
  return {
    origin: String(record.origin ?? identity(source, slug)),
    source,
    slug,
    version: String(record.skill_version ?? ''),
    sha256: String(record.sha256 ?? ''),
    installedAt: String(record.installed_at ?? ''),
  }
}

function directoriesIn(root: string): string[] {
  try {
    return readdirSync(root, { withFileTypes: true })
      .filter(entry => entry.isDirectory())
      .map(entry => entry.name)
      .sort()
  } catch {
    return []
  }
}

/**
 * Every skill this plugin installed under `root`, sorted by identity.
 *
 * A walk rather than an index read: the directory is the truth, so a skill the
 * user deleted by hand is simply gone rather than a stale row nobody can
 * explain.
 */
export function listInstalled(root: string): Origin[] {
  const out: Origin[] = []
  for (const name of directoriesIn(root)) {
    const marker = readMarker(join(root, name))
    if (marker) out.push(marker)
  }
  return out.sort((a, b) => (a.origin < b.origin ? -1 : a.origin > b.origin ? 1 : 0))
}

/** The directory holding an installed skill, by identity. */
export function findInstalled(root: string, origin: string): string | undefined {
  for (const name of directoriesIn(root)) {
    const path = join(root, name)
    if (readMarker(path)?.origin === origin) return path
  }
  return undefined
}

function scratchName(path: string, kind: string): string {
  return `${path}.${kind}-${process.pid}-${Math.trunc(Number(process.hrtime.bigint() % 100000n))}`
}

/**
 * Move a finished install over whatever is at `dest`.
 *
 * An install must never leave the user worse off than before it started, so an
 * update is "build the new one, switch, then delete the old" rather than
 * "delete the old, then build". A failure at any point leaves the previous
 * version in place and working.
 *
 * `rename` alone will not do it: on POSIX renaming onto a non-empty directory
 * fails, and on Windows onto any existing one. So the old copy is moved aside
 * first, and moved back if the switch does not complete.
 *
 * @throws when the install did not happen; `dest` is untouched.
 */
export function swapIntoPlace(staging: string, dest: string): void {
  let destExists = false
  try {
    destExists = statSync(dest).isDirectory()
  } catch {
    destExists = false
  }
  if (!destExists) {
    renameSync(staging, dest)
    return
  }

  const retired = scratchName(dest, 'retiring')
  renameSync(dest, retired)
  try {
    renameSync(staging, dest)
  } catch (error) {
    // Put the working copy back before letting the failure out.
    try {
      renameSync(retired, dest)
    } catch {
      // Nothing further to try; the original error is the one that matters.
    }
    throw error
  }
  rmSync(retired, { recursive: true, force: true })
}

/**
 * Remove an installed skill by identity. `false` if it was not there.
 *
 * Moved aside and then deleted, so a half-finished delete cannot leave a
 * directory the scanner still reads as a skill.
 */
export function uninstall(root: string, origin: string): boolean {
  const found = findInstalled(root, origin)
  if (!found) return false
  const retired = scratchName(found, 'removing')
  try {
    renameSync(found, retired)
  } catch {
    return false
  }
  rmSync(retired, { recursive: true, force: true })
  return true
}
