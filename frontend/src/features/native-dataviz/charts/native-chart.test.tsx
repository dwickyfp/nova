import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import type { ChartDefinition } from '../contracts'
import { NativeChart } from './native-chart'
import '@/styles/index.css'

const DATASET = 'dddddddd-0000-4000-8000-000000000001'
const bar = {
  schema_version: 2, chart_id: 'cccccccc-0000-4000-8000-000000000001', title: 'Revenue by region',
  dataset_id: DATASET, dataset_revision: 1, visual: 'bar',
  encoding: { kind: 'cartesian', dimensions: [{ field_id: 'region' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }] },
} as unknown as ChartDefinition

function respond(status: number, body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' } })))
}

async function show(chart: ChartDefinition = bar) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <div style={{ width: 480, height: 320, display: 'flex' }}><NativeChart chart={chart} draft={false} /></div>
  </QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('chart states', () => {
  it('says no rows match when the governed result is empty', async () => {
    respond(200, { dataset_id: DATASET, revision: 1, truncated: false, rows: [], columns: [
      { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
      { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }] })
    const screen = await show()
    await expect.element(screen.getByText('No rows match the current filters.')).toBeVisible()
    expect(screen.container.textContent).not.toContain('unavailable')
  })

  it('says the data is unavailable with this role, shows no values, and offers a retry', async () => {
    respond(403, { detail: 'Source schema is unavailable or not permitted' })
    const screen = await show()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('This data is unavailable with your current role.')
    await expect.element(screen.getByRole('button', { name: 'Retry' })).toBeVisible()
    expect(screen.container.textContent).not.toContain('Source schema')
    expect(screen.container.querySelector('canvas')).toBeNull()
  })

  it('names a timeout and a busy engine in plain words', async () => {
    respond(504, { detail: 'Dataset query timed out' })
    const timedOut = await show()
    await expect.element(timedOut.getByRole('alert')).toHaveTextContent('The query timed out. Add a filter or reduce the row limit.')
  })
})
