/**
 * Skill retrieval for OpenClaw 2.0 (2026.8.1 and newer).
 *
 * The 1.x plugin lives beside this one in `plugin-openclaw/` and is not
 * superseded by it: the two hosts want different shapes, and neither shape
 * works on the other's host. Install whichever matches the host — the
 * `openclaw.plugin.json` in each declares its own supported range.
 *
 * The default export is a plain plugin definition — `{ id, name, register }`
 * — rather than a `definePluginEntry(...)` call, so the bundle imports
 * nothing from the host at runtime and the same file loads under any 2.x
 * point release.
 *
 * @module
 */
import { register } from './register.js'

const entry = {
  id: 'skillsearch',
  name: 'Skill Search',
  description: 'Per-turn skill retrieval as a context engine: local directory, remote catalog, model-gated selection.',
  register,
}
export default entry

export type { SkillSearchConfig } from './config.js'
export { DEFAULTS, loadConfig } from './config.js'
export { buildEngine, expandHome, recentUserText, register, SkillSearchContextEngine } from './register.js'
export type * from './openclaw2-types.js'
