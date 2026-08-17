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

import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { bundleRoot, extractBundle } from './bundle.js'
import type { RouterHit, SearchOptions, SkillSource } from './types.js'

/** Success markers a catalog may send beside `status: 0`. */
const OK_TOKENS = new Set(['ok', 'success'])

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
  /**
   * Deadline for one bundle download. Separate from `timeoutMs` because
   * that one is sized for a catalog query on the hot path and a bundle is
   * megabytes.
   */
  readonly downloadTimeoutMs?: number
  /**
   * Where extracted bundles live. Keyed `<slug>@<version>`, so a repeat
   * install is a stat. Without it, `install` refuses rather than guessing
   * at a writable directory.
   */
  readonly cacheDir?: string
  /**
   * Download-stats tag, not a free label: a catalog validates it against its
   * own fixed set and answers 422 for anything outside it. `cli` is the safe
   * default; change it only against a deployment whose set you know.
   */
  readonly source?: string
}

/** HTTP client for the catalog's three endpoints. */
export class SkillHubClient {
  private readonly base: string
  private readonly apiKey: string | undefined
  private readonly timeoutMs: number
  private readonly downloadTimeoutMs: number
  private readonly cacheDir: string | undefined
  private readonly source: string

  constructor(endpoint: string, options: HubClientOptions = {}) {
    this.base = endpoint.replace(/\/+$/, '')
    this.apiKey = options.apiKey
    this.timeoutMs = options.timeoutMs ?? 2000
    this.downloadTimeoutMs = options.downloadTimeoutMs ?? 30_000
    this.cacheDir = options.cacheDir
    this.source = options.source ?? 'cli'
  }

  /**
   * Download a skill's bundle and extract it, or reuse an extracted copy.
   *
   * @param id - the catalog's own id for the skill.
   * @param meta - the skill's record, when the caller already fetched it;
   *   `slug` and `version` from it form the cache key.
   * @param signal - aborts the download when the turn is cancelled.
   * @returns the directory the skill's own paths resolve against, and the
   *   body the catalog stores, when the record carried one.
   * @throws Error when no cache directory is configured, or the archive is
   *   unusable. The caller keeps the unresolved body either way.
   */
  async install(
    id: string,
    meta?: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<{ dir: string; skillMd: string }> {
    if (!this.cacheDir) throw new Error('no cache directory is configured for bundles')
    const record = meta ?? (await this.get(id, signal))
    const slug = String(record.slug ?? record.skill_id ?? id).replace(/\//g, '_')
    const version = String(record.version ?? 'v0')
    const destination = join(this.cacheDir, `${slug}@${version}`)

    if (!existsSync(destination)) {
      const archive = await this.download(id, signal)
      await extractBundle(archive, destination)
    }
    return {
      dir: await bundleRoot(destination),
      skillMd: typeof record.skill_md === 'string' ? record.skill_md : '',
    }
  }

  /**
   * Fetch one bundle's bytes.
   *
   * @param id - the catalog's own id for the skill.
   * @param signal - aborts the request when the turn is cancelled.
   * @returns the archive.
   */
  async download(id: string, signal?: AbortSignal): Promise<Buffer> {
    const controller = new AbortController()
    const timer = setTimeout(() => { controller.abort() }, this.downloadTimeoutMs)
    const onAbort = () => { controller.abort() }
    signal?.addEventListener('abort', onAbort, { once: true })
    try {
      const headers: Record<string, string> = {}
      if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`
      const response = await fetch(this.downloadUrl(id), { headers, signal: controller.signal })
      if (!response.ok) throw new Error(`catalog returned HTTP ${response.status} for a bundle`)
      return Buffer.from(await response.arrayBuffer())
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
    }
  }

  /**
   * Search the catalog. Metadata only — no bodies.
   * @param query - the search text, sent as `q`.
   * @param signal - aborts the request when the turn is cancelled.
   * @param limit - how many entries to ask the catalog for. Sent explicitly:
   *   the catalog's own default page may be smaller than the fan-out wants.
   * @returns the entries the catalog matched, in its own order.
   */
  async search(query: string, signal?: AbortSignal, limit = 20): Promise<CatalogItem[]> {
    const url =
      `${this.base}/openapi/v1/skills` +
      `?q=${encodeURIComponent(query)}&limit=${Math.max(1, Math.floor(limit))}`
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
      // Both fields, as the Raven client checks them: `status === 0` is the
      // authoritative signal and the string is accepted leniently, `ok` per
      // the original spec and `success` from other deployments. Checking
      // only the status accepted a reply the Python engine rejects, so one
      // catalog answered differently on different hosts.
      if (envelope.status !== 0 || !OK_TOKENS.has(envelope.error ?? '')) {
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
    const items = await this.client.search(query, options.signal, k)
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
