/**
 * The host's types, copied from OpenClaw's source rather than imported.
 *
 * The plugin is built and tested without the `openclaw` peer installed, and
 * the emitted `.d.ts` must not reference the peer's internal type names. So
 * the shapes this plugin actually touches are declared here, narrowed to
 * exactly those — a field the plugin never reads is not copied, because an
 * unread field that drifts is a false alarm.
 *
 * Verified against `src/plugins/types.ts` in the host: the hook name, the
 * event fields, and the honored result fields.
 *
 * @module
 */

/** What `before_prompt_build` receives. */
export interface BeforePromptBuildEvent {
  /** The user's message for this turn. */
  prompt: string
  /** Session messages prepared for this run; shapes vary, so `unknown`. */
  messages: unknown[]
}

/**
 * What `before_prompt_build` may return.
 *
 * The host merges each plugin's result by concatenating the text fields, so
 * returning `prependContext` adds to whatever other plugins contributed
 * rather than replacing it. Retrieval uses `prependContext`, not the
 * `*SystemContext` pair: those exist for static guidance a provider can
 * cache, and a per-turn selection would invalidate that cache every turn.
 */
export interface BeforePromptBuildResult {
  systemPrompt?: string
  prependContext?: string
  appendContext?: string
  prependSystemContext?: string
  appendSystemContext?: string
}

/**
 * The agent context passed alongside a hook event.
 *
 * Note what is absent: the host does not report the agent's tool names here,
 * so the gate's environment check — the one that drops a skill needing a tool
 * this agent lacks — cannot run under OpenClaw. The gate still judges
 * relevance. `availableTools` in the plugin config is the way to restore the
 * check when a deployment knows its own tool set.
 */
export interface PluginHookAgentContext {
  agentId?: string
  sessionId?: string
  sessionKey?: string
  workspaceDir?: string
  /** What initiated this run: "user", "heartbeat", "cron", or "memory". */
  trigger?: string
  channelId?: string
}

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

/** The subset of the host's plugin API this plugin uses. */
export interface OpenClawPluginApi {
  readonly id: string
  readonly name: string
  logger?: PluginLogger
  /** Config the host resolved from `plugins.entries.<id>.config`. */
  pluginConfig?: Record<string, unknown>
  on(
    event: 'before_prompt_build',
    handler: (
      event: BeforePromptBuildEvent,
      ctx: PluginHookAgentContext,
    ) => BeforePromptBuildResult | void | Promise<BeforePromptBuildResult | void>,
  ): void
  /** Register a tool the agent may call. */
  registerTool(tool: AgentTool): void
}

/** What `definePluginEntry` accepts. */
export interface DefinePluginEntryOptions {
  readonly id: string
  readonly name?: string
  readonly description?: string
  register(api: OpenClawPluginApi): void
}

/** What `definePluginEntry` returns. */
export interface DefinedPluginEntry {
  readonly id: string
}
