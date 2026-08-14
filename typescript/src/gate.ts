/**
 * Relevance gate: an LLM pass that drops candidates this agent cannot run.
 *
 * Fusion ranks by position, so a source's best hit reaches the shortlist
 * however weakly it matched. This is where those go. The prompt also carries
 * an environment check that ranking cannot do at all: a skill whose body
 * assumes a vendor API, a `{baseDir}` placeholder, or a slash dispatcher is
 * unusable here no matter how well it matches the words, and injecting it
 * costs the turn context and sends the model down a path that dead-ends.
 *
 * Every failure keeps the top-ranked candidates rather than dropping to none.
 * An empty list is a decision the model is asked to make explicitly; it is
 * never the result of the gate itself breaking.
 *
 * @module
 */

import { withTimeout } from './deadline.js'
import { extractJsonObject } from './replies.js'
import type { RouterHit } from './types.js'

/** Characters of body shown per candidate. Enough to judge, cheap to send. */
const BODY_EXCERPT_CHARS = 300

/** How the gate reaches a model. Implemented by the plugin over `ctx.llm`. */
export interface GateModel {
  complete(prompt: string, options: { signal?: AbortSignal | undefined }): Promise<string>
}

/** Selection bounds and the deadline for one gate call. */
export interface GateOptions {
  /** Upper bound on selections. The model may return fewer, including none. */
  readonly maxSelect?: number
  /** Candidates kept when the gate cannot answer. */
  readonly fallbackTopK?: number
  /** Deadline for the call. This runs before the model replies to the user. */
  readonly timeoutMs?: number
}

/** The relevance and executability gate, backed by one model call. */
export class LLMGateFilter {
  private readonly model: GateModel
  private readonly maxSelect: number
  private readonly fallbackTopK: number
  private readonly timeoutMs: number

  constructor(model: GateModel, options: GateOptions = {}) {
    this.model = model
    this.maxSelect = options.maxSelect ?? 2
    this.fallbackTopK = options.fallbackTopK ?? this.maxSelect
    this.timeoutMs = options.timeoutMs ?? 20_000
  }

  /**
   * Narrow `candidates` to the skills worth injecting for `task`.
   *
   * `availableTools` enables the environment check; without it the gate still
   * judges relevance. Returns the top `fallbackTopK` candidates on timeout,
   * transport failure, or an unparseable reply — a broken gate degrades to
   * unfiltered retrieval rather than to silence.
   *
   * @param task - the user's words, unrewritten: the gate judges the real ask.
   * @param candidates - the fused pool, best first.
   * @param availableTools - tools this agent can call, enabling the hard rule.
   * @param signal - aborts the call when the turn is cancelled.
   * @returns the kept candidates, at most `maxSelect`, possibly empty.
   */
  async filter(
    task: string,
    candidates: readonly RouterHit[],
    availableTools?: readonly string[],
    signal?: AbortSignal,
  ): Promise<RouterHit[]> {
    if (candidates.length === 0) return []
    const { catalog, byId } = buildCatalog(candidates)
    const prompt = this.buildPrompt(task, catalog, availableTools)

    let content: string
    try {
      content = await withTimeout(
        this.model.complete(prompt, { signal }),
        this.timeoutMs,
      )
    } catch {
      return candidates.slice(0, this.fallbackTopK)
    }

    let selectedIds: string[]
    try {
      selectedIds = parseResponse(content)
    } catch {
      return candidates.slice(0, this.fallbackTopK)
    }

    const selected: RouterHit[] = []
    for (const id of selectedIds.slice(0, this.maxSelect)) {
      const hit = byId.get(id)
      if (hit) selected.push(hit)
    }
    return selected
  }

