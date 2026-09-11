/**
 * Notice when the shared skills directory changed, without a restart.
 *
 * The counterpart of `engine-python/skillsearch/watch.py`. The local scan is
 * built once and kept for the life of the engine, and `invalidate()` — the
 * supported way to drop it — is called by no adapter. So a skill added today
 * is invisible until the agent restarts, which is fatal for a *shared*
 * library whose whole premise is that installing in one agent shows up in the
 * others.
 *
 * ## Only the shared directory
 *
 * A host now scans five or more directories rather than one, and walking all
 * of them every turn would put the cost on deployments not using this. The
 * shared directory is ours, its size is something we control, and it is the
 * only one that changes behind the host's back. Everyone else's directories
 * keep their host's existing behaviour.
 *
 * ## Why a fingerprint rather than a revision file
 *
 * A counter the plugin bumps when *it* installs something is one `stat`, but
 * it cannot see a user dragging a directory in by hand — an acceptance case.
 * So the fingerprint is the mechanism; a revision file could only be a fast
 * path in front of it.
 *
 * The walk is not pure overhead. Measured on WorkBuddy, whose per-turn hook
 * has fingerprinted since 0.2.0: 34ms for 46 skills against 53ms to read and
 * parse them. The walk is flat in corpus size and the parse it avoids is not.
 *
 * @module
 */

import { readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Directory names never worth descending into. Mirrors the scanner's own skip
 * list — a fingerprint tracking different files from the scan would either
 * miss changes or invent them.
 */
const SKIP_DIRS = new Set(['.git', 'node_modules', '__pycache__', '.venv', 'venv', '.tox'])

const SKILL_FILE = 'SKILL.md'

/**
 * Path and mtime of every `SKILL.md` under `dirs`, in scan order.
 *
 * Sorted per level so two walks of an unchanged tree agree: a filesystem may
 * return `readdir` entries in any order, and an unsorted walk would
 * invalidate the cache at random.
 *
 * Never throws — a directory that vanishes mid-walk contributes nothing,
 * which is also the right answer.
 */
export function fingerprint(dirs: readonly string[], maxDepth = 5): string {
  const parts: string[] = []
  for (const root of dirs) collect(root, maxDepth, parts)
  return parts.join('\n')
}

function collect(dir: string, depth: number, out: string[]): void {
  if (depth < 0) return
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of [...entries].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))) {
    if (SKIP_DIRS.has(entry.name)) continue
    const path = join(dir, entry.name)
    if (entry.isDirectory()) {
      collect(path, depth - 1, out)
    } else if (entry.name === SKILL_FILE) {
      try {
        // Nanoseconds, not `mtimeMs`. Millisecond resolution misses an edit
        // made inside the same millisecond as the previous walk — rare, but
        // the failure is silent and looks like "my change did nothing".
        out.push(`${path}:${statSync(path, { bigint: true }).mtimeNs}`)
      } catch {
        // Deleted between readdir and stat: the next turn's walk settles it.
      }
    }
  }
}

/**
 * Answers "did these directories change since I last asked?".
 *
 * Stateful on purpose: the first call establishes the baseline and reports no
 * change, so constructing a watch does not throw away a scan just built.
 */
export class DirectoryWatch {
  private seen: string | undefined

  constructor(private readonly dirs: readonly string[], private readonly maxDepth = 5) {}

  /** Whether there is anything to watch. Cheap enough to call per turn. */
  get active(): boolean {
    return this.dirs.length > 0
  }

  /** Whether the tree differs from the last call. Never throws. */
  changed(): boolean {
    if (this.dirs.length === 0) return false
    let current: string
    try {
      current = fingerprint(this.dirs, this.maxDepth)
    } catch {
      return false
    }
    if (this.seen === undefined) {
      this.seen = current
      return false
    }
    if (current === this.seen) return false
    this.seen = current
    return true
  }
}
