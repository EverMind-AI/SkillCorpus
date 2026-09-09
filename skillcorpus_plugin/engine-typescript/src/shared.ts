/**
 * The one directory all five hosts agree on, and the registry inside it.
 *
 * A byte-for-byte counterpart of `engine-python/skillsearch/shared.py`. The two
 * write the same file and read each other's writes — a Python host and a
 * TypeScript host on one machine share this registry — so the semantics are
 * not merely similar, they have to match. `tests/parity.test.ts` pins the
 * cases where a difference would be invisible until it split five agents
 * apart on a user's machine.
 *
 * ## Why the root is a hardcoded expression
 *
 * `homedir()/.evermind-skillsearch`, identical on all three platforms, with no
 * per-platform branch. On Windows that is `C:\Users\x\.evermind-skillsearch`,
 * which is not the Windows convention — `%LOCALAPPDATA%` is. Consistency is
 * chosen over convention deliberately:
 *
 * - this is the *one* path all five hosts must compute identically, and the
 *   whole feature is premised on them landing in the same place;
 * - a platform branch is somewhere for them to diverge. An agent started as a
 *   service or a scheduled task has a different environment from a desktop
 *   session, so `%LOCALAPPDATA%` can resolve elsewhere and the five split
 *   apart with nothing logged;
 * - it is the plugin's own directory, not a system integration point, and the
 *   user has to open it to drop skills in.
 *
 * `SKILLSEARCH_HOME` overrides it, but only as an advanced escape hatch: a
 * GUI-launched agent never reads a shell profile, so the default can never
 * depend on it.
 *
 * ## Why hosts self-register
 *
 * Hardcoding the five defaults is wrong three ways at once — host versions
 * change the default, users move it, and the Windows location is not knowable
 * from here. Each host instead writes the absolute path it actually resolved
 * at runtime and reads the whole table back. What it then sees is exactly "the
 * directories of the other hosts that also have this plugin", which is the
 * right set: a host without the plugin has nothing to share.
 *
 * ## Everything here fails open
 *
 * A registry that cannot be read or written costs this machine the sharing
 * feature and nothing else. It must never cost a turn — WorkBuddy's hook calls
 * this once per turn inside an 8-second budget, and a hook that throws blocks
 * the user's message.
 *
 * @module
 */

import { mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { isAbsolute, join, resolve } from 'node:path'

/** Overrides the root. Advanced use only — see the module docs. */
export const HOME_ENV = 'SKILLSEARCH_HOME'

const NOTE =
  'To exclude a directory, set its `enabled` to false. Deleting the line does '
  + 'not work — that agent re-registers it on its next start.'

/** One host's registration. `enabled` belongs to the user, not to the host. */
export interface HostEntry {
  readonly id: string
  readonly dir: string
  readonly enabled: boolean
}

/** Expand a leading `~` against the user's home, leaving other paths alone. */
function expandHome(path: string, home: string = homedir()): string {
  if (path === '~') return home
  if (path.startsWith('~/')) return join(home, path.slice(2))
  return path
}

/**
 * The shared root, honouring `SKILLSEARCH_HOME`.
 *
 * Never throws and never creates anything: a caller that only reads should not
 * have to mkdir, and the ones that write say so.
 */
export function sharedRoot(env: NodeJS.ProcessEnv = process.env): string {
  const override = (env[HOME_ENV] ?? '').trim()
  if (override) return expandHome(override)
  return join(homedir(), '.evermind-skillsearch')
}

/** Where skills installed through the plugin land. */
export function sharedSkillsDir(env: NodeJS.ProcessEnv = process.env): string {
  return join(sharedRoot(env), 'skills')
}

/** The registry of host skills directories. */
export function registryPath(env: NodeJS.ProcessEnv = process.env): string {
  return join(sharedRoot(env), 'registry.json')
}

/** Settings all five hosts read, so one edit applies everywhere. */
export function sharedConfigPath(env: NodeJS.ProcessEnv = process.env): string {
  return join(sharedRoot(env), 'config.json')
}

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory()
  } catch {
    return false
  }
}

