/**
 * Plugin wiring for OpenClaw 2.0, kept apart from the entry object in
 * `index.ts`.
 *
 * Nothing here imports a runtime value from the host — only types, which
 * erase — so a test drives `register` with a fake `api` and the built bundle
 * carries no dependency on any particular OpenClaw version.
 *
 * ## Why this is a context engine and not a hook
 *
 * The 1.x plugin next door injects through the `before_prompt_build` hook.
 * On 2.0 that path is dead for a plugin installed from anywhere but an
 * official source: the handler either never runs (`agent --local`) or runs
 * and has its `prependContext` dropped (through the gateway) — measured on
 * 2026.8.1 with `hooks.allowConversationAccess`, `hooks.allowPromptInjection`
 * and `--accept-capabilities` all granted. Nothing is logged either way,
 * which is the worst shape a failure can take: retrieval looks installed and
 * silently never happens.
 *
 * 2.0's answer is capability registration. `PluginKind` is `"memory" |
 * "context-engine"`, and `assemble()` is handed the turn's `prompt`, the
 * agent's `availableTools` and the `model` — the host documents that
 * parameter as being there for "retrieval-oriented engines". So retrieval
 * moves from decorating a prompt to being part of assembling one.
 *
 * ## What occupying the slot costs
 *
 * The slot is exclusive: one context engine is active at a time, so this
 * plugin and any other context-engine plugin cannot both run. It reports
 * `ownsCompaction: false`, which keeps the host's own compaction in charge
 * of the transcript — this engine appends to an assembled context, it does
 * not manage one.
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
import { loadConfig, type SkillSearchConfig } from './config.js'
import { createChatModel } from './model.js'
import { skillSearchTool } from './tool.js'
import { VERSION } from './version.js'
import type {
  AgentMessage,
  AssembleResult,
  CommitTurnResult,
  CompactResult,
  ContextEngine,
  ContextEngineFactoryContext,
  IngestResult,
  OpenClaw2PluginApi,
  PluginLogger,
} from './openclaw2-types.js'

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
export function buildEngine(config: SkillSearchConfig, workspaceDir?: string): SkillSearchEngine {
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

/** Bound on `commitTurn`'s duplicate-detection memory; see the method. */
const MAX_TRACKED_ADVANCEMENTS = 512

/** Roughly four characters per token, the estimate the host's own docs use. */
function estimateTokens(messages: readonly AgentMessage[]): number {
  let chars = 0
  for (const message of messages) {
    const content = (message as { content?: unknown }).content
    if (typeof content === 'string') chars += content.length
    else if (content !== undefined) chars += JSON.stringify(content).length
  }
  return Math.ceil(chars / 4)
}

/**
 * The context engine: the host's assembled messages, plus this turn's skills.
 *
 * Every member the host requires is here and no more. `ingest` reports
 * `ingested: false` truthfully — this engine keeps no store of its own, and
 * claiming otherwise would tell the host its message was captured somewhere
 * it is not. `compact` declines for the same reason: `ownsCompaction` is
 * false, so the host's own compaction stays in charge.
 */
export class SkillSearchContextEngine implements ContextEngine {
  readonly info = {
    id: 'skillsearch',
    name: 'SkillSearch retrieval',
    version: VERSION,
    ownsCompaction: false,
    // Required in practice: an engine that leaves the fence undeclared is
    // degraded to `legacy` on every turn and `assemble` is never called, with
    // one gateway log line as the only trace. Adopting the host's own fence
    // is the accurate claim here — this engine appends to the messages it is
    // handed and never rewrites the transcript.
    transcriptSemantics: {
      currentTurnFence: 'before-current-turn-entry-v1',
      turnAdvancementIdempotency: 'atomic-idempotent-v1',
    },
  } as const

  /** Advancement keys seen in this process; see `commitTurn`. */
  private readonly advanced = new Set<string>()

  constructor(
    private readonly search: SkillSearchEngine,
    private readonly config: SkillSearchConfig,
    private readonly logger?: PluginLogger,
  ) {}

  async ingest(): Promise<IngestResult> {
    return { ingested: false }
  }

