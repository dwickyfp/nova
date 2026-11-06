import { api } from '@/lib/api-client'

/** A catalog the caller can see, described by the Nova Catalog store. */
export type CatalogSummary = {
  name: string
  /** `native` for the engine's internal catalog, `external` otherwise. */
  kind: string
  catalog_type: string
  /** Created through Nova (only these can be dropped from the UI). */
  managed: boolean
  /** Who first recorded it: `nova`, or `engine` for one made in SQL. */
  source: string
  comment: string | null
  status: string | null
  last_sync_at: string | null
  last_sync_error: string | null
  databases: string[]
}

/** Catalog detail. Deliberately has no credential-bearing field. */
export type CatalogDetail = CatalogSummary & {
  metastore_type: string | null
  metastore_uri: string | null
  storage_connection: string | null
  properties: Record<string, string>
  create_statement: string | null
  created_at: string | null
  created_by: string | null
}

export type CatalogList = {
  catalogs: CatalogSummary[]
  count: number
}

export type SyncRun = {
  id: string
  catalog_name: string
  trigger: string
  status: string
  started_at: string | null
  finished_at: string | null
  counts: Record<string, number>
  error: string | null
}

export type AlterCatalogInput = {
  metastore_uri?: string
  comment?: string
  properties?: Record<string, string>
}

const BASE = '/catalog/catalogs'

export function fetchCatalogs() {
  return api.get<CatalogList>(BASE)
}

export function fetchCatalog(name: string) {
  return api.get<CatalogDetail>(`${BASE}/${encodeURIComponent(name)}`)
}

export function alterCatalog(name: string, input: AlterCatalogInput) {
  return api.patch<CatalogDetail>(`${BASE}/${encodeURIComponent(name)}`, input)
}

export function deleteCatalog(name: string) {
  return api.delete<{ success: boolean; message: string }>(
    `${BASE}/${encodeURIComponent(name)}`
  )
}

/** Re-sync one catalog's metadata, or every catalog when `name` is omitted. */
export function refreshCatalogMetadata(name?: string) {
  const path = name ? `${BASE}/${encodeURIComponent(name)}/refresh` : `${BASE}/refresh`
  return api.post<{ runs: SyncRun[] }>(path)
}

export function fetchSyncRuns(name: string) {
  return api.get<{ runs: SyncRun[] }>(`${BASE}/${encodeURIComponent(name)}/sync-runs`)
}

export function fetchCatalogObjects(name: string, database: string) {
  return api.get<{ object_name: string; object_type: string; comment: string | null }[]>(
    `${BASE}/${encodeURIComponent(name)}/databases/${encodeURIComponent(database)}/objects`
  )
}

/** Roles that may refresh catalog metadata (mirrors the backend gate). */
export const SECURITY_ADMIN_ROLES = new Set(['ACCOUNTADMIN', 'SECURITYADMIN', 'security_admin'])
