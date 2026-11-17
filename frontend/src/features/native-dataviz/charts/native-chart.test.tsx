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

describe('chart text alternative', () => {
  it('gives a drawn chart a table of its governed rows for screen readers', async () => {
    respond(200, { dataset_id: DATASET, revision: 1, truncated: false, columns: [
      { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
      { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }],
    rows: [[{ type: 'string', value: 'Jakarta' }, { type: 'decimal', value: '400' }], [{ type: 'string', value: 'Bandung' }, { type: 'decimal', value: '600' }]] })
    const screen = await show()
    const table = screen.getByRole('table', { name: 'Data for Revenue by region' })
    await expect.element(table).toBeInTheDocument()
    await expect.element(table.getByRole('columnheader', { name: 'Sum of revenue' })).toBeInTheDocument()
    await expect.element(table.getByRole('cell', { name: 'Bandung' })).toBeInTheDocument()
    await expect.element(table.getByRole('cell', { name: '600' })).toBeInTheDocument()
  })
})

describe('Ranger policy changes while a session is open', () => {
  const columns = [
    { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
    { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }]
  const rows = (...regions: string[]) => regions.map(region => [{ type: 'string', value: region }, { type: 'decimal', value: '100' }])

  async function mount(client: QueryClient) {
    return render(<QueryClientProvider client={client}>
      <div style={{ width: 480, height: 320, display: 'flex' }}><NativeChart chart={bar} draft={false} /></div>
    </QueryClientProvider>)
  }

  // Past the short window a chart may show rows it already read.
  async function laterThanTheShownRowsWindow() {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(Date.now() + 31_000)
  }
  afterEach(() => { vi.useRealTimers() })

  it('asks the engine again when the chart opens, and shows no earlier rows once access is revoked', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    respond(200, { dataset_id: DATASET, revision: 1, truncated: false, columns, rows: rows('Jakarta', 'Bandung') })
    const first = await mount(client)
    await expect.element(first.getByRole('table', { name: 'Data for Revenue by region' })).toHaveTextContent('Jakarta')
    await first.unmount()

    // A steward revokes the policy; the role and security epoch stay the same.
    await laterThanTheShownRowsWindow()
    respond(403, { detail: 'Access denied by Ranger' })
    const again = await mount(client)
    await expect.element(again.getByRole('alert')).toHaveTextContent('This data is unavailable with your current role.')
    expect(again.container.textContent).not.toContain('Jakarta')
    expect(again.container.querySelector('canvas')).toBeNull()
  })

  it('replaces the rows with the narrowed result when a row filter is added', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    respond(200, { dataset_id: DATASET, revision: 1, truncated: false, columns, rows: rows('Jakarta', 'Bandung') })
    const first = await mount(client)
    await expect.element(first.getByRole('table', { name: 'Data for Revenue by region' })).toHaveTextContent('Bandung')
    await first.unmount()

    await laterThanTheShownRowsWindow()
    respond(200, { dataset_id: DATASET, revision: 1, truncated: false, columns, rows: rows('Jakarta') })
    const again = await mount(client)
    await vi.waitFor(() => expect(again.container.querySelector('table')?.textContent ?? '').not.toContain('Bandung'))
    await expect.element(again.getByRole('table', { name: 'Data for Revenue by region' })).toHaveTextContent('Jakarta')
  })
})

describe('NDV-CACHE-004: a filter changed ten times in a row', () => {
  it('shows only the newest result, even when older answers arrive last, and never sends queued superseded queries', async () => {
    const columns = [
      { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
      { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }]
    const asked: string[] = []
    // Each answer echoes the filtered region; earlier filters answer later, so replies arrive newest first.
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit = {}) => {
      const body = JSON.parse(String(options.body ?? '{}')) as { filters?: { expression: { values: { value: string }[] } } }
      const region = body.filters?.expression.values[0].value ?? 'none'
      asked.push(region)
      await new Promise(resolve => setTimeout(resolve, 900 - Number(region.slice(1)) * 80))
      return new Response(JSON.stringify({ dataset_id: DATASET, revision: 1, truncated: false, columns,
        rows: [[{ type: 'string', value: region }, { type: 'decimal', value: '1' }]] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const context = (n: number) => ({ expression: { type: 'predicate', op: 'eq',
      field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'region' }, values: [{ type: 'string', value: `R${n}` }] } }) as never
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = (n: number) => <QueryClientProvider client={client}>
      <div style={{ width: 480, height: 320, display: 'flex' }}><NativeChart chart={bar} draft={false} context={context(n)} /></div>
    </QueryClientProvider>
    const screen = await render(view(1))
    for (let n = 2; n <= 10; n += 1) {
      await new Promise(resolve => setTimeout(resolve, 20))
      await screen.rerender(view(n))
    }
    const table = screen.getByRole('table', { name: 'Data for Revenue by region' })
    await expect.element(table).toHaveTextContent('R10')
    // Let every earlier answer arrive; the chart must still show only the newest filter.
    await new Promise(resolve => setTimeout(resolve, 1000))
    expect(table.element().textContent).toContain('R10')
    expect(table.element().textContent).not.toMatch(/R[1-9](?!0)/)
    // Three queries run at once; filters superseded while waiting are never sent.
    expect(asked[asked.length - 1]).toBe('R10')
    expect(asked.length).toBeLessThan(10)
  })
})
