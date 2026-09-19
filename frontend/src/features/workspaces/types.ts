export type WorkspaceEntry = {
  id: string
  name: string
  parent_path: string
  path: string
  entry_type: 'file' | 'folder'
  size_bytes: number
}

export type WorkspaceTreeResponse = {
  root_name: string
  entries: WorkspaceEntry[]
  open_tabs: string[]
  active_tab: string | null
  sidebar_collapsed: boolean
  /** Added with the Stage B assistant field; absent until that lands. */
  assistant_collapsed?: boolean
  defaults: {
    database?: string | null
    schema?: string | null
    role?: string | null
  }
}

export type WorkspaceFileResponse = {
  entry: WorkspaceEntry
  content: string
}

export type QueryContextResponse = {
  roles: string[]
  databases: string[]
  schemas: string[]
  defaults: {
    database?: string | null
    schema?: string | null
    role?: string | null
  }
}

export type QueryResponse = {
  success: boolean
  columns: string[]
  rows: Array<Array<string | number | boolean | null>>
  row_count: number
  affected_rows: number
  elapsed_ms: number
  original_sql: string
  executed_sql: string
  warnings: string[]
  destructive?: boolean
  needs_confirmation?: boolean
  /** Engine or pipeline error text when `success` is false. */
  error?: string | null
}

export type SchemaResponse = {
  schemas: Array<{ name: string }>
}

export type SchemaTreeResponse = {
  database: string
  schema: string
  tables: Array<{ name: string; type: string }>
  views: Array<{ name: string; type: string }>
  materialized_views: Array<{ name: string; type: string }>
  stages: Array<{ id: string; name: string; type: string }>
}

export type WorkspaceTabState = {
  id: string
  title: string
  content: string
  savedContent: string
  database: string
  schema: string
  role: string
  loaded: boolean
}

export type HistoryItem = {
  query_id: string
  event_time: string
  sql_text: string
  status: string
  duration_ms: number | null
  rows_affected: number | null
  error_message: string | null
  file_id: string | null
  database_name: string | null
  schema_name: string | null
}

export type HistoryResponse = {
  items: HistoryItem[]
  total: number
}

export type FileVersion = {
  id: string
  entry_id: string
  version: number
  size_bytes: number
  etag: string | null
  created_at: string | null
}

export type FileVersionsResponse = {
  versions: FileVersion[]
}

export type FileVersionResponse = {
  version: FileVersion
  content: string
}
