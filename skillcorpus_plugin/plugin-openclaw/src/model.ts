/**
 * A chat model for the rewriter and the gate.
 *
 * OpenClaw resolves the agent's own provider inside the runtime and does not
 * hand a client to a hook, so this plugin brings its own: one POST to an
 * OpenAI-compatible endpoint. That is the entire `ChatModel` surface the
 * engine needs — a prompt in, text out.
 *
 * Configuring one matters more than it sounds. Fusion ranks by position, so
 * every source's best hit reaches the shortlist however weakly it matched,
 * and the gate is the only step that removes those. Without a model there is
 * no gate: an unrelated turn still gets a skill.
 *
 * @module
 */

/** Settings for one endpoint. */
export interface ChatModelOptions {
  readonly baseUrl: string
  readonly apiKey: string
  readonly model: string
}

/**
 * Build the completion function the engine's rewriter and gate call.
 *
 * @param options - endpoint, credential and model id.
 * @returns an object with `complete`, or `undefined` when no model is
 *   configured — which the engine reads as "run unfiltered".
 */
export function createChatModel(options: ChatModelOptions): {
  complete(prompt: string, opts: { signal?: AbortSignal | undefined }): Promise<string>
} | undefined {
  if (!options.model) return undefined
  const base = options.baseUrl.replace(/\/+$/, '')

  return {
    async complete(prompt, opts) {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (options.apiKey) headers.Authorization = `Bearer ${options.apiKey}`

      const response = await fetch(`${base}/chat/completions`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          model: options.model,
          messages: [{ role: 'user', content: prompt }],
          temperature: 0,
        }),
        ...(opts.signal ? { signal: opts.signal } : {}),
      })
      if (!response.ok) {
        const detail = await response.text().catch(() => '')
        throw new Error(`model endpoint returned HTTP ${response.status}: ${detail.slice(0, 200)}`)
      }
      const body = (await response.json()) as {
        choices?: { message?: { content?: string } }[]
      }
      return body.choices?.[0]?.message?.content ?? ''
    },
  }
}
