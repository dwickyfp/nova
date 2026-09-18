import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  FE_METRIC_KEYS,
  countAlive,
  fetchFeMetrics,
  fetchNodes,
  hasAnyMetric,
  metricValue,
  parseNodeRows,
  successRate,
} from './api'

const fetchMock = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({}),
  })
})

afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
})

describe('cluster monitor API', () => {
  it('fetches FE metrics from the monitoring endpoint', async () => {
    await fetchFeMetrics()

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/monitoring/metrics/fe')
    expect(fetchMock.mock.calls[0][1]?.method ?? 'GET').toBe('GET')
  })

  it('runs SHOW FRONTENDS through the query endpoint', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => [
        { success: true, columns: ['Host'], rows: [['fe0']], error: null },
      ],
    })

    const nodes = await fetchNodes('frontends')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/query/execute')
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string).sql).toBe(
      'SHOW FRONTENDS'
    )
    expect(nodes).toHaveLength(1)
    expect(nodes[0].host).toBe('fe0')
  })

  it('runs SHOW BACKENDS through the query endpoint', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => [
        { success: true, columns: ['Host'], rows: [['be0']], error: null },
      ],
    })

    await fetchNodes('backends')

    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string).sql).toBe(
      'SHOW BACKENDS'
    )
  })

  it('surfaces the engine error when SHOW fails', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => [
        { success: false, columns: [], rows: [], error: 'not authorized' },
      ],
    })

    await expect(fetchNodes('backends')).rejects.toThrow('not authorized')
  })
})

describe('node row mapping', () => {
  it('maps columns by name, not position', () => {
    const rows = parseNodeRows(
      ['LastHeartbeat', 'Alive', 'Host', 'HttpPort'],
      [['2026-01-01T00:00:00', 'true', '10.0.0.1', '8030']]
    )

    expect(rows[0].host).toBe('10.0.0.1')
    expect(rows[0].port).toBe('8030')
    expect(rows[0].alive).toBe(true)
    expect(rows[0].lastHeartbeat).toBe('2026-01-01T00:00:00')
  })

  it('treats a missing Alive column as unknown, not dead', () => {
    const rows = parseNodeRows(['Host'], [['10.0.0.2']])

    expect(rows[0].alive).toBeNull()
  })

  it('reads "false" as not alive', () => {
    const rows = parseNodeRows(['Host', 'Alive'], [['10.0.0.3', 'false']])

    expect(rows[0].alive).toBe(false)
  })

  it('counts alive nodes against the total', () => {
    const nodes = parseNodeRows(
      ['Host', 'Alive'],
      [
        ['a', 'true'],
        ['b', 'false'],
        ['c', 'true'],
      ]
    )

    expect(countAlive(nodes)).toEqual({ alive: 2, total: 3 })
  })
})

describe('cluster health derivation', () => {
  it('reports no metrics when the summary is empty', () => {
    expect(hasAnyMetric({})).toBe(false)
    expect(hasAnyMetric(undefined)).toBe(false)
  })

  it('reports metrics present when any key is numeric', () => {
    expect(hasAnyMetric({ query_total: 0 })).toBe(true)
  })

  it('computes success rate from total and success', () => {
    expect(successRate({ query_total: 100, query_success: 95 })).toBe(0.95)
  })

  it('returns null success rate when the total is zero or missing', () => {
    expect(successRate({ query_total: 0, query_success: 0 })).toBeNull()
    expect(successRate({ query_success: 5 })).toBeNull()
  })

  it('clamps a success count that exceeds the total', () => {
    expect(successRate({ query_total: 10, query_success: 15 })).toBe(1)
  })

  it('distinguishes an absent counter from a real zero', () => {
    expect(metricValue({ slow_query: 0 }, 'slow_query')).toBe(0)
    expect(metricValue({}, 'slow_query')).toBeNull()
    expect(metricValue({}, 'query_err')).toBeNull()
  })

  it('covers only the metric keys the UI is allowed to render', () => {
    expect(FE_METRIC_KEYS).toContain('query_total')
    expect(FE_METRIC_KEYS).toContain('query_latency_95th_ms')
  })
})
