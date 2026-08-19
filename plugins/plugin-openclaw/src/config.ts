/**
 * Plugin configuration, from the host's config document and the environment.
 *
 * Two sources because they answer different questions: the host document is
 * where a deployment states what it wants, and the environment is where a
 * secret belongs. The environment wins, so a key in a shell never has to be
 * copied into a file to take effect.
 *
 * Every value has a default that does something sensible, and capability is
 * expressed by presence rather than by flags: no `hubEndpoint` means no
 * remote source, no `model` means no rewriter and no gate.
 *
 * @module
 */

/** Resolved settings for one plugin instance. */
export interface SkillSearchConfig {
  /** Directories scanned for `SKILL.md`. */
  readonly skillsDirs: string[]
  /** Remote catalog base URL. Empty disables the remote source. */
  readonly hubEndpoint: string
  readonly hubApiKey: string
  /** Where extracted bundles live. Outside every scanned skills directory. */
  readonly bundleCacheDir: string
  /** Route for the rewriter and the gate. Empty runs retrieval unfiltered. */
  readonly model: string
  readonly modelBaseUrl: string
  readonly modelApiKey: string
  /** Upper bound on skills injected per turn. */
  readonly topK: number
  /** Candidates the gate judges. */
  readonly gatePool: number
  /** Upper bound on what the gate keeps. */
  readonly maxSelect: number
  /** Deadline for one retrieval, in milliseconds. */
  /** Index skill bodies alongside name and description. */
  readonly indexBody: boolean
  /** Clean the query before searching. Needs a `model`. */
  readonly rewrite: boolean
  /**
   * Drop candidates with a model before they reach the prompt. Undefined
   * means "on when a catalog is configured". Needs a `model`.
   */
  readonly gate: boolean | undefined
  readonly timeoutMs: number
  /**
   * Tool names this agent can call. The host does not report them to a hook,
   * so the gate's environment check runs only when a deployment states them
   * here. Empty leaves the gate judging relevance alone.
   */
  readonly availableTools: string[]
}

export const DEFAULTS: SkillSearchConfig = {
  skillsDirs: ['~/.openclaw/skills'],
  hubEndpoint: '',
  hubApiKey: '',
  bundleCacheDir: '',
  model: '',
  modelBaseUrl: 'https://api.openai.com/v1',
  modelApiKey: '',
  topK: 5,
  gatePool: 10,
  maxSelect: 2,
  indexBody: false,
  rewrite: true,
  gate: undefined,
  timeoutMs: 8000,
  availableTools: [],
}

/** Environment variable for each field a deployment may prefer to keep out of a file. */
const ENV_KEYS: Partial<Record<keyof SkillSearchConfig, string>> = {
  skillsDirs: 'SKILLSEARCH_SKILLS_DIRS',
  hubEndpoint: 'SKILLSEARCH_HUB_ENDPOINT',
  hubApiKey: 'SKILLSEARCH_HUB_API_KEY',
  bundleCacheDir: 'SKILLSEARCH_BUNDLE_CACHE_DIR',
  model: 'SKILLSEARCH_MODEL',
  modelBaseUrl: 'SKILLSEARCH_MODEL_BASE_URL',
  modelApiKey: 'SKILLSEARCH_MODEL_API_KEY',
  topK: 'SKILLSEARCH_TOP_K',
  gatePool: 'SKILLSEARCH_GATE_POOL',
  maxSelect: 'SKILLSEARCH_MAX_SELECT',
  indexBody: 'SKILLSEARCH_INDEX_BODY',
  rewrite: 'SKILLSEARCH_REWRITE',
  gate: 'SKILLSEARCH_GATE',
  timeoutMs: 'SKILLSEARCH_TIMEOUT_MS',
  availableTools: 'SKILLSEARCH_AVAILABLE_TOOLS',
}

/**
 * Read a list from an array or a comma-separated string.
 *
 * An empty array is a value, not an absence: a deployment writing
 * `skillsDirs: []` is turning the local source off, and treating that as
 * unset would silently hand it the default directory instead.
 */
function asList(value: unknown): string[] | undefined {
  if (Array.isArray(value)) {
    return value.map(entry => String(entry).trim()).filter(Boolean)
  }
  if (typeof value === 'string') {
    return value.split(',').map(entry => entry.trim()).filter(Boolean)
  }
  return undefined
}

function asNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

function asBoolean(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value
  // An environment variable is always a string, so the words have to be
  // read: `SKILLSEARCH_INDEX_BODY=false` must not arrive as truthy.
  if (typeof value === 'string') {
    const text = value.trim().toLowerCase()
    if (['true', '1', 'yes', 'on'].includes(text)) return true
    if (['false', '0', 'no', 'off'].includes(text)) return false
  }
  return undefined
}

function asText(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const trimmed = value.trim()
  return trimmed ? trimmed : undefined
}

/**
 * Resolve one configuration from the host document and the environment.
 *
 * @param pluginConfig - what the host read from `plugins.entries.<id>.config`.
 * @param env - the process environment; its values win over the document.
 * @returns a complete configuration, defaults filled in.
 */
export function loadConfig(
  pluginConfig: Record<string, unknown> | undefined,
  env: NodeJS.ProcessEnv = process.env,
): SkillSearchConfig {
  const document = pluginConfig ?? {}
  const pick = <K extends keyof SkillSearchConfig>(key: K): unknown => {
    const variable = ENV_KEYS[key]
    const fromEnv = variable ? env[variable] : undefined
    return fromEnv !== undefined && fromEnv !== '' ? fromEnv : document[key]
  }

  return {
    skillsDirs: asList(pick('skillsDirs')) ?? DEFAULTS.skillsDirs,
    hubEndpoint: asText(pick('hubEndpoint')) ?? DEFAULTS.hubEndpoint,
    hubApiKey: asText(pick('hubApiKey')) ?? DEFAULTS.hubApiKey,
    bundleCacheDir: asText(pick('bundleCacheDir')) ?? DEFAULTS.bundleCacheDir,
    model: asText(pick('model')) ?? DEFAULTS.model,
    modelBaseUrl: asText(pick('modelBaseUrl')) ?? DEFAULTS.modelBaseUrl,
    modelApiKey: asText(pick('modelApiKey')) ?? DEFAULTS.modelApiKey,
    topK: asNumber(pick('topK')) ?? DEFAULTS.topK,
    gatePool: asNumber(pick('gatePool')) ?? DEFAULTS.gatePool,
    maxSelect: asNumber(pick('maxSelect')) ?? DEFAULTS.maxSelect,
    indexBody: asBoolean(pick('indexBody')) ?? DEFAULTS.indexBody,
    rewrite: asBoolean(pick('rewrite')) ?? DEFAULTS.rewrite,
    // Stays undefined when unset — `false` and "not configured" mean
    // different things here.
    gate: asBoolean(pick('gate')),
    timeoutMs: asNumber(pick('timeoutMs')) ?? DEFAULTS.timeoutMs,
    availableTools: asList(pick('availableTools')) ?? DEFAULTS.availableTools,
  }
}
