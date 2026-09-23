import { api } from '@/lib/api-client'

/**
 * Read-only metadata the Agent Studio builders need: the databases a user can
 * pick, and the tables and columns inside one. Reuses the Database Explorer
 * endpoints rather than a second metadata path, so RBAC and caching are shared.
 */

export type CatalogInfo = {
  name: string
  type: string
  comment: string | null
  databases: string[]
}

/** The system database is Nova's own; it is never a valid agent/semantic target. */
export const EXCLUDED_DATABASES = ['NOVA_SYSTEM', 'information_schema', '_statistics_']

export type TableSummary = {
  name: string
  table_model: string | null
  engine: string | null
  row_count: number | null
  data_size: number | null
  create_time: string | null
}

export type DatabaseObjects = {
  database: string
  tables: TableSummary[]
  views: { name: string }[]
}

export type ColumnInfo = {
  name: string
  ordinal_position: number
  data_type: string
  column_type: string | null
  is_nullable: string | null
  column_key: string | null
  column_default: string | null
}

export type TableDetail = {
  database: string
  table: string
  columns: ColumnInfo[]
}

export const metadataApi = {
  /**
   * Databases on the internal catalog, excluding Nova's own and the engine's
   * information schemas. Sorted, so the dropdown order is stable.
   */
  async listDatabases(includeSystem = false): Promise<string[]> {
    const response = await api.get<{ catalogs: CatalogInfo[] }>('/explorer/catalogs')
    const internal = response.catalogs.find((c) => c.name === 'default_catalog')
    const databases = internal?.databases ?? []
    return databases
      .filter((db) => includeSystem
        ? !['information_schema', '_statistics_'].includes(db)
        : !EXCLUDED_DATABASES.includes(db))
      .sort((a, b) => a.localeCompare(b))
  },

  listTables(database: string): Promise<DatabaseObjects> {
    return api.get<DatabaseObjects>(`/explorer/databases/${encodeURIComponent(database)}`)
  },

  getTableDetail(database: string, table: string): Promise<TableDetail> {
    return api.get<TableDetail>(
      `/explorer/databases/${encodeURIComponent(database)}/tables/${encodeURIComponent(table)}`
    )
  },
}
