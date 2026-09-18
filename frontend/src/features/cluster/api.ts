import { api } from '@/lib/api-client'

export type FeMetrics = {
  query_total?: number
  query_success?: number
  query_err?: number
  slow_query?: number
  connection_total?: number
  query_latency_ms?: number
  query_latency_99th_ms?: number
  query_latency_95th_ms?: number
  query_begin_failed?: number
  query_internal_error?: number
}

export type FeMetricsResponse = {
  metrics: FeMetrics
}

export function fetchFeMetrics() {
  return api.get<FeMetricsResponse>('/monitoring/metrics/fe')
}

type QueryResponse = {
  success: boolean
  columns: string[]
  rows: unknown[][]
  error: string | null
}

/**
 * Node inventory comes from the engine's own SHOW statements through the
 * generic query endpoint, the same read-only path the docs/13 node-management
 * contract names. No new backend route is involved.
 */
export async function fetchNodes(kind: 'frontends' | 'backends'): Promise<NodeRow[]> {
  const statement = kind === 'frontends' ? 'SHOW FRONTENDS' : 'SHOW BACKENDS'
  const [result] = await api.post<QueryResponse[]>('/query/execute', {
    sql: statement,
  })
  if (!result?.success) {
    throw new Error(result?.error || `SHOW ${kind.toUpperCase()} failed`)
  }
  return parseNodeRows(result.columns, result.rows)
}

export type NodeRow = {
  host: string
  port: string
  role: string
  alive: boolean | null
  lastHeartbeat: string
  raw: Record<string, string>
}

const HOST_KEYS = ['Host', 'IP', 'Address']
const PORT_KEYS = ['HttpPort', 'Port', 'BePort', 'HeartbeatPort']
const ALIVE_KEYS = ['Alive', 'Status', 'State']
const HEARTBEAT_KEYS = ['LastHeartbeat', 'LastStartTime', 'LastSuccessReportTabletsTime']
const ROLE_KEYS = ['Role', 'NodeRole', 'Type']

function pick(record: Record<string, string>, keys: string[]) {
  for (const key of keys) {
    if (record[key]) return record[key]
  }
  return ''
}

/** Map a SHOW result set to node rows without assuming a fixed column order. */
export function parseNodeRows(columns: string[], rows: unknown[][]): NodeRow[] {
  const normalized = columns.map((column) => column.trim())
  return rows.map((row) => {
    const record: Record<string, string> = {}
    normalized.forEach((column, index) => {
      const value = row[index]
      record[column] = value == null ? '' : String(value)
    })
    const aliveRaw = pick(record, ALIVE_KEYS).toLowerCase()
    const alive =
      aliveRaw === ''
        ? null
        : aliveRaw === 'true' || aliveRaw === 'alive' || aliveRaw === 'ok'
    return {
      host: pick(record, HOST_KEYS),
      port: pick(record, PORT_KEYS),
      role: pick(record, ROLE_KEYS),
      alive,
      lastHeartbeat: pick(record, HEARTBEAT_KEYS),
      raw: record,
    }
  })
}

/** FE metric keys the cluster health view reads, in display order. */
export const FE_METRIC_KEYS: Array<keyof FeMetrics> = [
  'query_total',
  'query_success',
  'query_err',
  'slow_query',
  'connection_total',
  'query_latency_ms',
  'query_latency_95th_ms',
  'query_latency_99th_ms',
  'query_begin_failed',
  'query_internal_error',
]

export function hasAnyMetric(metrics: FeMetrics | undefined): boolean {
  if (!metrics) return false
  return FE_METRIC_KEYS.some((key) => typeof metrics[key] === 'number')
}

/** Success rate as a 0-1 ratio, or null when the totals are unusable. */
export function successRate(metrics: FeMetrics | undefined): number | null {
  const total = metrics?.query_total
  const success = metrics?.query_success
  if (typeof total !== 'number' || typeof success !== 'number' || total <= 0) {
    return null
  }
  return Math.min(1, Math.max(0, success / total))
}

/**
 * A counter that the engine reports but this build of the FE does not expose
 * reads as absent, not zero. Returning null keeps "no data" distinct from a
 * real zero in the UI.
 */
export function metricValue(
  metrics: FeMetrics | undefined,
  key: keyof FeMetrics
): number | null {
  const value = metrics?.[key]
  return typeof value === 'number' ? value : null
}

export function isHealthy(metrics: FeMetrics | undefined): boolean {
  return hasAnyMetric(metrics)
}

export function countAlive(nodes: NodeRow[]): { alive: number; total: number } {
  const total = nodes.length
  const alive = nodes.filter((node) => node.alive === true).length
  return { alive, total }
}
