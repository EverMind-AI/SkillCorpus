/**
 * Skill retrieval for OpenClaw.
 *
 * Every turn, this searches a local skills directory and an optional remote
 * catalog against what the user just wrote, narrows the result with a model,
 * and returns the matching skill bodies through `before_prompt_build` for the
 * host to prepend. The wiring lives in `./register.js`, which imports no
 * runtime value from the host and is therefore unit-testable.
 *
 * @module
 */
import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry'

import type { DefinedPluginEntry } from './openclaw-types.js'
import { register } from './register.js'

const entry: DefinedPluginEntry = definePluginEntry({
  id: 'skillsearch',
  name: 'Skill Search',
  description: 'Per-turn skill retrieval: local directory, remote catalog, model-gated selection.',
  register,
})
export default entry

export type { SkillSearchConfig } from './config.js'
export { DEFAULTS, loadConfig } from './config.js'
export { buildEngine, expandHome, recentUserText, register } from './register.js'
export type * from './openclaw-types.js'
