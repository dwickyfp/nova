import { api } from '@/lib/api-client'
import type { CatalogDetail } from '@/features/external-catalogs/api'
import type { SourceType } from './sources'

export type ConnectorType = SourceType

/**
 * A connection as the console shows it. The endpoint, the secret reference,
 * and any credential stay on the server: only the name, type, and whether
 * credentials are present come back.
 */
export type ConnectionSummary = {
  name: string
  connector_type: string
  /** `managed`: entered through Nova. `external`: a secret store reference. */
  credential_source: 'managed' | 'external' | string
  has_credentials: boolean
  owner: string | null
  comment: string | null
  catalogs: string[]
  created_at: string | null
  updated_at: string | null
}

export type ConnectionList = {
  connections: ConnectionSummary[]
  count: number
}

/** Write-only: sent once, stored encrypted, never returned. */
export type ConnectionCredentials = {
  username: string
  password: string
}

export type CreateConnectionInput = {
  name: string
  connector_type: ConnectorType
  endpoint: string
  comment?: string
} & (
  | { credentials: ConnectionCredentials; secret_ref?: undefined }
  | { secret_ref: string; credentials?: undefined }
)

export type CreateForeignCatalogInput = {
  name: string
  connection: string
  comment?: string
}

const BASE = '/catalog/connections'

export function fetchConnections() {
  return api.get<ConnectionList>(BASE)
}

export function createConnection(input: CreateConnectionInput) {
  return api.post<ConnectionSummary>(BASE, input)
}

export function setConnectionCredentials(name: string, credentials: ConnectionCredentials) {
  return api.put<ConnectionSummary>(
    `${BASE}/${encodeURIComponent(name)}/credentials`,
    credentials
  )
}

export function deleteConnection(name: string) {
  return api.delete<{ success: boolean; message: string }>(
    `${BASE}/${encodeURIComponent(name)}`
  )
}

export function createForeignCatalog(input: CreateForeignCatalogInput) {
  return api.post<CatalogDetail>('/catalog/foreign-catalogs', input)
}
