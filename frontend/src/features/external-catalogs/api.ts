import { api } from '@/lib/api-client'

export type CatalogType = 'hive' | 'iceberg'
export type MetastoreType = 'hms' | 'rest'

export type ExternalCatalog = {
  name: string
  type: string
  metastore_type: string | null
  metastore_uri: string | null
  storage_connection: string | null
  comment: string | null
  properties: Record<string, string>
  create_statement: string | null
  created_at: string | null
  created_by: string | null
}

export type ExternalCatalogList = {
  catalogs: ExternalCatalog[]
  count: number
}

export type CreateExternalCatalogInput = {
  name: string
  type: CatalogType
  metastore_type: MetastoreType
  metastore_uri: string
  storage_connection: string
  comment?: string
  properties?: Record<string, string>
}

export type AlterExternalCatalogInput = {
  metastore_uri?: string
  comment?: string
  properties?: Record<string, string>
}

const BASE = '/external-catalogs'

export function fetchExternalCatalogs() {
  return api.get<ExternalCatalogList>(BASE)
}

export function fetchExternalCatalog(name: string) {
  return api.get<ExternalCatalog>(`${BASE}/${name}`)
}

export function createExternalCatalog(input: CreateExternalCatalogInput) {
  return api.post<ExternalCatalog>(BASE, input)
}

export function alterExternalCatalog(
  name: string,
  input: AlterExternalCatalogInput
) {
  return api.patch<ExternalCatalog>(`${BASE}/${name}`, input)
}

export function deleteExternalCatalog(name: string) {
  return api.delete<{ success: boolean; message: string }>(`${BASE}/${name}`)
}

export function fetchCatalogTables(name: string, database: string) {
  return api.get<{ name: string; database: string }[]>(
    `${BASE}/${name}/databases/${database}/tables`
  )
}
