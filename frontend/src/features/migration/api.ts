import { api } from '@/lib/api-client'

export type MigrationVerdict = 'migratable' | 'lossy' | 'skipped'

export type ObjectKind =
  | 'table'
  | 'view'
  | 'materialized_view'
  | 'function'
  | 'task'
  | 'pipe'
  | 'masking_policy'
  | 'row_access_policy'

export type SourceConnection = {
  id: string
  name: string
  host: string
  port: number
  username: string
  secret_ref: string
  comment: string
  created_at: string | null
  created_by: string | null
}

export type SourceConnectionList = {
  connections: SourceConnection[]
  count: number
}

export type SourceObject = {
  name: string
  kind: ObjectKind
  database: string
  detail: string | null
  extra: Record<string, string>
}

export type EnumerateResponse = {
  database: string
  objects: SourceObject[]
  count: number
}

export type DryRunItem = {
  name: string
  kind: ObjectKind
  verdict: MigrationVerdict
  reason: string
  detail: string | null
}

export type DryRunSummary = {
  migratable: number
  lossy: number
  skipped: number
}

export type DryRunResponse = {
  database: string
  items: DryRunItem[]
  summary: DryRunSummary
  engine_available: boolean
  engine_path: string | null
}

export type EngineStatus = {
  available: boolean
  configured_path: string | null
  resolved_path: string | null
  reason: string | null
}

export type MigrationCapabilities = {
  phase: string
  version: string
  phases: string[]
  write_operations: boolean
  execute_available: boolean
  execute_gate: { issue: string; name: string }
  mv_ddl_surface: string
}

export type PlanStepKind =
  | 'database'
  | 'table'
  | 'view'
  | 'materialized_view'
  | 'function'

export type PlanStep = {
  order: number
  kind: PlanStepKind
  object_name: string
  statement: string
  dropped_properties: string[]
}

export type BlockedObject = {
  name: string
  kind: ObjectKind
  reason: string
}

export type PlanResponse = {
  source_database: string
  target_database: string
  steps: PlanStep[]
  blocked: BlockedObject[]
  step_count: number
  execute_available: boolean
}

const BASE = '/migration'

export function fetchCapabilities() {
  return api.get<MigrationCapabilities>(`${BASE}/capabilities`)
}

export function fetchEngineStatus() {
  return api.get<EngineStatus>(`${BASE}/engine`)
}

export function fetchSources() {
  return api.get<SourceConnectionList>(`${BASE}/sources`)
}

export function createSource(input: {
  name: string
  host: string
  port: number
  username: string
  secret_ref?: string
  comment?: string
}) {
  return api.post<SourceConnection>(`${BASE}/sources`, input)
}

export function enumerateObjects(source: string, database: string) {
  return api.post<EnumerateResponse>(`${BASE}/enumerate`, { source, database })
}

export function dryRun(source: string, database: string, objects: string[] = []) {
  return api.post<DryRunResponse>(`${BASE}/dry-run`, {
    source,
    database,
    objects,
  })
}

export function fetchPlan(input: {
  source: string
  database: string
  target_database?: string
  objects?: string[]
  create_database?: boolean
}) {
  return api.post<PlanResponse>(`${BASE}/plan`, input)
}

export type ExecuteStepResult = {
  order: number
  kind: PlanStepKind
  object_name: string
  statement: string
  status: 'ok' | 'failed' | 'skipped'
  error: string | null
}

export type TableCopyResult = {
  table: string
  rows_exported: number
  rows_imported: number
  verified: boolean
  digest_match: boolean | null
  note: string
  errors: string[]
}

export type ExecuteResponse = {
  source_database: string
  target_database: string
  results: ExecuteStepResult[]
  blocked: BlockedObject[]
  succeeded: number
  failed: number
  skipped: number
  data: TableCopyResult[]
  rows_moved: number
}

export type PreflightCheck = {
  privilege: string
  reason: string
  satisfied: boolean
}

export type PreflightResponse = {
  source_database: string
  target_database: string
  database_step_planned: boolean
  checks: PreflightCheck[]
  missing: string[]
  storage_checked: boolean
  storage_ok: boolean | null
  storage_reason: string
  ok: boolean
}

export function runPreflight(input: {
  source: string
  database: string
  target_database?: string
  objects?: string[]
  create_database?: boolean
  include_data?: boolean
}) {
  return api.post<PreflightResponse>(`${BASE}/preflight`, input)
}

export function executeMigration(input: {
  source: string
  database: string
  target_database?: string
  objects?: string[]
  create_database?: boolean
  acknowledge_omissions: boolean
  confirmation?: string
  include_data?: boolean
  stage_connection?: string
}) {
  return api.post<ExecuteResponse>(`${BASE}/execute`, input)
}
