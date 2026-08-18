/**
 * Plugin configuration, from a file on disk and the environment.
 *
 * The OpenClaw plugin reads `api.pluginConfig`, which the host hands it. A
 * WorkBuddy hook is a spawned process and gets no such thing: stdin carries
 * the turn, not the deployment. So the document lives in a file the plugin
 * owns, and the environment — set per-command in `hooks.json` — still wins,
 * because that is where a secret belongs.
 *
 * @module
 */

import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

/** Resolved settings for one plugin instance. Field names match the other hosts'. */
export interface SkillSearchConfig {
  readonly skillsDirs: string[]
  readonly hubEndpoint: string
  readonly hubApiKey: string
  readonly bundleCacheDir: string
  readonly model: string
  readonly modelBaseUrl: string
  readonly modelApiKey: string
  readonly topK: number
  readonly gatePool: number
  readonly maxSelect: number
  readonly indexBody: boolean
  readonly rewrite: boolean
  readonly gate: boolean | undefined
  readonly timeoutMs: number
  readonly availableTools: string[]
  /**
   * Fusion weights per source.
   *
   * At this topK these are not a quality dial but a seating order: within one
   * source, adjacent ranks differ by ~8% (1/(K+1) vs 1/(K+2)), while any
   * weight gap worth setting is larger, so the weight always wins. Local below
   * hub therefore means "the catalog gets the first two seats", regardless of
   * what either side scored.
   */
  readonly localWeight: number
  readonly hubWeight: number
  /**
   * Fusion's rank-damping offset. 10 rather than the engine's default 60:
   * at 60, with a topK of 3, any weight gap between sources is larger than
   * every rank gap within one, so fusion degenerates into "one source's whole
   * list, then the other's". 10 keeps the seating order while letting rank
   * still matter.
   */
  readonly rrfK: number
  /** Where the scan is cached between turns. Empty disables the cache. */
  readonly indexCachePath: string
  /** Append one JSON line per turn here. Empty disables the log. */
  readonly logPath: string
}

/** The directory WorkBuddy gives this plugin for its own state. */
export const DATA_DIR = join(
  homedir(), '.workbuddy-ai', 'plugins', 'data', 'skillsearch-memmy-marketplace',
)

export const DEFAULTS: SkillSearchConfig = {
  // Both roots WorkBuddy actually keeps skills in: what the user installed,
  // and what plugins brought with them.
  skillsDirs: ['~/.workbuddy-ai/skills', '~/.workbuddy-ai/plugins/cache'],
  hubEndpoint: '',
  hubApiKey: '',
  bundleCacheDir: '',
  model: '',
  modelBaseUrl: 'https://api.openai.com/v1',
  modelApiKey: '',
  topK: 3,
  gatePool: 10,
  maxSelect: 2,
  indexBody: false,
  // Off, unlike every other host. A rewrite is a model round-trip inside the
  // gap between the user pressing enter and the reply starting, and this host
  // has no way to show that it is working.
  rewrite: false,
  gate: undefined,
  // 8s is the shared default. Here it would be 8s of visible silence, so the
  // deadline is set to what a person will not notice losing.
  timeoutMs: 2500,
  availableTools: [],
  // Local first, catalog third. Tried the other way on 2026-08-18: the
  // catalog's top two for a poster task both depended on infrastructure this
  // machine does not have (a private ngrok MCP, a NANO_BANANA key), and the
  // model spent its whole reasoning budget on them while the local skill that
  // actually runs here sat unread in seat three. Curated-local beats
  // unvetted-remote wherever both have an answer; the catalog earns its seat
  // where local has nothing.
  localWeight: 1.0,
  hubWeight: 0.85,
  rrfK: 10,
  indexCachePath: join(DATA_DIR, 'index-cache.json'),
  logPath: join(DATA_DIR, 'skillsearch.log'),
}

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
  localWeight: 'SKILLSEARCH_LOCAL_WEIGHT',
  hubWeight: 'SKILLSEARCH_HUB_WEIGHT',
  rrfK: 'SKILLSEARCH_RRF_K',
  indexCachePath: 'SKILLSEARCH_INDEX_CACHE_PATH',
  logPath: 'SKILLSEARCH_LOG_PATH',
}

function asList(value: unknown): string[] | undefined {
  if (Array.isArray(value)) return value.map(entry => String(entry).trim()).filter(Boolean)
  if (typeof value === 'string') return value.split(',').map(entry => entry.trim()).filter(Boolean)
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
 * Read the configuration document.
 *
 * A missing file is the normal case, not an error: the defaults search the
 * two directories WorkBuddy already keeps skills in, which is a working
 * install with nothing written down.
 *
 * @param path - the document; defaults to `config.json` in the data directory.
 * @returns the parsed object, or `{}` when absent or unreadable.
 */
export function readConfigDocument(path = join(DATA_DIR, 'config.json')): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'))
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

/**
 * Resolve one configuration from the document and the environment.
 *
 * @param document - what `readConfigDocument` returned.
 * @param env - the process environment; its values win over the document.
 * @returns a complete configuration, defaults filled in.
 */
export function loadConfig(
  document: Record<string, unknown> | undefined,
  env: NodeJS.ProcessEnv = process.env,
): SkillSearchConfig {
  const source = document ?? {}
  const pick = <K extends keyof SkillSearchConfig>(key: K): unknown => {
    const variable = ENV_KEYS[key]
    const fromEnv = variable ? env[variable] : undefined
    return fromEnv !== undefined && fromEnv !== '' ? fromEnv : source[key]
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
    gate: asBoolean(pick('gate')),
    timeoutMs: asNumber(pick('timeoutMs')) ?? DEFAULTS.timeoutMs,
    availableTools: asList(pick('availableTools')) ?? DEFAULTS.availableTools,
    localWeight: asNumber(pick('localWeight')) ?? DEFAULTS.localWeight,
    hubWeight: asNumber(pick('hubWeight')) ?? DEFAULTS.hubWeight,
    rrfK: asNumber(pick('rrfK')) ?? DEFAULTS.rrfK,
    indexCachePath: asText(pick('indexCachePath')) ?? DEFAULTS.indexCachePath,
    logPath: asText(pick('logPath')) ?? DEFAULTS.logPath,
  }
}
