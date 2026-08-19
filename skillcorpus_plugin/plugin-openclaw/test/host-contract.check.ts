/**
 * The copied host types, checked against the host's own.
 *
 * `src/openclaw-types.ts` is a hand-copy, which is what lets this plugin
 * build and test without the peer installed — and a hand-copy is exactly the
 * thing that drifts silently. This file compiles the copies against the real
 * declarations, so a signature change upstream becomes a compile error here
 * instead of a wrong `prependContext` at a user's first turn.
 *
 * Not part of `npm run typecheck`: it needs an OpenClaw checkout, which a
 * consumer does not have. Run it against one:
 *
 *     npm run check:host -- --hostSrc ../openclaw-host/src
 *
 * The assertions are assignability in both directions. One direction alone
 * would pass while the copy quietly gained or lost a field.
 */

import type {
  PluginHookAgentContext as HostAgentContext,
  PluginHookBeforePromptBuildEvent as HostEvent,
  PluginHookBeforePromptBuildResult as HostResult,
  PluginHookName as HostHookName,
} from 'openclaw-host/plugins/types.js'
import type {
  BeforePromptBuildEvent,
  BeforePromptBuildResult,
  PluginHookAgentContext,
} from '../src/openclaw-types.ts'

/** True only when `A` and `B` accept each other, field for field. */
type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2) ? true : never

/**
 * The event the host passes, and the copy the plugin reads it as.
 *
 * Equality, not one-way assignability: the plugin reads both fields, so a
 * field gained or lost upstream must fail here either way.
 */
export type EventMatches = Equal<BeforePromptBuildEvent, HostEvent>
const eventCheck: EventMatches = true

/**
 * The context the host passes.
 *
 * One direction only, deliberately: the plugin copies the fields it reads and
 * leaves the rest out, so the host's context is not assignable to the copy —
 * and should not be. What matters is that every field the plugin declares
 * still exists upstream with the same type.
 */
export type ContextIsAssignable = HostAgentContext extends PluginHookAgentContext ? true : never
const contextCheck: ContextIsAssignable = true

/**
 * The result the host honors.
 *
 * The plugin's copy carries `appendContext`, which the host's own type
 * declares too but its merge function ignores; only the four in
 * `PLUGIN_PROMPT_MUTATION_RESULT_FIELDS` are merged. The plugin returns
 * `prependContext`, which is in both.
 */
export type ResultIsAssignable = BeforePromptBuildResult extends HostResult ? true : never
const resultCheck: ResultIsAssignable = true

/** The hook name the plugin registers must still be one the host dispatches. */
export type HookNameExists = 'before_prompt_build' extends HostHookName ? true : never
const hookCheck: HookNameExists = true

export const checks = [eventCheck, contextCheck, resultCheck, hookCheck] as const
