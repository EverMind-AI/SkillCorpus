/**
 * OpenClaw 2.0's types, copied from the host rather than imported.
 *
 * Same rule as the 1.x plugin next door: the package builds and tests without
 * the host installed, and the emitted `.d.ts` must not name the host's
 * internal types — so only the shapes this plugin actually touches are
 * declared, narrowed to exactly those.
 *
 * Copied from `ContextEngine`, `ContextEngineFactoryContext`, `AssembleResult`
 * and friends in the host's `src/context-engine/`, verified against OpenClaw
 * 2026.8.1. `test/host-contract.check.ts` typechecks these against the real
 * host when one is on the path.
 *
 * @module
 */

/** A message in the host's transcript. Opaque here: we only pass them through. */
export type AgentMessage = Record<string, unknown>

/** What the host asks a context engine to report about itself. */
export interface ContextEngineInfo {
  id: string
  name: string
  version?: string
  /**
   * Whether the engine manages its own compaction lifecycle.
   *
   * False here, and that is the whole reason this plugin can occupy the slot
   * honestly: retrieval appends to an assembled context, it does not own the
   * transcript. Saying `true` would tell the host to stop running its own
   * compaction and hand a growing session to an engine that never compacts it.
   */
  ownsCompaction?: boolean
  /**
   * The transcript semantics the engine implements.
   *
   * Not optional in practice, whatever the type says: an engine that leaves
   * `currentTurnFence` undeclared is degraded to `legacy` on every turn, and
   * the only trace is one gateway log line — `assemble` is simply never
   * called. Declaring the fence is what makes the registration take effect.
   */
  transcriptSemantics?: {
    currentTurnFence?: 'before-current-turn-entry-v1'
    turnAdvancementIdempotency?: 'atomic-idempotent-v1'
  }
}

/** What `assemble` must return. */
export interface AssembleResult {
  /** Ordered messages to use as model context. */
  messages: AgentMessage[]
  /** Estimated total tokens in the assembled context. */
  estimatedTokens: number
}

/** What `ingest` must return. */
export interface IngestResult {
  /** Whether the message was ingested; `false` for a duplicate or a no-op. */
  ingested: boolean
}

/** What `compact` must return. */
export interface CompactResult {
  ok: boolean
  compacted: boolean
  reason?: string
}

/** What `commitTurn` must return. */
export interface CommitTurnResult {
  status: 'committed' | 'duplicate'
}

/** The four members the host requires; the rest of its interface is optional. */
export interface ContextEngine {
  readonly info: ContextEngineInfo
  ingest(params: { sessionId: string; sessionKey?: string; message: AgentMessage }): Promise<IngestResult>
  /**
   * Accept one turn's advancement.
   *
   * Optional in the host's interface, but required in fact by anything that
   * declares `turnAdvancementIdempotency`: the host checks the declaration
   * *and* `typeof engine.commitTurn === 'function'` together, and an engine
   * that declares without implementing is degraded to `legacy` every turn.
   */
  commitTurn?(params: {
    advancementKey: string
    sessionId: string
    sessionKey?: string
    messages: AgentMessage[]
  }): Promise<CommitTurnResult>
  assemble(params: {
    sessionId: string
    sessionKey?: string
    messages: AgentMessage[]
    tokenBudget?: number
    /** Tool names available for this run. */
    availableTools?: Set<string>
    /** Current model identifier, so an engine can adapt formatting per model. */
    model?: string
    /** The incoming user prompt for this turn — what retrieval runs on. */
    prompt?: string
  }): Promise<AssembleResult>
  compact(params: { sessionId: string; sessionKey: string }): Promise<CompactResult>
}

/** What the host hands the factory when it builds the engine for a session. */
export interface ContextEngineFactoryContext {
  agentDir?: string
  /**
   * The workspace this engine serves.
   *
   * The factory runs per agent/workspace, which is why the 1.x plugin's
   * per-turn `runtime` plumbing has no counterpart here: the workspace is
   * settled before the engine exists and does not change under it.
   */
  workspaceDir?: string
}

export type ContextEngineFactory = (
  ctx: ContextEngineFactoryContext,
) => ContextEngine | Promise<ContextEngine>

/** What a tool returns to the model. */
export interface ToolResult {
  content: { type: 'text'; text: string }[]
  details: unknown
}

/** The slice of the host's tool-execution context this plugin reads. */
export interface ToolExecuteContext {
  /** Tool names this agent can call, for the gate's environment check. */
  availableTools?: readonly string[]
}

/**
 * A tool definition, narrowed to the fields this plugin sets.
 *
 * The host types `parameters` as a TypeBox `TSchema`, which at runtime is a
 * plain JSON Schema object — so the schema is written as an object literal
 * here and the plugin still imports no runtime value from the host.
 */
export interface AgentTool {
  name: string
  label: string
  description: string
  parameters: unknown
  execute(
    toolCallId: string,
    params: Record<string, unknown>,
    signal: AbortSignal | undefined,
    onUpdate: unknown,
    ctx?: ToolExecuteContext,
  ): Promise<ToolResult>
}

/** Where the plugin writes diagnostics. Every method is optional. */
export interface PluginLogger {
  debug?(message: string, ...args: unknown[]): void
  info?(message: string, ...args: unknown[]): void
  warn?(message: string, ...args: unknown[]): void
  error?(message: string, ...args: unknown[]): void
}

/** The subset of the host's 2.0 plugin API this plugin uses. */
export interface OpenClaw2PluginApi {
  readonly id: string
  readonly name: string
  logger?: PluginLogger
  /** Config the host resolved from `plugins.entries.<id>.config`. */
  pluginConfig?: Record<string, unknown>
  /** Register a context engine. Exclusive slot — only one is active at a time. */
  registerContextEngine(id: string, factory: ContextEngineFactory): void
  /** Register a tool the agent may call. */
  registerTool(tool: AgentTool): void
}

/** What `definePluginEntry` accepts. */
export interface DefinePluginEntryOptions {
  readonly id: string
  readonly name?: string
  readonly description?: string
  register(api: OpenClaw2PluginApi): void
}

/** What `definePluginEntry` returns. */
export interface DefinedPluginEntry {
  readonly id: string
}
