/**
 * Engine wiring for WorkBuddy, and the one call a turn makes.
 *
 * Mirrors `plugin-openclaw/src/register.ts`, minus the host API: there is no
 * plugin object to register and no event to subscribe to, only a process that
 * answers one question and exits.
 *
 * @module
 */

import { homedir } from 'node:os'
import { join } from 'node:path'
import { SkillSearchEngine, type SourceDiagnostic } from '../../engine-typescript/src/engine.js'
import { LLMGateFilter } from '../../engine-typescript/src/gate.js'
import { HubSkillSource, SkillHubClient } from '../../engine-typescript/src/hub-source.js'
import { MarketplaceClient, MarketplaceSkillSource } from '../../engine-typescript/src/marketplace-source.js'
import type { SkillSource } from '../../engine-typescript/src/types.js'
import { QueryRewriter } from '../../engine-typescript/src/rewriter.js'
import { CachedLocalSkillSource } from './cached-local-source.js'
import type { SkillSearchConfig } from './config.js'
import { createChatModel } from './model.js'

/** Expand a leading `~` against the user's home, leaving other paths alone. */
export function expandHome(path: string, home: string = homedir()): string {
  if (path === '~') return home
  if (path.startsWith('~/')) return join(home, path.slice(2))
  return path
}

/**
 * Build the retrieval engine for one configuration.
 *
 * @param config - the resolved plugin configuration.
 * @returns the engine, which reports `enabled: false` when nothing is
 *   configured to search.
 */
export function buildEngine(
  config: SkillSearchConfig,
  onDiagnostic?: (diagnostic: SourceDiagnostic) => void,
  workspaceDir?: string,
): SkillSearchEngine {
  const sources: SkillSource[] = []

  const dirs = config.skillsDirs.map(dir => expandHome(dir)).filter(Boolean)
  if (dirs.length > 0) {
    const local = new CachedLocalSkillSource(
      dirs.map(path => ({ path, name: 'local' })),
      { indexBody: config.indexBody, cachePath: expandHome(config.indexCachePath) },
    )
    // Set here rather than upstream: preferring the catalog is this host's
    // trade-off, and the engine's defaults serve four others.
    local.weight = config.localWeight
    sources.push(local)
  }

  let client: SkillHubClient | undefined
  if (config.hubEndpoint) {
    client = new SkillHubClient(config.hubEndpoint, {
      ...(config.hubApiKey ? { apiKey: config.hubApiKey } : {}),
      // Outside every scanned directory. `~/.workbuddy-ai/plugins/cache` is
      // one of the defaults, so a bundle extracted under it would come back
      // as a local skill on the next scan.
      cacheDir: expandHome(config.bundleCacheDir)
        || join(homedir(), '.workbuddy-ai', 'skillsearch-bundles'),
    })
    const hub = new HubSkillSource(client)
    hub.weight = config.hubWeight
    sources.push(hub)
  }

  const marketplaceClients = new Map<string, MarketplaceClient>()
  for (const [kind, endpoint] of [
    ['clawhub', config.clawhubEndpoint],
    ['skillhub_cn', config.skillhubCnEndpoint],
  ] as const) {
    if (!endpoint) continue
    const marketplace = new MarketplaceClient(kind, endpoint, {
      cacheDir: expandHome(config.bundleCacheDir)
        || join(homedir(), '.workbuddy-ai', 'skillsearch-bundles'),
      // ClawHub measured 4–5s on the supported route. Give search headroom,
      // but leave time under the hook's global deadline for body hydration.
      timeoutMs: Math.max(1, Math.min(config.timeoutMs, 6500)),
      downloadTimeoutMs: Math.max(1, config.timeoutMs),
    })
    marketplaceClients.set(kind, marketplace)
    sources.push(new MarketplaceSkillSource(marketplace))
  }

  const model = createChatModel({
    baseUrl: config.modelBaseUrl,
    apiKey: config.modelApiKey,
    model: config.model,
  })

  return new SkillSearchEngine(
    {
      sources,
      ...(onDiagnostic ? { onDiagnostic } : {}),
      ...(model && config.rewrite ? { rewriter: new QueryRewriter(model) } : {}),
      ...(model && (config.gate ?? (Boolean(config.hubEndpoint) || marketplaceClients.size > 0))
        ? { gate: new LLMGateFilter(model, { maxSelect: config.maxSelect }) }
        : {}),
      ...((client || marketplaceClients.size > 0)
        ? {
          fetchBody: async (hit, signal) => {
            const marketplace = marketplaceClients.get(String(hit.meta.source))
            if (marketplace) {
              const installed = await marketplace.install(hit, signal)
              return { body: installed.body, record: { _installed: installed } }
            }
            if (hit.meta.source !== 'hub' || !client) return undefined
            const record = await client.get(String(hit.meta.id), signal)
            // The record rides along so `materialise` can skip re-fetching
            // the same detail — one request per selected skill otherwise.
            return {
              ...(typeof record.skill_md === 'string' ? { body: record.skill_md } : {}),
              record,
            }
          },
          materialise: async (hit, signal) => {
            const marketplace = marketplaceClients.get(String(hit.meta.source))
            if (marketplace) {
              const fetched = hit.meta._fetched as { _installed?: { dir: string; body: string } } | undefined
              const installed = fetched?._installed ?? await marketplace.install(hit, signal)
              return { dir: installed.dir, body: installed.body }
            }
            if (hit.meta.source !== 'hub' || !client) return undefined
            const fetched = hit.meta._fetched as Record<string, unknown> | undefined
            const installed = await client.install(String(hit.meta.id), fetched, signal)
            return { dir: installed.dir, body: installed.skillMd }
          },
        }
        : {}),
    },
    {
      topK: config.topK,
      gatePool: config.gatePool,
      rrfK: config.rrfK,
      // PathGuard placeholders' per-agent facts. WorkBuddy's own config root
      // is ~/.workbuddy-ai; the agent's writable output is its workspace,
      // falling back to the hook process's cwd when the payload reports none.
      outputDir: workspaceDir || process.cwd(),
      homeDir: homedir(),
      stateDir: join(homedir(), '.workbuddy-ai'),
      resolvePlaceholders: config.resolvePlaceholders,
    },
  )
}

/**
 * Retrieve for one turn, under this configuration's deadline.
 *
 * Never throws and never rejects: a hook that fails takes the whole turn with
 * it (the host raises `HookBlockedError` and the user's message never reaches
 * the model), so every failure here has to become an empty block instead.
 *
 * @param query - the user's message for this turn.
 * @param config - the resolved plugin configuration.
 * @param deps - test seam for the engine builder.
 * @returns the text to inject, or `''` when this turn gets no skills.
 */
export async function retrieveForTurn(
  query: string,
  config: SkillSearchConfig,
  deps: { buildEngineFn?: typeof buildEngine } = {},
  onDiagnostic?: (diagnostic: SourceDiagnostic) => void,
  workspaceDir?: string,
): Promise<string> {
  if (!query.trim()) return ''

  let engine: SkillSearchEngine
  try {
    engine = (deps.buildEngineFn ?? buildEngine)(config, onDiagnostic, workspaceDir)
  } catch {
    return ''
  }
  if (!engine.enabled) return ''

  const controller = new AbortController()
  const timer = setTimeout(() => { controller.abort() }, config.timeoutMs)
  try {
    return await engine.retrieve(query, {
      signal: controller.signal,
      ...(config.availableTools.length > 0 ? { availableTools: config.availableTools } : {}),
    })
  } catch {
    return ''
  } finally {
    clearTimeout(timer)
  }
}
