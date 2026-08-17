/**
 * Skill retrieval for OpenClaw.
 *
 * The default export is a plain plugin definition — `{ id, name, register }`
 * — which every OpenClaw version accepts: older ones (2026.3.x) read it
 * directly, and `definePluginEntry` in newer ones only stamps the same
 * fields. Importing that helper would have made this plugin refuse to load
 * on any host older than 2026.6.10 for no gain.
 *
 * @module
 */
import { register } from './register.js'

const entry = {
  id: 'skillsearch',
  name: 'Skill Search',
  description: 'Per-turn skill retrieval: local directory, remote catalog, model-gated selection.',
  register,
}
export default entry

export type { SkillSearchConfig } from './config.js'
export { DEFAULTS, loadConfig } from './config.js'
export { buildEngine, expandHome, recentUserText, register } from './register.js'
export type * from './openclaw-types.js'
