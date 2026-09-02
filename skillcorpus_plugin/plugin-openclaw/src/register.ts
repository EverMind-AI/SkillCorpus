/**
 * Plugin wiring, kept apart from the entry object in `index.ts`.
 *
 * Nothing here imports a runtime value from the host — only types, which
 * erase — so a test drives `register` with a fake `api` and the built bundle
 * carries no dependency on any particular OpenClaw version.
 *
 * @module
 */

import { homedir } from 'node:os'
import { isAbsolute, join } from 'node:path'
import { SkillSearchEngine } from '../../engine-typescript/src/engine.js'
import { LLMGateFilter } from '../../engine-typescript/src/gate.js'
import { HubSkillSource, SkillHubClient } from '../../engine-typescript/src/hub-source.js'
import { MarketplaceClient, MarketplaceSkillSource } from '../../engine-typescript/src/marketplace-source.js'
import { LocalSkillSource } from '../../engine-typescript/src/local-source.js'
import { QueryRewriter } from '../../engine-typescript/src/rewriter.js'
import type { SkillSource } from '../../engine-typescript/src/types.js'
import { loadConfig, unknownMode, type SkillSearchConfig } from './config.js'
import { createChatModel } from './model.js'
import { skillSearchTool } from './tool.js'
import type {
  BeforePromptBuildEvent,
  BeforePromptBuildResult,
  OpenClawPluginApi,
  PluginLogger,
} from './openclaw-types.js'

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
  workspaceDir?: string,
  logger?: PluginLogger,
): SkillSearchEngine {
  const sources: SkillSource[] = []

  const dirs = config.skillsDirs.map(dir => expandHome(dir)).filter(dir => isAbsolute(dir) || dir)
  if (dirs.length > 0) {
    sources.push(new LocalSkillSource(
      dirs.map(path => ({ path, name: 'local' })),
      { indexBody: config.indexBody },
    ))
  }

  let client: SkillHubClient | undefined
  if (config.hubEndpoint) {
    client = new SkillHubClient(config.hubEndpoint, {
      ...(config.hubApiKey ? { apiKey: config.hubApiKey } : {}),
      // Outside every scanned directory: an extracted bundle inside one
      // would be picked up as a local skill on the next scan.
      cacheDir: config.bundleCacheDir || join(homedir(), '.openclaw', 'skillsearch-bundles'),
    })
    sources.push(new HubSkillSource(client))
  }

  const marketplaceClients = new Map<string, MarketplaceClient>()
  for (const [kind, endpoint] of [
    ['clawhub', config.clawhubEndpoint],
    ['skillhub_cn', config.skillhubCnEndpoint],
  ] as const) {
    if (!endpoint) continue
    const marketplace = new MarketplaceClient(kind, endpoint, {
      cacheDir: config.bundleCacheDir ? expandHome(config.bundleCacheDir) : join(homedir(), '.openclaw', 'skillsearch-bundles'),
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
      // Without this a source that is down is invisible here. The engine
      // already reports it — one failing source leaves the others usable, by
      // design — but nothing was consuming the report, so "the catalogue was
      // unreachable all afternoon" and "the catalogue had nothing" looked
      // identical from the outside. Only diagnostics carrying an error are
      // logged; the successful ones are per-turn noise.
      onDiagnostic: diagnostic => {
        if (diagnostic.error === undefined) return
        logger?.warn?.(
          `[skillsearch] source ${diagnostic.source} failed at ${diagnostic.stage}: ${diagnostic.error}`,
        )
      },
      // Two independent switches over one model. Configuring a model used
      // to turn both on together, leaving no way to keep the query cleaning
      // and drop the gate.
      ...(model && config.rewrite ? { rewriter: new QueryRewriter(model) } : {}),
      // `gate` unset means "on when a catalog is configured": the gate is
      // told to reject when unsure, which a curated local directory does
      // not need and an unvetted catalog does.
      ...(model && (config.gate ?? (Boolean(config.hubEndpoint) || marketplaceClients.size > 0))
        ? { gate: new LLMGateFilter(model, { maxSelect: config.maxSelect }) }
        : {}),
      ...((client || marketplaceClients.size > 0)
        ? {
          // The engine calls both for any hit without a skill directory,
          // which is every hit a third-party source contributes too. Without
          // this guard those arrive here with `hit.meta.id` undefined and
          // fetch `/skills/undefined` from the catalog, so an extra source
          // costs a 404 per hit.
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
      // PathGuard placeholders' per-agent facts. An absent `workspaceDir`
      // means "this caller has no trustworthy workspace", and the empty
      // string leaves `{{OUTPUT_DIR}}` literal rather than pointing a skill
      // at some other directory — see the tool path, which passes none. OpenClaw's own config root is
      // ~/.openclaw; the agent's writable output is its workspace, falling back
      // to the host process's cwd when the hook did not report one.
      outputDir: workspaceDir ?? '',
      homeDir: homedir(),
      stateDir: join(homedir(), '.openclaw'),
      resolvePlaceholders: config.resolvePlaceholders,
    },
  )
}

/** The most recent user text, for a turn whose `prompt` arrived empty. */
export function recentUserText(messages: readonly unknown[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as { role?: unknown; content?: unknown } | null
    if (!message || message.role !== 'user') continue
    const content = message.content
    if (typeof content === 'string' && content.trim()) return content
    if (Array.isArray(content)) {
      const text = content
        .map(block => (block as { text?: unknown }).text)
        .filter((value): value is string => typeof value === 'string')
        .join(' ')
        .trim()
      if (text) return text
    }
  }
  return ''
}

function warn(logger: PluginLogger | undefined, message: string, error: unknown): void {
  logger?.warn?.(`[skillsearch] ${message}`, error)
}

/**
 * Wire retrieval into the host: one hook, one injected block.
 *
 * Nothing here throws. The hook runs between the user's message and the
 * model's reply, so a failure returns no block and the turn proceeds
 * without skills.
 *
 * @param api - the host's plugin API.
 * @param deps - test seam for the engine builder.
 */
export function register(
  api: OpenClawPluginApi,
  deps: { buildEngineFn?: typeof buildEngine } = {},
): void {
  const config = loadConfig(api.pluginConfig)
  const asked = unknownMode(api.pluginConfig)
  if (asked !== undefined) {
    // Narrowed, not rejected — but said out loud. Without this the operator
    // who typed `atuo` gets the opposite mode and no indication of it.
    api.logger?.warn?.(
      `[skillsearch] unknown mode ${JSON.stringify(asked)}; running in ${config.mode}`,
    )
  }
  const build = deps.buildEngineFn ?? buildEngine

  // Probe without a workspace: `enabled` depends only on the sources, not on
  // the output directory.
  const probe = build(config)
  if (!probe.enabled) {
    api.logger?.info?.('[skillsearch] no sources configured; retrieval is off')
    return
  }

  // One engine per workspace, shared by both modes. `{{OUTPUT_DIR}}` resolves
  // against the turn's directory, and `process.cwd()` is the host process's
  // own — wrong for a multi-agent host and for any session working elsewhere.
  const engines = new Map<string, SkillSearchEngine>()
  const engineFor = (workspaceDir?: string): SkillSearchEngine => {
    const key = workspaceDir || process.cwd()
    let engine = engines.get(key)
    if (!engine) {
      engine = build(config, key, api.logger)
      engines.set(key, engine)
    }
    return engine
  }

  if (config.mode === 'on_demand') {
    // The agent decides when it needs a skill. No hook: injecting on every
    // turn is the other mode, and running both would pay for retrieval twice
    // and put the same skill in front of the model from two directions.
    // The tool API has no trustworthy session workspace, so reuse the probe
    // built without one. This leaves workspace-dependent PathGuard
    // placeholders unresolved instead of expanding them to the gateway cwd.
    api.registerTool(skillSearchTool(() => probe, config, api.logger))
    api.logger?.info?.('[skillsearch] on-demand mode: the agent calls skill_search')
    return
  }
  api.logger?.info?.('[skillsearch] auto mode: retrieval runs on every turn')

  api.on('before_prompt_build', async (
    event: BeforePromptBuildEvent,
    ctx: { workspaceDir?: string },
  ): Promise<BeforePromptBuildResult | void> => {
    const engine = engineFor(ctx?.workspaceDir)

    const query = (event.prompt || '').trim() || recentUserText(event.messages ?? [])
    if (!query) return

    const controller = new AbortController()
    const timer = setTimeout(() => { controller.abort() }, config.timeoutMs)
    try {
      const block = await engine.retrieve(query, {
        signal: controller.signal,
        ...(config.availableTools.length > 0 ? { availableTools: config.availableTools } : {}),
      })
      return block ? { prependContext: block } : undefined
    } catch (error) {
      warn(api.logger, 'retrieval failed', error)
      return
    } finally {
      clearTimeout(timer)
    }
  })
}
