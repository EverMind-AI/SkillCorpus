/**
 * A remote skill catalog, reached over HTTP.
 *
 * Three calls, used at three stages, because each costs more than the last:
 * search returns metadata only, fetching a body is one request per candidate
 * the gate might keep, and installing downloads a zip. Discovery therefore
 * runs on every turn, bodies on the shortlist, and installs only on what the
 * gate selected.
 *
 * The catalog answers in a uniform envelope; a reply is successful only when
 * `status` is 0. Any failure throws, and the router turns that into an empty
 * result for this source rather than failing the whole fan-out.
 *
 * @module
 */

import type { RouterHit, SearchOptions, SkillSource } from './types.js'

/** One catalog entry as the search endpoint returns it. */
interface CatalogItem {
  readonly id?: string
  readonly skill_id?: string
  readonly name?: string
  readonly description?: string
  readonly category?: string
  readonly quality_score?: number
  readonly install_count?: number
  readonly score_safety?: number
  readonly tags?: readonly string[]
}

/** Transport settings for one catalog deployment. */
export interface HubClientOptions {
  readonly apiKey?: string
  readonly timeoutMs?: number
  /** Download tag the catalog records. It validates against a fixed set. */
  readonly source?: 'raven' | 'everme' | 'cli' | 'web'
}

/** HTTP client for the catalog's three endpoints. */
export class SkillHubClient {
  private readonly base: string
  private readonly apiKey: string | undefined
  private readonly timeoutMs: number
  private readonly source: string

  constructor(endpoint: string, options: HubClientOptions = {}) {
    this.base = endpoint.replace(/\/+$/, '')
    this.apiKey = options.apiKey
    this.timeoutMs = options.timeoutMs ?? 2000
    this.source = options.source ?? 'cli'
  }

  /**
   * Search the catalog. Metadata only — no bodies.
   * @param query - the search text, sent as `q`.
   * @param signal - aborts the request when the turn is cancelled.
   * @returns the entries the catalog matched, in its own order.
   */
  async search(query: string, signal?: AbortSignal): Promise<CatalogItem[]> {
    const url = `${this.base}/openapi/v1/skills?q=${encodeURIComponent(query)}`
    const result = await this.getJson(url, signal)
    const items = (result as { items?: unknown }).items
    return Array.isArray(items) ? (items as CatalogItem[]) : []
  }

  /**
   * Fetch one skill's full record.
   * @param id - the catalog's own id for the skill.
   * @param signal - aborts the request when the turn is cancelled.
   * @returns the record, including `skill_md` when the catalog carries it.
   */
  async get(id: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    const url = `${this.base}/openapi/v1/skills/${encodeURIComponent(id)}`
    return (await this.getJson(url, signal)) as Record<string, unknown>
  }

  /**
   * Build the bundle URL for a caller that will download and extract it.
   * @param id - the catalog's own id for the skill.
   * @returns the download URL, tagged with this client's `source`.
   */
  downloadUrl(id: string): string {
    return `${this.base}/openapi/v1/skills/${encodeURIComponent(id)}/download?source=${this.source}`
  }

  private async getJson(url: string, signal?: AbortSignal): Promise<unknown> {
    const controller = new AbortController()
    const timer = setTimeout(() => { controller.abort() }, this.timeoutMs)
    const onAbort = () => { controller.abort() }
    signal?.addEventListener('abort', onAbort, { once: true })
    try {
      const headers: Record<string, string> = { 'X-Request-ID': randomId() }
      if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`
      const res = await fetch(url, { headers, signal: controller.signal })
      if (!res.ok) throw new Error(`catalog returned HTTP ${res.status}`)
      const envelope = (await res.json()) as { status?: number; error?: string; result?: unknown }
      if (envelope.status !== 0) {
        throw new Error(`catalog error ${envelope.error ?? 'unknown'} (status ${envelope.status})`)
      }
      return envelope.result ?? {}
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
    }
  }
}

/** The remote catalog as a fusion source, ranked by its own quality score. */
export class HubSkillSource implements SkillSource {
  readonly name = 'hub'
  weight: number

  private readonly client: SkillHubClient
  private readonly minSafety: number

  constructor(
    client: SkillHubClient,
    options: { weight?: number; minSafety?: number } = {},
  ) {
    this.client = client
    this.weight = options.weight ?? 0.85
    this.minSafety = options.minSafety ?? 0.7
  }

  /**
   * Rank by the catalog's own quality score, carrying no bodies.
   *
   * Entries missing an id or a name are skipped: without both, a hit cannot be
   * fetched later or shown to the model now.
   */
  async search(query: string, options: SearchOptions, k: number): Promise<RouterHit[]> {
    const items = await this.client.search(query, options.signal)
    const hits: RouterHit[] = []
    for (const item of items.slice(0, k)) {
      const id = item.id
      const name = item.name
      if (!id || !name) continue
      // Per-skill safety lives in the detail response, so this only bites on
      // deployments whose catalog includes it.
      if (item.score_safety !== undefined && item.score_safety < this.minSafety) continue
      hits.push({
        qualifiedId: `hub/${id}`,
        name,
        content: '',
        score: item.quality_score ?? 0,
        meta: {
          source: 'hub',
          id,
          skillId: item.skill_id,
          description: item.description,
          category: item.category,
          qualityScore: item.quality_score,
          installCount: item.install_count,
        },
      })
    }
    return hits
  }
}

function randomId(): string {
  return Array.from({ length: 4 }, () =>
    Math.floor(Math.random() * 0xffffffff)
      .toString(16)
      .padStart(8, '0'),
  ).join('')
}
