/**
 * Per-turn skill retrieval: search on what the user just asked, inject what
 * fits.
 *
 * `dsh-tool-skill` publishes a catalog of every skill and lets the model load
 * one by name. This does the opposite: it searches local and remote sources
 * against the current message and puts the matching skill bodies straight in
 * front of the model, which needs no tool call and no name it has to already
 * know. The two are alternatives — running both publishes the same skills
 * twice — so a deployment picking this one disables `dsh-tool-skill`.
 *
 * Retrieval runs on `agent/pre-step`, before the model is called, and never
 * throws: a failed source or a slow catalog costs the turn its skills and
 * nothing else.
 *
 * @module @deepseek-ai/dsh-skill-search
 */

import { homedir } from 'node:os'
import { join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import type { Agent, PreStepDecision } from '@deepseek-ai/dsh-agent'
import { BlockAssembler, createUserMessage } from '@deepseek-ai/dsh-llm'
import type { FinishReason, GenerateOptions } from '@deepseek-ai/dsh-llm'
import type { UserMessage } from '@deepseek-ai/dsh-session'
// Declaration-merges the optional `tools` service this reads through `ctx.get`.
import type {} from '@deepseek-ai/dsh-tools'
import { SkillSearchEngine } from './engine.js'
import type { SkillSource } from './types.js'
import { LLMGateFilter } from './gate.js'
import { HubSkillSource, SkillHubClient } from './hub-source.js'
import { LocalSkillSource } from './local-source.js'
import { MarketplaceClient, MarketplaceSkillSource } from './marketplace-source.js'
import { QueryRewriter } from './rewriter.js'

export const name = 'skill-search'
export const inject = ['agents', 'llm']

export * from './types.js'
export { SkillSearchEngine } from './engine.js'
export type {
  EngineOptions, EngineParts, RetrieveOptions, SourceDiagnostic, SourceDiagnosticStage,
} from './engine.js'
export { LLMGateFilter } from './gate.js'
export { HubSkillSource, SkillHubClient } from './hub-source.js'
export { LocalSkillSource } from './local-source.js'
export { MarketplaceClient, MarketplaceSkillSource } from './marketplace-source.js'
export type { MarketplaceItem, MarketplaceKind } from './marketplace-source.js'
export { QueryRewriter } from './rewriter.js'
export { checkKeywordRelevance, compactCatalogQuery, queryTerms } from './relevance.js'
export { BM25Okapi, tokenize } from './bm25.js'
export { RRF_K, rrfMergeWeighted } from './fusion.js'
export { resolveRefs } from './refs.js'

/** Per-turn skill retrieval configuration. */
export interface Config {
  /** Directories scanned for `SKILL.md`. Relative paths resolve against cwd. */
  skillsDirs?: string[]
  /** Remote catalog base URL. Empty disables the remote source. */
  hubEndpoint?: string
  /** Bearer token the catalog requires, if any. */
  hubApiKey?: string
  /** ClawHub API base URL. Empty disables this source. */
  clawhubEndpoint?: string
  /** skillhub.cn API base URL. Empty disables this source. */
  skillhubCnEndpoint?: string
  /**
   * Where extracted bundles live. Outside every scanned skills directory,
   * or a downloaded skill reappears as a local one on the next scan.
   */
  bundleCacheDir?: string
  /** Per-request deadline for the catalog, search and body fetch alike. */
  /**
   * Index skill bodies alongside name and description. Off by default:
   * the description is what the format asks authors to write the trigger
   * conditions into, and it is what the gate reads.
   */
  indexBody?: boolean

  /**
   * Clean the query before searching. On by default: since it lost the
   * power to veto retrieval it can only sharpen a match, never remove one.
   * Requires `provider` and `model`.
   */
  rewrite?: boolean

  /**
   * Let a model drop candidates before they reach the prompt.
   *
   * Unset means "on when `hubEndpoint` is configured". The gate is told to
   * reject when unsure: a curated local directory is better served by
   * ranking alone, while a catalog of unvetted skills needs the check for
   * whether this agent even has the tools a skill calls for. An explicit
   * value is always honoured. Requires `provider` and `model`.
   */
  gate?: boolean

  hubTimeoutMs?: number
  /**
   * Drop catalog entries whose safety score falls below this. Only bites on
   * catalogs that put per-skill safety in the search payload.
   */
  hubMinSafety?: number
  /** Fusion weight for local skills, which are curated and so outrank. */
  weightLocal?: number
  /** Fusion weight for catalog skills. */
  weightHub?: number
  /** Skills injected per turn. */
  topK?: number
  /** Candidates the gate judges before selecting. */
  gatePool?: number
  /** Upper bound on what the gate keeps. */
  maxSelect?: number
  /**
   * Registered provider route for the rewriter and the gate. Required with
   * `model`; either one alone fails at load.
   */
  provider?: string
  /**
   * Model for the rewriter and the gate. Unset runs retrieval unfiltered,
   * which lets a weak best-hit from any source reach the prompt: fusion ranks
   * by position, and removing those is the gate's job.
   */
  model?: string
  /**
   * Deadline for the rewrite, the turn's first model call. Tight because
   * it runs before the gate and before the model answers: a stalled
   * rewriter must degrade to searching the raw query, not hold the turn.
   */
  rewriteTimeoutMs?: number
  /** Deadline for the gate, which runs before the user sees a reply. */
  gateTimeoutMs?: number
  /**
   * Rewrite `{baseDir}` and bundled-file links in selected bodies to absolute
   * paths. Turn off when the agent does not share a filesystem with the
   * skills it retrieves.
   */
  resolveRefs?: boolean
}

export const Config: z<Config> = z.object({
  skillsDirs: z.array(z.string()).default(['.dsh/skills']),
  hubEndpoint: z.string().default(''),
  hubApiKey: z.string().default(''),
  clawhubEndpoint: z.string().default('https://clawhub.ai'),
  skillhubCnEndpoint: z.string().default('https://api.skillhub.cn'),
  bundleCacheDir: z.string().default(''),
  indexBody: z.boolean().default(false),
  rewrite: z.boolean().default(true),
  gate: z.boolean(),
  hubTimeoutMs: z.number().default(5000),
  hubMinSafety: z.number().default(0.7),
  weightLocal: z.number().default(1.0),
  weightHub: z.number().default(0.85),
  topK: z.number().default(2),
  gatePool: z.number().default(10),
  maxSelect: z.number().default(2),
  provider: z.string().default(''),
  model: z.string().default(''),
  rewriteTimeoutMs: z.number().default(5_000),
  gateTimeoutMs: z.number().default(20_000),
  resolveRefs: z.boolean().default(true),
})

/** Marks the messages this plugin publishes. */
export interface SkillSearchSource {
  readonly kind: 'skill-search'
  /** Injected skill bodies are instructions for the model to follow. */
  readonly form: 'instructions'
  /**
   * The ids injected this turn, so a transcript consumer reads what the model
   * was shown from metadata instead of re-parsing the model-facing text.
   */
  readonly skillIds: readonly string[]
}

declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    /** Skills retrieved for the current turn and injected before the step. */
    'skill-search': SkillSearchSource
  }
}