  private buildPrompt(
    task: string,
    catalog: string,
    availableTools?: readonly string[],
  ): string {
    let toolsBlock = ''
    if (availableTools && availableTools.length > 0) {
      const names = [...new Set(availableTools)].sort().join(', ')
      toolsBlock =
        '# Agent Tools\n\n' +
        `The agent's ONLY available tools are: ${names}.\n\n` +
        '**Hard rule**: a skill is NOT relevant if its workflow ' +
        'requires any tool, file, or environment that the agent ' +
        'lacks. Inspect EACH candidate\'s body excerpt and ' +
        'exclude it if you see any of:\n' +
        '- A specific external API / SDK / vendor ' +
        '(e.g. ``nyne-deep-research``, ``musicbrainz``, ' +
        '``bandcamp``, ``-api`` suffix, vendor wrapper).\n' +
        '- Environment placeholders or paths that won\'t exist ' +
        'in this runtime: ``${CLAUDE_PLUGIN_ROOT}``, ' +
        '``{baseDir}``, ``{overrides}``, ``.aiwg/``, ``${SKILL_HOME}``, ' +
        '``$ARGUMENTS`` as a slot, references to ' +
        '``${...}`` template variables.\n' +
        '- Slash-command triggers (e.g. ``/research-query``) — ' +
        'the agent has no slash dispatcher.\n' +
        '- ``Parent agent:`` style multi-agent framework ' +
        'assumptions, or references to other SKILL.md files ' +
        'under unspecified directories.\n' +
        '- Agent personas, role-play, creative writing, content ' +
        'generation — these are not research procedures.\n\n' +
        '**Only include** skills whose body describes a ' +
        'self-contained procedure that the agent can execute ' +
        'with just the listed tools (e.g. query-writing ' +
        'strategies, verification workflows, ' +
        'search-result interpretation).\n\n'
    }
    return (
      'You are a skill selector for an autonomous agent.\n\n' +
      `# Task\n\n${task}\n\n` +
      toolsBlock +
      `# Candidate Skills\n\n${catalog}\n\n` +
      '# Instructions\n\n' +
      '1. **Plan**: briefly think about what the task requires ' +
      'and which sequence of available-tool calls would achieve it.\n' +
      '2. **Filter**: for EACH candidate skill, ask ' +
      '"can the agent execute this skill\'s workflow using only the ' +
      'available tools above?" If no, drop it — no matter how ' +
      'topically relevant.\n' +
      '3. **Match**: among the survivors, a skill is relevant ONLY ' +
      'if it provides a procedure or strategy directly useful for ' +
      'a core part of your plan. Vague topical overlap is not enough.\n' +
      `4. **Decide**: select AT MOST ${this.maxSelect} skill(s). ` +
      'If no skill survives both the tool check and the relevance ' +
      'check, you MUST return an empty list. Selecting an ' +
      'irrelevant or unexecutable skill is strictly worse than ' +
      'selecting none.\n\n' +
      'Return ONLY a JSON object on a single line:\n' +
      '{"plan": "1-sentence plan", "skills": ["qualified_id_1"]}\n\n' +
      'Or when nothing applies: {"plan": "...", "skills": []}\n\n' +
      'Use the EXACT qualified_id strings from the candidate list above.'
    )
  }
}

function buildCatalog(candidates: readonly RouterHit[]): {
  catalog: string
  byId: Map<string, RouterHit>
} {
  const lines: string[] = []
  const byId = new Map<string, RouterHit>()
  for (const h of candidates) {
    const sid = h.qualifiedId
    let desc = ((h.meta.description as string | undefined) ?? '').trim().replace(/\n/g, ' ')
    if (!desc) desc = '(no description)'
    if (desc.length > 200) desc = `${desc.slice(0, 197)}...`
    const body = h.content.trim()
    const excerpt = body.split(/\s+/).join(' ').slice(0, BODY_EXCERPT_CHARS) || '(no body)'
    lines.push(`- ${sid}: ${desc}\n  Body excerpt: ${excerpt}`)
    byId.set(sid, h)
  }
  return { catalog: lines.join('\n'), byId }
}

/**
 * Pull the selection out of a model reply.
 *
 * Tolerates a reasoning block and a fenced or bare JSON object, because models
 * asked for "only JSON" routinely send one anyway. Throws when no object with
 * a `skills` array survives, which the caller turns into the unfiltered
 * fallback.
 */
function parseResponse(content: string): string[] {
  const data = extractJsonObject(content)
  if (data === undefined) throw new Error('no JSON object in reply')
  const skills = data.skills
  if (!Array.isArray(skills)) throw new Error("missing 'skills' array")
  return skills.filter((s): s is string => typeof s === 'string' && s.length > 0)
}

