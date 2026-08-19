/**
 * Local skills: scan `SKILL.md` files, rank them with BM25.
 *
 * The scan is cached until invalidated, and the BM25 index is rebuilt from
 * that cache on first search after a change. Both are cheap at this scale —
 * a few hundred short documents — which is why this keeps no index on disk
 * and needs no server.
 *
 * Frontmatter parsing is deliberately not YAML: skill frontmatter in the wild
 * is flat `key: value`, and a YAML dependency would be paid by every consumer
 * of this package for a format that does not need it.
 *
 * @module
 */

import { readFile, readdir } from 'node:fs/promises'
import { basename, join } from 'node:path'
import { BM25Okapi, tokenize } from './bm25.js'
import type { RouterHit, SearchOptions, SkillSource } from './types.js'

const SKILL_FILE = 'SKILL.md'

/** Characters of body indexed per skill. Retrieval signal lives up front. */
const INDEXED_BODY_CHARS = 4000

/** Build output and version-control noise, never a skill. */
const SKIP_DIRS = new Set(['.git', '__pycache__', 'node_modules', '.venv', 'venv'])

/** One `SKILL.md` on disk. */
export interface FileSkill {
  readonly name: string
  readonly description: string
  readonly content: string
  readonly source: string
  readonly dir: string
}

/** One directory of skills, and the source label its hits carry. */
export interface SkillRoot {
  readonly path: string
  readonly name: string
}

/** A set of directories of `SKILL.md` files, ranked by BM25 over their text. */
export class LocalSkillSource implements SkillSource {
  readonly name = 'local'
  weight = 1.0

  private readonly roots: readonly SkillRoot[]
  private readonly maxDepth: number
  private readonly indexBody: boolean
  private cache: FileSkill[] | undefined
  private index: { bm25: BM25Okapi; skills: FileSkill[] } | undefined

  constructor(
    roots: readonly SkillRoot[],
    options: { maxDepth?: number; indexBody?: boolean } = {},
  ) {
    this.roots = roots
    this.maxDepth = options.maxDepth ?? 5
    this.indexBody = options.indexBody ?? false
  }

  /** Drop the scan and the index. Call when a `SKILL.md` changes on disk. */
  invalidate(): void {
    this.cache = undefined
    this.index = undefined
  }

  async search(query: string, options: SearchOptions, k: number): Promise<RouterHit[]> {
    const { bm25, skills } = await this.ensureIndex()
    if (skills.length === 0) return []
    options.signal?.throwIfAborted()

    const scores = bm25.getScores(tokenize(query))
    return skills
      .map((skill, i) => ({ score: scores[i] ?? 0, skill }))
      .filter(entry => entry.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, k)
      .map(({ score, skill }) => ({
        qualifiedId: `local/${skill.name}`,
        name: skill.name,
        content: skill.content,
        score,
        meta: {
          source: 'local',
          description: skill.description,
          // The renderer turns this into an absolute path the model can hand
          // to a file tool; without it a body saying `scripts/x.sh` resolves
          // against the agent's cwd, which is the wrong directory.
          skillDir: skill.dir,
        },
      }))
  }

  private async ensureIndex(): Promise<{ bm25: BM25Okapi; skills: FileSkill[] }> {
    if (this.index) return this.index
    const skills = await this.listAll()
    const corpus = skills.map(s => tokenize(formatSkillText(s, this.indexBody)))
    this.index = { bm25: new BM25Okapi(corpus), skills }
    return this.index
  }

  /**
   * Scan every root once and cache the result.
   * @returns every skill found, first root winning a name collision.
   */
  async listAll(): Promise<FileSkill[]> {
    if (this.cache) return this.cache
    const found: FileSkill[] = []
    const seen = new Set<string>()
    for (const root of this.roots) {
      for await (const file of walk(root.path, this.maxDepth)) {
        let text: string
        try {
          text = await readFile(file, 'utf8')
        } catch {
          continue
        }
        const { meta, body } = parseFrontmatter(text)
        const dir = file.slice(0, file.length - SKILL_FILE.length - 1)
        // The skill's own directory names it, exactly as the Python
        // implementation does — never a segment further up, which would
        // collapse every nameless skill under one grouping directory
        // into a single entry.
        const name = meta.name ?? basename(dir)
        const key = `${root.name}/${name}`
        if (seen.has(key)) continue
        seen.add(key)
        found.push({
          name,
          description: meta.description ?? '',
          content: body,
          source: root.name,
          dir,
        })
      }
    }
    this.cache = found
    return found
  }
}

/**
 * The text one skill contributes to the BM25 index.
 *
 * Byte-for-byte the Python implementation's `_format_skill_text`, because the
 * index text decides the ranking and the two implementations promise the same
 * ranking: the name twice, so a query naming a skill outweighs a description
 * mentioning the same words.
 *
 * The body is out by default. The description is the retrieval contract of
 * the `SKILL.md` format — authors are told to write what the skill is *for*
 * there — and it is also what the gate reads, so indexing it alone keeps
 * ranking and gating looking at the same text. A body is prose: mostly stop
 * words, and the single largest source of a spurious match.
 *
 * @param skill - the name, description and body to index.
 * @param indexBody - append the body, capped. For a corpus with thin
 *   descriptions; the cost of leaving it off is that a tool named only
 *   inside a body cannot be found by name.
 * @returns the line handed to the tokenizer.
 */
export function formatSkillText(
  skill: { name: string; description: string; content: string },
  indexBody = false,
): string {
  const parts = [skill.name, skill.name, skill.description]
  if (indexBody) parts.push(skill.content.slice(0, INDEXED_BODY_CHARS))
  return parts.join(' ')
}

async function* walk(root: string, maxDepth: number): AsyncGenerator<string> {
  const stack: { dir: string; depth: number }[] = [{ dir: root, depth: 0 }]
  for (let next = stack.pop(); next !== undefined; next = stack.pop()) {
    const { dir, depth } = next
    if (depth > maxDepth) continue
    let entries
    try {
      entries = await readdir(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) stack.push({ dir: join(dir, entry.name), depth: depth + 1 })
      } else if (entry.name === SKILL_FILE) {
        yield join(dir, entry.name)
      }
    }
  }
}

/**
 * Split `---` delimited frontmatter from the body.
 * @param text - the raw `SKILL.md` contents.
 * @returns the frontmatter keys and the body; empty keys when there is none.
 */
export function parseFrontmatter(text: string): {
  meta: Record<string, string>
  body: string
} {
  if (!text.startsWith('---')) return { meta: {}, body: text }
  const end = text.indexOf('\n---', 3)
  if (end === -1) return { meta: {}, body: text }
  const head = text.slice(3, end)
  const body = text.slice(end + 4).replace(/^\n+/, '')
  const meta: Record<string, string> = {}
  for (const line of head.split('\n')) {
    const colon = line.indexOf(':')
    if (colon === -1) continue
    const key = line.slice(0, colon)
    if (key.startsWith(' ') || key.startsWith('\t') || key.startsWith('#')) continue
    meta[key.trim()] = line
      .slice(colon + 1)
      .trim()
      .replace(/^["']|["']$/g, '')
  }
  return { meta, body }
}
