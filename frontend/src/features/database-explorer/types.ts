export type ExplorerNodeType =
  | 'catalog'
  | 'database'
  | 'group'
  | 'table'
  | 'view'
  | 'materialized_view'
  | 'function'
  | 'pipe'
  | 'stage'
  | 'task'
  | 'entity'
  | 'semantic_view'
  | 'feature_view'

export type ExplorerNode = {
  id: string
  label: string
  type: ExplorerNodeType
  path: string[]
  metadata: Array<{ label: string; value: string }>
  children?: ExplorerNode[]
  /** Database name for lazy-load nodes */
  database?: string
  /** Catalog a database node belongs to; external databases need this to list tables */
  catalog?: string
}

// API response types
export type CatalogInfo = {
  name: string
  type: string
  comment: string | null
  databases: string[]
}

export type CatalogsResponse = {
  catalogs: CatalogInfo[]
}

export type TableSummary = {
  name: string
  table_model: string | null
  engine: string | null
  row_count: number | null
  data_size: number | null
  create_time: string | null
}

export type ViewSummary = {
  name: string
  definer: string | null
  is_updatable: string | null
}

export type MVSummary = {
  name: string
  refresh_type: string | null
  is_active: boolean | null
  last_refresh_state: string | null
  table_rows: number | null
  query_rewrite_status: string | null
}

export type FunctionSummary = {
  name: string
  routine_type: string | null
  definer: string | null
  created: string | null
}

export type PipeSummary = {
  name: string
  state: string | null
  target_table: string | null
  load_status: string | null
  last_error: string | null
  created_time: string | null
}

export type PipeDetailResponse = {
  name: string
  database: string
  pipe_id: number | null
  state: string | null
  target_table: string | null
  properties: Record<string, string>
  load_status: string | null
  last_error: string | null
  created_time: string | null
  create_ddl: string | null
}

export type ViewDetailResponse = {
  name: string
  database: string
  definition: string | null
  definer: string | null
  security_type: string | null
  is_updatable: string | null
  create_ddl: string | null
}

export type MVDetailResponse = {
  name: string
  database: string
  definition: string | null
  refresh_type: string | null
  is_active: string | null
  inactive_reason: string | null
  task_name: string | null
  last_refresh_state: string | null
  last_refresh_error_message: string | null
  last_refresh_start_time: string | null
  last_refresh_finished_time: string | null
  last_refresh_duration: number | null
  table_rows: number | null
  query_rewrite_status: string | null
  creator: string | null
}

export type FunctionDetailResponse = {
  name: string
  database: string
  routine_type: string | null
  definition: string | null
  definer: string | null
  created: string | null
  last_altered: string | null
  is_deterministic: string | null
  sql_data_access: string | null
  comment: string | null
  signature: string | null
  return_type: string | null
  function_type: string | null
  properties: string | null
  create_ddl: string | null
}

export type StageSummary = {
  name: string
  storage_connection: string | null
  base_prefix: string | null
  created_at: string | null
}

export type TaskSummary = {
  id: string
  name: string
  schedule_kind: string | null
  schedule_expr: string | null
  timezone: string | null
  overlap_policy: string | null
}

export type EntitySummary = {
  id: string
  name: string
  schema_name: string
  relation: string
  key_columns: string[]
}

export type SemanticViewSummary = {
  id: string
  name: string
  schema_name: string
  status: string
  active_version: number | null
}

export type FeatureViewSummary = {
  name: string
  entity_id: string
  status: string
  active_version: number | null
}

export type DatabaseObjectsResponse = {
  database: string
  tables: TableSummary[]
  views: ViewSummary[]
  materialized_views: MVSummary[]
  functions: FunctionSummary[]
  pipes: PipeSummary[]
  stages: StageSummary[]
  tasks: TaskSummary[]
  entities: EntitySummary[]
  semantic_views: SemanticViewSummary[]
  feature_views: FeatureViewSummary[]
  summary: Record<string, number>
}

export type ColumnInfo = {
  name: string
  ordinal_position: number
  data_type: string
  column_type: string | null
  is_nullable: string | null
  column_key: string | null
  column_default: string | null
  extra: string | null
  column_comment: string | null
  numeric_precision: number | null
  numeric_scale: number | null
  character_maximum_length: number | null
}

export type TableProperties = {
  table_model: string | null
  primary_key: string | null
  partition_key: string | null
  distribute_key: string | null
  distribute_type: string | null
  distribute_bucket: string | number | null
  sort_key: string | null
  properties: Record<string, string>
  create_ddl: string | null
}

export type PartitionInfo = {
  name: string
  partition_id: number | null
  partition_key: string | null
  partition_value: string | null
  row_count: number | null
  data_size: number | null
  storage_size: number | null
  buckets: number | null
  replication_num: number | null
  visible_version: number | null
  data_version: number | null
}

export type TableDetailResponse = {
  name: string
  database: string
  columns: ColumnInfo[]
  properties: TableProperties
  partitions: PartitionInfo[]
  row_count: number | null
  data_size: number | null
}