/**
 * Every registered host, or an empty list if the file is unusable.
 *
 * Missing, truncated, hand-corrupted and half-written all answer the same way.
 * The alternative — throwing — turns one broken JSON file into a broken agent
 * on five hosts at once.
 */
export function readRegistry(path?: string, env: NodeJS.ProcessEnv = process.env): HostEntry[] {
  const target = path ?? registryPath(env)
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(target, 'utf8'))
  } catch {
    return []
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  const hosts = (raw as { hosts?: unknown }).hosts
  if (!Array.isArray(hosts)) return []

  const entries: HostEntry[] = []
  const seen = new Set<string>()
  for (const item of hosts) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue
    const record = item as { id?: unknown; dir?: unknown; enabled?: unknown }
    const id = String(record.id ?? '').trim()
    const dir = String(record.dir ?? '').trim()
    if (!id || !dir || seen.has(id)) continue
    seen.add(id)
    entries.push({ id, dir, enabled: record.enabled == null ? true : Boolean(record.enabled) })
  }
  return entries
}

/**
 * Replace the registry atomically. `false` if anything went wrong.
 *
 * Temp file beside the target, then `rename` — the same shape the bundle
 * installer uses, and for the same reason: five processes can reach this
 * concurrently and a reader must never see a half-written file. Losing the
 * race is fine; the loser's next start writes again.
 */
