/**
 * `skill_search` — the on-demand half of retrieval.
 *
 * Two modes exist because two different things go wrong with one.
 *
 * **Auto** searches every turn and injects what it finds. It discovers
 * capability the agent did not know to ask for, which is exactly what a short
 * turn wants — and it pays for that on every turn, in tokens and in latency,
 * whether or not the turn had anything to do with skills.
 *
 * **On demand** hands the agent a tool and lets it decide. A long task calls
 * it once, when it reaches the step that needs a skill, and the rest of the
 * turns cost nothing. The trade is that an agent that never thinks to search
 * never finds anything — so the tool's description is not decoration here, it
 * is the whole discovery mechanism, and it is written to say plainly when to
 * reach for it.
 *
 * The tool returns the same rendered block the auto path injects, so a skill
 * reads identically however it arrived.
 *
 * @module
 */

import type { SkillSearchEngine } from '../../engine-typescript/src/engine.js'
import type { SkillSearchConfig } from './config.js'
import type { AgentTool, PluginLogger, ToolExecuteContext, ToolResult } from './openclaw-types.js'

export const SKILL_SEARCH_TOOL_NAME = 'skill_search'

// Joined with newlines, not spaces: the empty strings above are blank lines,
// and the dashes are a list. `.join(' ')` flattened the whole thing into one
// paragraph — the model still called the tool, but the shape the text was
// written in never reached it.
const DESCRIPTION = [
  'Search the skill library for a procedure that fits the task at hand, and',
  'get back the matching skills in full.',
  '',
  'A skill is a written workflow for a specific job — filling PDF forms,',
  'building a slide deck, migrating a schema — including the exact commands,',
  'files, and in-house conventions it needs.',
  '',
  'Reach for it when:',
  '- a task needs a multi-step procedure you would otherwise improvise;',
  '- a task names a format, tool, or workflow you would have to guess at;',
  '- a question asks about an internal convention, template, standard, or',
  '  "our" way of doing something — a skill is where those are written down,',
  '  so searching here comes before answering that you do not know.',
  '',
  'Search with the words the task actually uses; the query is matched against',
  'skill names and descriptions. Returns nothing when the library has no fit,',
  'which is a normal answer and means: proceed on your own.',
].join('\n')

/** The parameter schema, written as the plain JSON Schema the host reads. */
const PARAMETERS = {
  type: 'object',
  properties: {
    query: {
      type: 'string',
      description:
        'What the agent needs to do, in the task\'s own words — e.g. "extract tables from a scanned PDF invoice".',
    },
  },
  required: ['query'],
  additionalProperties: false,
} as const

function text(value: string): ToolResult {
  return { content: [{ type: 'text', text: value }], details: {} }
}

/**
 * Build the tool over an engine.
 *
 * @param search - the retrieval engine, already configured.
 * @param config - the resolved plugin configuration, for the deadline.
 * @param logger - where a failure is reported.
 * @returns the tool definition to hand `api.registerTool`.
 */
export function skillSearchTool(
  search: SkillSearchEngine,
  config: SkillSearchConfig,
  logger?: PluginLogger,
): AgentTool {
  return {
    name: SKILL_SEARCH_TOOL_NAME,
    label: 'Skill search',
    description: DESCRIPTION,
    parameters: PARAMETERS,
    async execute(
      _toolCallId: string,
      params: { query?: unknown },
      signal: AbortSignal | undefined,
      _onUpdate: unknown,
      ctx?: ToolExecuteContext,
    ): Promise<ToolResult> {
      const query = typeof params?.query === 'string' ? params.query.trim() : ''
      if (!query) return text('skill_search needs a query describing the task.')

      // The agent's own deadline, not the turn's: unlike the auto path this
      // runs inside a tool call the model is already waiting on, so the same
      // bound applies for the same reason.
      const controller = new AbortController()
      const abort = (): void => { controller.abort() }
      signal?.addEventListener('abort', abort, { once: true })
      const timer = setTimeout(abort, config.timeoutMs)
      try {
        const block = await search.retrieve(query, {
          signal: controller.signal,
          // The gate's environment check needs the agent's real tools. The
          // host reports them per call here; `availableTools` in the config
          // is the fallback for a host that reports none.
          ...(ctx?.availableTools?.length
            ? { availableTools: ctx.availableTools }
            : config.availableTools.length > 0
              ? { availableTools: config.availableTools }
              : {}),
        })
        return text(block || `No skill in the library matches "${query}". Proceed without one.`)
      } catch (error) {
        logger?.warn?.('[skillsearch] skill_search failed', error)
        // A failed search is not a failed tool call: the agent should carry
        // on without a skill rather than treat this as an error to recover
        // from. Same rule as the auto path, where a failure costs the turn
        // its skills and nothing else.
        return text('Skill search is unavailable right now. Proceed without a skill.')
      } finally {
        clearTimeout(timer)
        signal?.removeEventListener('abort', abort)
      }
    },
  }
}
