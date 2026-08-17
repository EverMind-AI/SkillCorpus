/**
 * Plugin wiring, split from `index.ts` so it can be driven by a test.
 *
 * `index.ts` imports `definePluginEntry`, a runtime value from the `openclaw`
 * peer, which does not resolve outside the host — importing it in a test
 * crashes at module load. This module imports only types from the host, which
 * erase at runtime, so a test can call `register` with a fake `api`.
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
    sources.push(new LocalSkillSource(dirs.map(path => ({ path, name: 'local' })), {}))
  }

  let client: SkillHubClient | undefined
  if (config.hubEndpoint) {
    client = new SkillHubClient(config.hubEndpoint, {
      ...(config.hubApiKey ? { apiKey: config.hubApiKey } : {}),
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
      ...(model ? { rewriter: new QueryRewriter(model) } : {}),
      ...(model ? { gate: new LLMGateFilter(model, { maxSelect: config.maxSelect }) } : {}),
      ...(client
        ? {
          fetchBody: async (hit, signal) => {
            const record = await client.get(String(hit.meta.id), signal)
            return typeof record.skill_md === 'string' ? record.skill_md : undefined
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