  /**
   * Accept one turn's advancement, reporting a repeat as a duplicate.
   *
   * This engine writes no transcript, so "advancing a turn" is only the
   * bookkeeping that lets it answer the host's retry question honestly. The
   * host may replay an `advancementKey` after a failure; a key already seen
   * in this process is a duplicate, and a key that is not — including every
   * key after a restart — is `committed`, which is accurate rather than
   * merely convenient: with nothing durable to write, re-accepting a turn
   * cannot double-write anything.
   *
   * The set is bounded because a long-lived gateway would otherwise grow one
   * entry per turn forever. Evicting the oldest keys can only turn a
   * would-be `duplicate` into a `committed`, which, per the paragraph above,
   * costs nothing.
   */
  async commitTurn(params: { advancementKey: string }): Promise<CommitTurnResult> {
    const key = params.advancementKey
    if (this.advanced.has(key)) return { status: 'duplicate' }
    if (this.advanced.size >= MAX_TRACKED_ADVANCEMENTS) {
      const oldest = this.advanced.values().next()
      if (!oldest.done) this.advanced.delete(oldest.value)
    }
    this.advanced.add(key)
    return { status: 'committed' }
  }

  async compact(): Promise<CompactResult> {
    return {
      ok: true,
      compacted: false,
      reason: 'skillsearch appends retrieval to an assembled context; it does not own compaction',
    }
  }

  async assemble(params: {
    sessionId: string
    sessionKey?: string
    messages: AgentMessage[]
    tokenBudget?: number
    availableTools?: Set<string>
    model?: string
    prompt?: string
  }): Promise<AssembleResult> {
    const messages = params.messages ?? []
    const block = await this.retrieve(params)
    const assembled = block
      ? [...messages, { role: 'user', content: block } as AgentMessage]
      : messages
    return { messages: assembled, estimatedTokens: estimateTokens(assembled) }
  }

  /** Never throws: a failure costs the turn its skills, not the turn. */
  private async retrieve(params: {
    messages: AgentMessage[]
    availableTools?: Set<string>
    prompt?: string
  }): Promise<string> {
    if (!this.search.enabled) return ''
    const query = (params.prompt || '').trim() || recentUserText(params.messages ?? [])
    if (!query) return ''

    // The host reports the agent's real tool surface here, which the 1.x hook
    // never did — so the gate's environment check runs for free, and the
    // `availableTools` config setting is only a fallback for a host that
    // reports none.
    const tools = params.availableTools?.size
      ? [...params.availableTools]
      : this.config.availableTools

    const controller = new AbortController()
    const timer = setTimeout(() => { controller.abort() }, this.config.timeoutMs)
    try {
      return await this.search.retrieve(query, {
        signal: controller.signal,
        ...(tools.length > 0 ? { availableTools: tools } : {}),
      })
    } catch (error) {
      warn(this.logger, 'retrieval failed', error)
      return ''
    } finally {
      clearTimeout(timer)
    }
  }
}

/**
 * Wire retrieval into the host: one context engine, registered per workspace.
 *
 * @param api - the host's plugin API.
 * @param deps - test seam for the engine builder.
 */
export function register(
  api: OpenClaw2PluginApi,
  deps: { buildEngineFn?: typeof buildEngine } = {},
): void {
  const config = loadConfig(api.pluginConfig)
  const build = deps.buildEngineFn ?? buildEngine

  // Probed without a workspace: `enabled` depends only on the sources.
  const probe = build(config)
  if (!probe.enabled) {
    api.logger?.info?.('[skillsearch] no sources configured; retrieval is off')
    return
  }

  if (config.mode === 'auto') {
    // The real engines are built per workspace by this factory.
    api.registerContextEngine('skillsearch', (ctx: ContextEngineFactoryContext) =>
      new SkillSearchContextEngine(build(config, ctx?.workspaceDir), config, api.logger))
    api.logger?.info?.('[skillsearch] auto mode: retrieval runs on every turn')
    return
  }

  // On demand. Deliberately no context engine: that slot is exclusive, and
  // holding it in order to inject nothing would deny it to whatever else
  // could have used it.
  //
  // An engine per workspace, as the auto path's factory already builds:
  // `{{OUTPUT_DIR}}` resolves against the turn's directory, so one engine
  // built here would expand it to the host process's own on a multi-agent
  // host or a session working elsewhere.
  api.registerTool(skillSearchTool(
    workspaceDir => build(config, workspaceDir),
    config,
    api.logger,
  ))
  api.logger?.info?.('[skillsearch] on-demand mode: the agent calls skill_search')
}
