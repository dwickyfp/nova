import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { NativeChart } from './native-chart'
import '@/styles/index.css'

// Release QA NDV-PERF-009 (docs/reports/native-dataviz/QA_MATRIX.csv): chart data
// is reused on screen for 30 seconds and never cached on the server. Hits and
// misses are counted from real requests, and no cache or speed badge is shown.

const DATASET = 'aaaaaaaa-0000-4000-8000-0000000000c9'
const chart = {
  schema_version: 2, chart_id: 'cccccccc-0000-4000-8000-0000000000c9', title: 'Revenue', dataset_id: DATASET,
  dataset_revision: 1, visual: 'bar', row_limit: 1000,
  encoding: { kind: 'cartesian', dimensions: [{ field_id: 'region' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }] },
} as unknown as ChartDefinition
const result: DatasetQueryResult = {
  dataset_id: DATASET, revision: 1, truncated: false,
  columns: [{ key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
    { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }] as DatasetQueryResult['columns'],
  rows: [[{ type: 'string', value: 'East' }, { type: 'decimal', value: '1500' }]],
}

let requests = 0
function show(client: QueryClient) {
  return render(<QueryClientProvider client={client}>
    <div style={{ width: 640, height: 360, display: 'flex' }}><NativeChart chart={chart} draft={false} /></div>
  </QueryClientProvider>)
}

afterEach(async () => { await cleanup(); vi.unstubAllGlobals() })

describe('NDV-PERF-009 cold and warm chart data', () => {
  it('counts a cold miss, a warm hit within the reuse window and shows no cache badge', async () => {
    requests = 0
    vi.stubGlobal('fetch', vi.fn(async () => {
      requests += 1
      return new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let screen = await show(client)
    await vi.waitFor(() => expect(requests).toBe(1))
    await vi.waitFor(() => expect(client.getQueryCache().getAll().some(query => query.state.status === 'success')).toBe(true))
    // Warm: the same chart opened again within 30 s reads the rows on screen.
    await cleanup()
    screen = await show(client)
    await vi.waitFor(() => expect(screen.container.querySelector('canvas, svg, table')).not.toBeNull())
    await new Promise(resolve => setTimeout(resolve, 200))
    expect(requests).toBe(1)
    // Cold: a new page load asks the server again; nothing is kept there.
    await cleanup()
    screen = await show(new QueryClient({ defaultOptions: { queries: { retry: false } } }))
    await vi.waitFor(() => expect(requests).toBe(2))
    // No invented performance claim on the chart.
    const text = screen.container.textContent ?? ''
    expect(text).not.toMatch(/cache|cached|instant|faster|\d+\s?ms\b/i)
  })
})
