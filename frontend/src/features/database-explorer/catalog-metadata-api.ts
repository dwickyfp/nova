import { api } from '@/lib/api-client'

/** Nova Catalog business metadata on securable ids (`/api/v1/catalog`). */

export interface CatalogTag {
  key: string
  value: string | null
  updated_by?: string | null
  updated_at?: string | null
}

export interface SecurableSummary {
  id: string
  securable_type: string
  full_name: string
  catalog_name: string
  database_name: string | null
  object_name: string | null
  column_name: string | null
  data_type: string | null
  status: 'live' | 'dropped'
  dropped_at: string | null
  comment: string | null
  engine_comment: string | null
  owner_name: string | null
  owner_type: string | null
  classification: string | null
  tags: CatalogTag[]
}

export interface SecurableDetail extends SecurableSummary {
  first_seen_at: string | null
  parent_id: string | null
  can_edit: boolean
  can_set_owner: boolean
  columns: SecurableSummary[]
  used_by: Array<{ owner_type: string; owner_id: string; label: string }>
}

export interface SearchHit extends SecurableSummary {
  matched: string[]
}

export interface SearchResponse {
  query: string | null
  tag: string | null
  results: SearchHit[]
  count: number
  truncated: boolean
}

export interface LineageNode {
  id: string
  full_name: string
  status: 'live' | 'dropped'
  statement_kind: string
  last_seen_at: string | null
}

export interface LineageResponse {
  id: string
  full_name: string
  upstream: LineageNode[]
  downstream: LineageNode[]
}

export const CLASSIFICATIONS = ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED'] as const

export const TAG_KEY_PATTERN = /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/

const BASE = '/catalog'

/**
 * Split a search box value into words and a tag filter: `tag:pii` or
 * `tag:pii=email` anywhere in the text becomes the tag; the rest is the query.
 */
export function parseCatalogQuery(text: string): { q: string; tag: string } {
  let tag = ''
  const words: string[] = []
  for (const part of text.trim().split(/\s+/)) {
    if (!part) continue
    if (/^tag:/i.test(part) && part.length > 4) tag = part.slice(4)
    else words.push(part)
  }
  return { q: words.join(' '), tag }
}

/** `key` or `key=value` typed into a tag box, or null when the key is invalid. */
export function parseTagInput(text: string): { key: string; value: string | null } | null {
  const [rawKey, ...rest] = text.split('=')
  const key = rawKey.trim()
  if (!TAG_KEY_PATTERN.test(key)) return null
  const value = rest.join('=').trim()
  return { key, value: value || null }
}

export function searchCatalog(params: { q?: string; tag?: string; limit?: number }, signal?: AbortSignal) {
  const query = new URLSearchParams()
  if (params.q) query.set('q', params.q)
  if (params.tag) query.set('tag', params.tag)
  query.set('limit', String(params.limit ?? 20))
  return api.get<SearchResponse>(`${BASE}/search?${query.toString()}`, signal)
}

export function resolveSecurable(catalog: string, database: string, object: string) {
  const query = new URLSearchParams({ catalog, database, object })
  return api.get<SecurableDetail>(`${BASE}/objects/resolve?${query.toString()}`)
}

export function putTags(id: string, tags: Array<{ key: string; value: string | null }>) {
  return api.put<SecurableDetail>(`${BASE}/objects/${id}/tags`, { tags })
}

export function deleteTag(id: string, key: string) {
  return api.delete<SecurableDetail>(`${BASE}/objects/${id}/tags/${encodeURIComponent(key)}`)
}

export function annotateSecurable(
  id: string,
  changes: Partial<{
    comment: string | null
    owner_name: string | null
    owner_type: 'user' | 'role' | null
    classification: string | null
  }>,
) {
  return api.patch<SecurableDetail>(`${BASE}/objects/${id}/annotations`, changes)
}

export function getLineage(id: string) {
  return api.get<LineageResponse>(`${BASE}/objects/${id}/lineage`)
}
