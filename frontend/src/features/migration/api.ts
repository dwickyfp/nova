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
