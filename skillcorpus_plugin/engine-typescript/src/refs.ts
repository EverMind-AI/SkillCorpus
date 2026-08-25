/**
 * Skill-body ref resolution: `{baseDir}` placeholders and markdown links to
 * bundled files become absolute paths rooted at the skill's directory.
 *
 * A skill body routinely says `run scripts/x.py` or links `references/y.md`.
 * Those paths mean something only relative to the skill's own directory; the
 * model resolves them against its cwd, which is the wrong place. Rewriting
 * them is also what keeps such skills alive through the gate — its prompt
 * treats a literal `{baseDir}` as proof a skill cannot run here, which is
 * true only when nothing resolves it.
 *
 * Every substitution is existence-checked: a ref whose target is not on disk
 * stays literal rather than becoming a confident 404. Code fences are left
 * whole so example markup is not silently rewritten.
 *
 * A port of the Python implementation's `refs.py`, same regexes, same rules.
 *
 * @module
 */

import { existsSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'

const BUNDLED_DIRS = ['references', 'scripts', 'assets', 'examples']

const MD_LINK_RE = new RegExp(
  String.raw`\[([^\]]+)\]\((?:\.{0,2}/)?((?:${BUNDLED_DIRS.join('|')})/[^)\s]+)\)`,
  'g',
)
const BASE_DIR_REF_RE = /\{baseDir\}\/(\S+?)(?=[\s)'"`]|$)/g
const BARE_BASE_DIR_RE = /\{baseDir\}(?!\/)/g
const CODE_FENCE_RE = /(```[\s\S]*?```)/

/** What `resolveRefs` did to a body. */
export interface ResolvedBody {
  /** The body, with every resolvable ref rewritten to an absolute path. */
  readonly body: string
  /** Whether at least one substitution matched a real file on disk. */
  readonly anyResolved: boolean
}

/**
 * Rewrite `{baseDir}` and bundled-file links in `body` to absolute paths.
 *
 * @param body - the skill body, frontmatter already stripped.
 * @param skillDir - the directory holding the skill's `SKILL.md`. When
 *   missing or not a real directory, `{baseDir}/` is stripped to bare
 *   relative paths — not resolvable, but no nonsense literal in the prompt.
 * @returns the rewritten body, and whether anything actually resolved.
 */
export function resolveRefs(body: string, skillDir: string | undefined): ResolvedBody {
  if (!body) return { body: '', anyResolved: false }

  const hasDir = !!skillDir && isDirectory(skillDir)
  if (!hasDir) {
    const stripped = body.includes('{baseDir}')
      ? body.replaceAll('{baseDir}/', '').replaceAll('{baseDir}', '')
      : body
    return { body: stripped, anyResolved: false }
  }

  const baseDir = skillDir
  let anyResolved = false

  const mdSub = (match: string, label: string, rel: string): string => {
    const trimmed = rel.replace(/[.,;:]+$/, '')
    const cut = firstIndexOfAny(trimmed, ['#', '?'])
    const fragment = cut === -1 ? '' : trimmed.slice(cut)
    const relFile = cut === -1 ? trimmed : trimmed.slice(0, cut)
    if (relFile && existsSync(join(baseDir, relFile))) {
      anyResolved = true
      return `[${label}](${baseDir}/${relFile}${fragment})`
    }
    return match
  }

  const segments = body.split(CODE_FENCE_RE)
  let out = segments
    .map(segment => (segment.startsWith('```') ? segment : segment.replace(MD_LINK_RE, mdSub)))
    .join('')

  if (out.includes('{baseDir}')) {
    out = out.replace(BASE_DIR_REF_RE, (match, ref: string) => {
      const trimmed = ref.replace(/[.,;:]+$/, '')
      if (trimmed && existsSync(join(baseDir, trimmed))) {
        anyResolved = true
        return `${baseDir}/${ref}`
      }
      return match
    })
    // A function replacement: `baseDir` is a filesystem path, and a string
    // replacement would read any `$` in it as a pattern reference.
    out = out.replace(BARE_BASE_DIR_RE, () => {
      anyResolved = true
      return baseDir
    })
  }

  return { body: out, anyResolved }
}

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory()
  } catch {
    return false
  }
}

function firstIndexOfAny(text: string, needles: readonly string[]): number {
  const found = needles.map(n => text.indexOf(n)).filter(i => i !== -1)
  return found.length === 0 ? -1 : Math.min(...found)
}

const PLACEHOLDER_RE = /\{\{([A-Z_]+)(?::([A-Za-z0-9._-]+))?\}\}/g

/** Per-agent facts that fill the PathGuard placeholders. */
export interface PlaceholderRuntime {
  /** The agent's own config/state root, for `{{AGENT_STATE_DIR}}`. */
  readonly stateDir?: string
  /** The agent's home, for `{{HOME}}`. */
  readonly homeDir?: string
  /** The agent's writable output directory, for `{{OUTPUT_DIR}}`. */
  readonly outputDir?: string
}

/**
 * Replace PathGuard placeholders in a skill body with real paths.
 *
 * `{{SKILL_DIR}}` / `{{SKILL_DIR:<name>}}` / `{{AGENT_STATE_DIR}}` /
 * `{{HOME}}` / `{{OUTPUT_DIR}}` are written by an offline pass over a shared
 * skill library (see `skill_retire_and_classify/pathguard`) that rewrites
 * hardcoded paths to agent-agnostic placeholders. This fills them per agent,
 * next to {@link resolveRefs}.
 *
 * - `{{SKILL_DIR}}` → this skill's bundle directory; `{{SKILL_DIR:<n>}}` → a
 *   sibling bundle under the same parent. `<n>` is restricted to a safe slug
 *   (`[A-Za-z0-9._-]+`), so `../../x` or an absolute path does not match and
 *   stays literal.
 * - `{{AGENT_STATE_DIR}}` → `stateDir`, else `outputDir`.
 * - `{{HOME}}` → `homeDir`, else `outputDir`.
 * - `{{OUTPUT_DIR}}` → `outputDir`.
 *
 * Substitution is unconditional — unlike `resolveRefs` it never checks the
 * filesystem, because a placeholder already carries its target.
 */
export function resolvePlaceholders(
  body: string,
  skillDir: string | undefined,
  runtime: PlaceholderRuntime = {},
): string {
  if (!body || !body.includes('{{')) return body

  const sd = skillDir || undefined

  // Bare {{SKILL_DIR}} and {{SKILL_DIR}}/… — the slash is folded in so the
  // directory is written once. Without a directory on disk the placeholder is
  // left literal, not stripped: a bare `scripts/x.py` would tell the model the
  // bundle is present when it never downloaded.
  if (sd) {
    body = body.replaceAll('{{SKILL_DIR}}/', `${sd.replace(/\/+$/, '')}/`)
    body = body.replaceAll('{{SKILL_DIR}}', sd)
  }

  return body.replace(PLACEHOLDER_RE, (match, name: string, arg?: string) => {
    if (name === 'SKILL_DIR') {
      return sd && arg && arg !== '.' && arg !== '..' ? join(dirname(sd), arg) : match
    }
    if (name === 'AGENT_STATE_DIR') {
      return runtime.stateDir || runtime.outputDir || match
    }
    if (name === 'HOME') {
      return runtime.homeDir || runtime.outputDir || match
    }
    if (name === 'OUTPUT_DIR') {
      return runtime.outputDir || match
    }
    return match
  })
}
