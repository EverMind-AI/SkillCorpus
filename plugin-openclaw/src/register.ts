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
import { LocalSkillSource } from '../../engine-typescript/src/local-source.js'
import { QueryRewriter } from '../../engine-typescript/src/rewriter.js'
import type { SkillSource } from '../../engine-typescript/src/types.js'
import { loadConfig, type SkillSearchConfig } from './config.js'
import { createChatModel } from './model.js'
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
export function buildEngine(config: SkillSearchConfig): SkillSearchEngine {
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

  const model = createChatModel({
    baseUrl: config.modelBaseUrl,
    apiKey: config.modelApiKey,
    model: config.model,
  })

  return new SkillSearchEngine(
    {
      sources,
      // Two independent switches over one model. Configuring a model used
      // to turn both on together, leaving no way to keep the query cleaning
      // and drop the gate.
      ...(model && config.rewrite ? { rewriter: new QueryRewriter(model) } : {}),
      // `gate` unset means "on when a catalog is configured": the gate is
      // told to reject when unsure, which a curated local directory does
      // not need and an unvetted catalog does.
      ...(model && (config.gate ?? Boolean(config.hubEndpoint))
        ? { gate: new LLMGateFilter(model, { maxSelect: config.maxSelect }) }
        : {}),
      ...(client
        ? {
          // The engine calls both for any hit without a skill directory,
          // which is every hit a third-party source contributes too. Without
          // this guard those arrive here with `hit.meta.id` undefined and
          // fetch `/skills/undefined` from the catalog, so an extra source
          // costs a 404 per hit.
          fetchBody: async (hit, signal) => {
            if (hit.meta.source !== 'hub') return undefined
            const record = await client.get(String(hit.meta.id), signal)
            // The record rides along so `materialise` can skip re-fetching
            // the same detail — one request per selected skill otherwise.
            return {
              ...(typeof record.skill_md === 'string' ? { body: record.skill_md } : {}),
              record,
            }
          },
          materialise: async (hit, signal) => {
            if (hit.meta.source !== 'hub') return undefined
            const fetched = hit.meta._fetched as Record<string, unknown> | undefined
            const installed = await client.install(String(hit.meta.id), fetched, signal)
            return { dir: installed.dir, body: installed.skillMd }
          },
        }
        : {}),
    },
    { topK: config.topK, gatePool: config.gatePool },
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
  const engine = (deps.buildEngineFn ?? buildEngine)(config)

  if (!engine.enabled) {
    api.logger?.info?.('[skillsearch] no sources configured; retrieval is off')
    return
  }

  api.on('before_prompt_build', async (
    event: BeforePromptBuildEvent,
  ): Promise<BeforePromptBuildResult | void> => {
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
