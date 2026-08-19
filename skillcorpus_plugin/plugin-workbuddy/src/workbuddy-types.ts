/**
 * The hook contract, as WorkBuddy 5.3.13 actually speaks it.
 *
 * Every field below was observed on the wire, not read from documentation:
 * the host writes one JSON object to the hook's stdin and reads one from its
 * stdout. Nothing here is imported from the host — a hook is a process, not a
 * plugin loaded into one — so this file is the whole seam.
 *
 * @module
 */

/** What the host writes to stdin for a `UserPromptSubmit` hook. */
export interface UserPromptSubmitPayload {
  readonly hook_event_name?: string
  /** The user's message for this turn. Empty on a turn with no visible text. */
  readonly prompt?: string
  readonly session_id?: string
  /** Absolute path of the session `.jsonl`. */
  readonly transcript_path?: string
  readonly cwd?: string
  /** `default` | `bypassPermissions` | … — the host's permission mode. */
  readonly permission_mode?: string
  /** `WorkBuddy` on the desktop client. */
  readonly client?: string
  readonly version?: string
  /** Route the turn runs on, e.g. `fast-model`. */
  readonly model?: string
  /** Present when the turn belongs to a sub-agent rather than the root one. */
  readonly agent_type?: string
  readonly generation_id?: string
}

/**
 * What the host reads from stdout.
 *
 * `additionalContext` is wrapped in `<system-reminder data-role="hook">` and
 * appended to the user message, then dropped from the pending buffer — it
 * reaches the model but is never written to the transcript. Omitting the field
 * injects nothing, which is the right answer for a turn with no hits.
 *
 * `continue: false` aborts the turn. This plugin never sends it.
 */
export interface UserPromptSubmitResult {
  readonly continue: true
  readonly hookSpecificOutput?: {
    readonly hookEventName: 'UserPromptSubmit'
    readonly additionalContext: string
  }
}
