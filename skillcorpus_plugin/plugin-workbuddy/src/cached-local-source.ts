/**
 * `LocalSkillSource` with its scan kept on disk between turns.
 *
 * Every other host holds the engine in memory, so the scan amortises over a
 * session's turns. A WorkBuddy hook is spawned per turn and exits, so it
 * amortises over nothing: without this, each message re-reads every
 * `SKILL.md` on the machine before the model may answer.
 *
 * The cache is keyed by a fingerprint of the files themselves — path plus
 * mtime — so an edited, added or removed skill invalidates it and nothing
 * else does. Building the fingerprint still walks the directories (measured
 * at 34ms for 46 skills on the reference machine); what it saves is the
 * reading and parsing (53ms), which is the larger half and grows with the
 * corpus while the walk does not.
 *
 * @module
 */

import { mkdirSync, readFileSync, readdirSync, renameSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import {
  LocalSkillSource,
  type FileSkill,
  type SkillRoot,
} from '../../engine-typescript/src/local-source.js'

/** Mirrors the engine's own skip list, so the fingerprint tracks the scan. */
const SKIP_DIRS = new Set(['.git', '__pycache__', 'node_modules', '.venv', 'venv'])
const SKILL_FILE = 'SKILL.md'

/** What one cache file holds. `version` lets a format change miss rather than misread. */
interface CacheFile {
  readonly version: 1
  readonly fingerprint: string
  readonly skills: FileSkill[]
}

export interface CachedLocalOptions {
  readonly maxDepth?: number
  readonly indexBody?: boolean
  /** Where to keep the scan. Empty runs uncached, which is the parent's behaviour. */
  readonly cachePath: string
}

export class CachedLocalSkillSource extends LocalSkillSource {
  private readonly cachePath: string
  private readonly rootPaths: readonly string[]
  private readonly depth: number

  constructor(roots: readonly SkillRoot[], options: CachedLocalOptions) {
    super(roots, options)
    this.cachePath = options.cachePath
    this.rootPaths = roots.map(root => root.path)
    this.depth = options.maxDepth ?? 5
  }

  /**
   * The parent's scan, served from disk when nothing on disk has changed.
   * @returns every skill found, first root winning a name collision.
   */
  override async listAll(): Promise<FileSkill[]> {
    if (!this.cachePath) return super.listAll()

    const fingerprint = this.fingerprint()
    const cached = this.read()
    if (cached && cached.fingerprint === fingerprint) return cached.skills

    const skills = await super.listAll()
    this.write({ version: 1, fingerprint, skills })
    return skills
  }

  /** Path and mtime of every `SKILL.md` under the roots, in scan order. */
  fingerprint(): string {
    const parts: string[] = []
    for (const root of this.rootPaths) collect(root, this.depth, parts)
    return `${parts.length}|${hash(parts.join('\n'))}`
  }

  private read(): CacheFile | undefined {
    try {
      const parsed: unknown = JSON.parse(readFileSync(this.cachePath, 'utf8'))
      if (!parsed || typeof parsed !== 'object') return undefined
      const file = parsed as Partial<CacheFile>
      if (file.version !== 1 || typeof file.fingerprint !== 'string') return undefined
      return Array.isArray(file.skills) ? file as CacheFile : undefined
    } catch {
      return undefined
    }
  }

  private write(file: CacheFile): void {
    try {
      mkdirSync(dirname(this.cachePath), { recursive: true })
      // Write-then-rename: the main agent and a sub-agent can run hooks in
      // the same instant, and a torn write would fail to parse every turn
      // until the next invalidation. The pid keeps two writers off one temp.
      const temp = `${this.cachePath}.${process.pid}.tmp`
      writeFileSync(temp, JSON.stringify(file))
      renameSync(temp, this.cachePath)
    } catch {
      // A cache that cannot be written costs a rescan next turn, nothing more.
    }
  }
}

function collect(dir: string, depth: number, out: string[]): void {
  if (depth < 0) return
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    if (SKIP_DIRS.has(entry.name)) continue
    const path = join(dir, entry.name)
    if (entry.isDirectory()) collect(path, depth - 1, out)
    else if (entry.name === SKILL_FILE) {
      try {
        out.push(`${path}:${statSync(path).mtimeMs}`)
      } catch {
        // Deleted between readdir and stat: the next turn's walk settles it.
      }
    }
  }
}

/** djb2. Not a checksum — it only has to change when the input does. */
function hash(text: string): string {
  let value = 5381
  for (let index = 0; index < text.length; index += 1) {
    value = ((value * 33) ^ text.charCodeAt(index)) >>> 0
  }
  return value.toString(36)
}