function writeRegistry(entries: HostEntry[], path: string): boolean {
  const payload = {
    _note: NOTE,
    hosts: entries.map(entry => ({ id: entry.id, dir: entry.dir, enabled: entry.enabled })),
  }
  let staging: string | undefined
  try {
    const parent = path.slice(0, Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')))
    mkdirSync(parent, { recursive: true })
    staging = mkdtempSync(join(parent, '.registry-'))
    const scratch = join(staging, 'registry.json')
    writeFileSync(scratch, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
    renameSync(scratch, path)
    return true
  } catch {
    return false
  } finally {
    if (staging) {
      try {
        rmSync(staging, { recursive: true, force: true })
      } catch {
        // Leaving a scratch directory behind is not worth a thrown error.
      }
    }
  }
}

/**
 * Record this host's skills directory and return the whole table.
 *
 * Cheap and idempotent by design: the common case is one small read and no
 * write. WorkBuddy's hook calls this once per turn on the turn's hot path, so
 * writing every turn would be a real cost — and five agents rewriting one file
 * all day is a race nobody needs.
 *
 * `enabled` is never written back for an entry that already exists. That is
 * what makes the file editable: a user who sets `enabled: false` must not have
 * it undone by the next start of the host it belongs to.
 *
 * @param hostId - stable per host, e.g. `"openclaw2"`. One entry per id.
 * @param skillsDir - resolved to an absolute path; a relative path or a `~`
 *   would each have to be re-resolved by a reader on another platform.
 * @returns every entry, this host's included. Empty on any failure.
 */
export function registerHost(
  hostId: string,
  skillsDir: string | undefined,
  path?: string,
  env: NodeJS.ProcessEnv = process.env,
): HostEntry[] {
  const target = path ?? registryPath(env)
  const entries = readRegistry(target, env)
  const id = String(hostId ?? '').trim()
  if (!id || !skillsDir) return entries

  let wanted: string
  try {
    wanted = resolve(expandHome(skillsDir))
  } catch {
    return entries
  }
  if (!isAbsolute(wanted)) return entries

  const index = entries.findIndex(entry => entry.id === id)
  const existing = index >= 0 ? entries[index] : undefined
  if (existing) {
    // The short circuit the per-turn caller depends on: one read, no write.
    if (existing.dir === wanted) return entries
    // The path moved. Update it and leave the user's `enabled` alone.
    entries[index] = { id, dir: wanted, enabled: existing.enabled }
  } else {
    entries.push({ id, dir: wanted, enabled: true })
  }

  writeRegistry(entries, target)
  return entries
}

/**
 * Directories to scan, as `[path, name]` pairs.
 *
 * Filtered three ways, all deliberate: entries the user disabled are dropped;
 * entries whose directory no longer exists are dropped, because an uninstalled
 * agent leaves its line behind; and this host's own directory is dropped
 * unless asked for, since the caller already scans it and a second copy would
 * compete with itself in one ranking.
 */
export function registeredDirs(
  hostId = '',
  path?: string,
  options: { includeSelf?: boolean } = {},
  env: NodeJS.ProcessEnv = process.env,
): Array<[string, string]> {
  const out: Array<[string, string]> = []
  for (const entry of readRegistry(path, env)) {
    if (!entry.enabled) continue
    if (entry.id === hostId && !options.includeSelf) continue
    if (!isDirectory(entry.dir)) continue
    out.push([entry.dir, entry.id])
  }
  return out
}

/**
 * Register, then answer with everything worth scanning beyond our own.
 *
 * The one call a host adapter needs. The shared skills directory comes first
 * and is listed whenever it exists — it is where this plugin installs things,
 * independent of whether any other host has registered.
 *
 * Never throws.
 */
export function sharedDirs(
  hostId: string,
  skillsDir?: string,
  path?: string,
  env: NodeJS.ProcessEnv = process.env,
): Array<[string, string]> {
  try {
    registerHost(hostId, skillsDir, path, env)
    const dirs: Array<[string, string]> = []
    const shared = sharedSkillsDir(env)
    if (isDirectory(shared)) dirs.push([shared, 'shared'])

    let own: string | undefined
    if (skillsDir) {
      try {
        own = resolve(expandHome(skillsDir))
      } catch {
        own = undefined
      }
    }
    for (const [dir, name] of registeredDirs(hostId, path, {}, env)) {
      // Two hosts pointed at one directory is a real configuration — OpenClaw
      // 1 and 2 share `~/.openclaw/skills` — and scanning it twice would
      // double every skill in it.
      if (dir === own || dirs.some(([seen]) => seen === dir)) continue
      dirs.push([dir, name])
    }
    return dirs
  } catch {
    return []
  }
}

/**
 * Whether a host's config asked to join the shared library.
 *
 * Its own switch, separate from the registry's `enabled`, because the two
 * answer different questions and users conflate them: this one is "do I read
 * the others", the registry's is "do the others read me".
 *
 * Strings are accepted because a config file or an environment variable
 * delivers one.
 */
export function optedIn(value: unknown, fallback = true): boolean {
  if (value == null) return fallback
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  const text = String(value).trim().toLowerCase()
  if (['false', '0', 'no', 'off'].includes(text)) return false
  if (['true', '1', 'yes', 'on'].includes(text)) return true
  return fallback
}

/**
 * Every directory this host should scan, its own first.
 *
 * The one call a host's engine builder needs: registers the host's main
 * directory so the others can find it, then appends the shared skills
 * directory and the other hosts' directories. Deduplicated by path, because
 * the same directory listed twice doubles every skill in it in one ranking —
 * and OpenClaw 1 and 2 both defaulting to `~/.openclaw/skills` makes that a
 * real configuration rather than a hypothetical.
 *
 * Never throws; the worst case is the host's own directories, unchanged.
 *
 * @param hostId - stable per host, e.g. `"workbuddy"`.
 * @param ownDirs - already expanded and absolute. The first is registered as
 *   this host's directory; the rest are the deployment's extra roots.
 * @param share - `false` keeps this host out of the shared library entirely.
 */
export function scanDirs(
  hostId: string,
  ownDirs: readonly string[],
  share = true,
  path?: string,
  env: NodeJS.ProcessEnv = process.env,
): Array<{ path: string; name: string }> {
  const out: Array<{ path: string; name: string }> = []
  const seen = new Set<string>()
  const add = (dir: string, name: string): void => {
    if (!dir || seen.has(dir)) return
    seen.add(dir)
    out.push({ path: dir, name })
  }

  for (const dir of ownDirs) add(dir, 'local')
  if (!share) return out

  try {
    for (const [dir, name] of sharedDirs(hostId, ownDirs[0], path, env)) add(dir, name)
  } catch {
    // Sharing is never worth a failed turn.
  }
  return out
}