export function apply(ctx: Context, config: Config = {}): void {
  const cfg = Config(config)
  const engine = buildEngine(ctx, cfg)
  if (!engine.enabled) {
    ctx.logger('skill-search').info('no sources configured; retrieval is off')
    return
  }

  ctx.on('agent/pre-step', async (
    { agent, messages, signal },
    next,
  ): Promise<PreStepDecision> => {
    const decision = await next()
    if (decision.kind === 'reject') return decision
    const query = latestUserText(messages)
    if (!query) return decision
    signal.throwIfAborted()

    const hits = await engine.hits(query, { signal, availableTools: toolNames(ctx, agent) })
    if (hits.length === 0) return decision
    const text = engine.render(hits)
    if (!text) return decision
    signal.throwIfAborted()

    const source: SkillSearchSource = {
      kind: 'skill-search',
      form: 'instructions',
      skillIds: hits.map(h => h.qualifiedId),
    }
    const injection: UserMessage = createUserMessage({
      content: [{ type: 'text', text }],
      source,
    })
    return { kind: 'enter', messages: [...decision.messages, injection] }
  })
}

function buildEngine(ctx: Context, cfg: Config): SkillSearchEngine {
  const sources: SkillSource[] = []

  const dirs = cfg.skillsDirs ?? []
  if (dirs.length > 0) {
    const local = new LocalSkillSource(
      dirs.map(path => ({ path, name: 'local' })),
      { indexBody: cfg.indexBody ?? false },
    )
    local.weight = cfg.weightLocal ?? 1.0
    sources.push(local)
  }

  let client: SkillHubClient | undefined
  if (cfg.hubEndpoint) {
    client = new SkillHubClient(cfg.hubEndpoint, {
      ...(cfg.hubApiKey ? { apiKey: cfg.hubApiKey } : {}),
      timeoutMs: cfg.hubTimeoutMs ?? 5000,
      // Beside the scanned directories, never inside one: an extracted
      // bundle under a skills directory would be rescanned as a local skill.
      cacheDir: cfg.bundleCacheDir || join(homedir(), '.dsh', 'skillsearch-bundles'),
    })
    sources.push(
      new HubSkillSource(client, {
        weight: cfg.weightHub ?? 0.85,
        minSafety: cfg.hubMinSafety ?? 0.7,
      }),
    )
  }

  const marketplaceClients = new Map<string, MarketplaceClient>()
  for (const [kind, endpoint] of [['clawhub', cfg.clawhubEndpoint], ['skillhub_cn', cfg.skillhubCnEndpoint]] as const) {
    if (!endpoint) continue
    const marketplace = new MarketplaceClient(kind, endpoint, {
      cacheDir: cfg.bundleCacheDir || join(homedir(), '.dsh', 'skillsearch-bundles'),
      timeoutMs: cfg.hubTimeoutMs ?? 5000,
    })
    marketplaceClients.set(kind, marketplace)
    sources.push(new MarketplaceSkillSource(marketplace))
  }

  const route = resolveRoute(cfg)
  const model = route ? modelBridge(ctx, route) : undefined
  // Two independent switches over one model, matching the Python config.
  // Configuring a model used to turn both on together, which left no way
  // to keep the query cleaning and drop the gate.
  const wantsRewrite = cfg.rewrite ?? true
  // `undefined` means "on when a catalog is configured": the gate rejects
  // when unsure, which a curated local directory does not need and an
  // unvetted catalog does.
  const wantsGate = cfg.gate ?? (Boolean(cfg.hubEndpoint) || marketplaceClients.size > 0)
  return new SkillSearchEngine(
    {
      sources,
      ...(model && wantsRewrite
        ? {
          rewriter: new QueryRewriter(model, {
            ...(cfg.rewriteTimeoutMs === undefined ? {} : { timeoutMs: cfg.rewriteTimeoutMs }),
          }),
        }
        : {}),
      ...(model && wantsGate
        ? {
          gate: new LLMGateFilter(model, {
            maxSelect: cfg.maxSelect ?? 2,
            ...(cfg.gateTimeoutMs === undefined ? {} : { timeoutMs: cfg.gateTimeoutMs }),
          }),
        }
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
      topK: cfg.topK ?? 2,
      gatePool: cfg.gatePool ?? 10,
      resolveRefs: cfg.resolveRefs ?? true,
    },
  )
}

/**
 * The explicit provider/model pair for the rewriter and the gate.
 *
 * Both or neither: a lone `model` would otherwise pick some default route
 * silently, and a lone `provider` reads as a configured gate that never runs.
 */
function resolveRoute(cfg: Config): { provider: string; model: string } | undefined {
  const provider = cfg.provider ?? ''
  const model = cfg.model ?? ''
  if (!provider && !model) return undefined
  if (!provider || !model) {
    throw new Error('skill-search: configure `provider` and `model` together, or neither')
  }
  return { provider, model }
}

/**
 * Adapt `ctx.llm.stream()` to the single-shot completion the rewriter and gate
 * expect. Both send one prompt and parse one JSON reply, so the stream is
 * drained into text here rather than exposed further.
 *
 * A terminal finish rejects, which each caller turns into its own documented
 * fallback: the rewriter searches anyway, the gate keeps its top candidates.
 */
function modelBridge(
  ctx: Context,
  route: { provider: string; model: string },
): { complete(prompt: string, options: { signal?: AbortSignal }): Promise<string> } {
  return {
    async complete(prompt, options) {
      const request: GenerateOptions = {
        provider: route.provider,
        model: route.model,
        messages: [
          createUserMessage({
            content: [{ type: 'text', text: prompt }],
            // Internal retrieval prompts, not part of the user's conversation.
            source: { kind: 'plugin', plugin: 'dsh-skill-search' },
          }),
        ],
        ...(options.signal ? { signal: options.signal } : {}),
      }
      const assembler = new BlockAssembler()
      for await (const chunk of ctx.llm.stream(request)) assembler.push(chunk)
      const failure = finishError(assembler.finish)
      if (failure !== undefined) throw failure
      return assembler
        .blocks()
        .filter((block): block is Extract<typeof block, { type: 'text' }> => block.type === 'text')
        .map(block => block.text)
        .join('')
    },
  }
}

/**
 * Map a finish this plugin cannot use into the error its callers fall back on.
 *
 * `max-tokens` is terminal here even though text arrived: a truncated reply
 * carries a truncated JSON object, and parsing half a selection is worse than
 * taking the documented fallback.
 */
function finishError(finish: FinishReason): Error | undefined {
  switch (finish.kind) {
    case 'stop':
      return undefined
    case 'error':
    case 'aborted':
      return new Error(finish.failure.message)
    case 'max-tokens':
      return new Error('skill-search: reply hit the model’s output limit')
    case 'tool-calls':
      return new Error('skill-search: model requested a tool instead of answering')
    default:
      return new Error(`skill-search: unusable finish "${String((finish as { kind?: unknown }).kind)}"`)
  }
}

/**
 * The tools this agent may call, or `undefined` where no tool service is
 * mounted. The gate uses them to reject a skill this agent cannot execute.
 */
function toolNames(ctx: Context, agent: Agent): readonly string[] | undefined {
  const tools = ctx.get('tools')
  return tools?.schemas(agent).map(schema => schema.name)
}

/** The most recent user turn, which is what retrieval searches on. */
function latestUserText(messages: readonly unknown[]): string {
  for (const entry of [...messages].reverse()) {
    const message = entry as { role?: string; content?: unknown }
    if (message.role !== 'user') continue
    const content = message.content
    if (typeof content === 'string') return content
    if (Array.isArray(content)) {
      const text = content
        .map(block => (block as { text?: string }).text ?? '')
        .join(' ')
        .trim()
      if (text) return text
    }
  }
  return ''
}
