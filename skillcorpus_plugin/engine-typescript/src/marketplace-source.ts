/** Public ClawHub and skillhub.cn search/download adapters. */
import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { bundleRoot, extractBundle } from './bundle.js'
import { compactCatalogQuery } from './relevance.js'
import type { RouterHit, SearchOptions, SkillSource } from './types.js'

export type MarketplaceKind = 'clawhub' | 'skillhub_cn'

export interface MarketplaceItem {
  id: string
  slug: string
  name: string
  description: string
  score: number
  owner?: string
  version?: string
  suspicious?: boolean
  installable?: boolean
  tags?: string[]
}

export class MarketplaceClient {
  readonly kind: MarketplaceKind
  private readonly base: string
  private readonly cacheDir: string
  private readonly timeoutMs: number
  private readonly downloadTimeoutMs: number

  constructor(kind: MarketplaceKind, endpoint: string, options: {
    cacheDir: string; timeoutMs?: number; downloadTimeoutMs?: number
  }) {
    this.kind = kind
    this.base = endpoint.replace(/\/+$/, '')
    this.cacheDir = options.cacheDir
    this.timeoutMs = options.timeoutMs ?? 5000
    this.downloadTimeoutMs = options.downloadTimeoutMs ?? 30_000
  }

  async search(query: string, signal?: AbortSignal, limit = 2): Promise<MarketplaceItem[]> {
    return this.kind === 'clawhub'
      ? this.searchClawHub(query, signal, limit)
      : this.searchSkillHubCn(query, signal, limit)
  }

  async install(hit: RouterHit, signal?: AbortSignal): Promise<{ dir: string; body: string }> {
    const slug = String(hit.meta.slug ?? hit.meta.id)
    const owner = String(hit.meta.owner ?? '')
    const version = String(hit.meta.version ?? 'v0')
    const key = `${this.kind}-${owner ? `${owner}_` : ''}${slug}@${version}`.replace(/[^A-Za-z0-9_.@-]+/g, '_')
    const destination = join(this.cacheDir, key)
    if (!existsSync(destination)) {
      const archive = await this.download(slug, owner, version, signal)
      await extractBundle(archive, destination)
    }
    const dir = await bundleRoot(destination)
    const skillMd = await readFile(join(dir, 'SKILL.md'), 'utf8')
    return { dir, body: stripFrontmatter(skillMd) }
  }

  private async searchClawHub(query: string, signal: AbortSignal | undefined, limit: number) {
    const url = new URL(`${this.base}/api/v1/search`)
    url.searchParams.set('q', query)
    url.searchParams.set('limit', String(limit))
    url.searchParams.set('nonSuspiciousOnly', 'true')
    const payload = await this.json(url, signal) as { results?: Record<string, unknown>[] }
    return (payload.results ?? []).flatMap(raw => {
      const slug = String(raw.slug ?? '')
      const native = raw.native as { skill?: Record<string, unknown> } | undefined
      const skill = native?.skill
      const trust = raw.trust as Record<string, unknown> | undefined
      if (!slug || trust?.visibility === 'blocked' || trust?.installability === 'blocked') return []
      return [{
        id: String(raw.id ?? slug), slug, name: String(raw.displayName ?? slug),
        description: String(raw.summary ?? skill?.summary ?? ''), score: Number(raw.score ?? 0),
        owner: String(raw.ownerHandle ?? ''), version: String(raw.version ?? skill?.latestVersionId ?? 'v0'),
        suspicious: skill?.isSuspicious === true, installable: trust?.installability == null || trust.installability === 'installable',
        tags: Array.isArray(skill?.topics) ? skill.topics.map(String) : [],
      }]
    })
  }

  private async searchSkillHubCn(query: string, signal: AbortSignal | undefined, limit: number) {
    const url = new URL(`${this.base}/api/skills`)
    url.searchParams.set('keyword', query)
    url.searchParams.set('sortBy', 'score')
    url.searchParams.set('order', 'desc')
    url.searchParams.set('page', '1')
    url.searchParams.set('pageSize', String(limit))
    const payload = await this.json(url, signal) as { code?: number; data?: { skills?: Record<string, unknown>[] } }
    if (payload.code !== 0) throw new Error('skillhub.cn search failed')
    return (payload.data?.skills ?? []).flatMap(raw => {
      const slug = String(raw.slug ?? '')
      if (!slug || malicious(raw.securityReports)) return []
      const namespace = raw.namespace as Record<string, unknown> | undefined
      return [{ id: String(namespace?.canonicalName ?? slug), slug,
        name: String(raw.name ?? slug), description: String(raw.description_zh ?? raw.description ?? ''),
        score: Number(raw.score ?? 0), owner: String(raw.ownerName ?? namespace?.handle ?? ''),
        version: String(raw.version ?? 'v0'), installable: true }]
    })
  }

  private async download(slug: string, owner: string, version: string, signal?: AbortSignal) {
    const url = new URL(`${this.base}/api/v1/download`)
    url.searchParams.set('slug', slug)
    if (this.kind === 'clawhub' && owner) url.searchParams.set('ownerHandle', owner)
    if (this.kind === 'skillhub_cn' && version !== 'v0') url.searchParams.set('version', version)
    url.searchParams.set('source', this.kind === 'skillhub_cn' ? 'dsh' : 'cli')
    return this.bytes(url, signal, this.downloadTimeoutMs)
  }

  private async json(url: URL, signal?: AbortSignal): Promise<unknown> {
    const bytes = await this.bytes(url, signal, this.timeoutMs)
    return JSON.parse(bytes.toString('utf8'))
  }

  private async bytes(url: URL, signal: AbortSignal | undefined, timeoutMs: number): Promise<Buffer> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const abort = () => controller.abort()
    if (signal?.aborted) controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    try {
      const response = await fetch(url, { signal: controller.signal })
      if (!response.ok) throw new Error(`${this.kind} returned HTTP ${response.status}`)
      return Buffer.from(await response.arrayBuffer())
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
    }
  }
}

export class MarketplaceSkillSource implements SkillSource {
  readonly name: MarketplaceKind
  weight: number
  constructor(readonly client: MarketplaceClient, options: { weight?: number } = {}) {
    this.name = client.kind
    this.weight = options.weight ?? 0.75
  }
  async search(query: string, options: SearchOptions, k: number): Promise<RouterHit[]> {
    const items = await this.client.search(compactCatalogQuery(query), options.signal, Math.min(2, k))
    return items.filter(item => !item.suspicious && item.installable !== false).slice(0, Math.min(2, k)).map(item => ({
      qualifiedId: `${this.name}/${item.id}`, name: item.name, content: '', score: item.score,
      meta: { source: this.name, id: item.id, slug: item.slug, owner: item.owner,
        version: item.version, description: item.description, tags: item.tags },
    }))
  }
}

function malicious(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  return Object.values(value).some(report => report && typeof report === 'object'
    && ['malicious', 'suspicious'].includes(String((report as Record<string, unknown>).status)))
}

function stripFrontmatter(text: string): string {
  if (!text.startsWith('---')) return text
  const end = text.indexOf('\n---', 3)
  return end < 0 ? text : text.slice(end + 4).replace(/^\n+/, '')
}
