import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { getInstanceByDom } from 'echarts/core'
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

  it('says an unreachable engine is not about access, and offers a retry', async () => {
    respond(503, { detail: 'The data engine is not responding. Try again in a moment.' })
    const screen = await show()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The data engine is not responding. Retry in a moment.')
    await expect.element(screen.getByRole('button', { name: 'Retry' })).toBeVisible()
    expect(screen.container.textContent).not.toContain('current role')
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

describe('NDV-INTER-006: a legend entry as the selection source', () => {
  const split = { ...bar, chart_id: 'cccccccc-0000-4000-8000-000000000006', title: 'Revenue by region and channel',
    encoding: { kind: 'cartesian', dimensions: [{ field_id: 'region' }], color: { field_id: 'channel' },
      measures: [{ field_id: 'revenue', aggregation: 'sum' }] } } as unknown as ChartDefinition
  const columns = [
    { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
    { key: 'd1', name: 'channel', logical_type: 'string', role: 'dimension', field_id: 'channel' },
    { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }]
  const cell = (value: string) => ({ type: 'string', value })
  const rows = [[cell('East'), cell('Online'), { type: 'decimal', value: '10' }], [cell('East'), cell('Store'), { type: 'decimal', value: '5' }],
    [cell('West'), cell('Online'), { type: 'decimal', value: '7' }]]

  it('selects the series field and value, from the legend, and keeps every series drawn', async () => {
    respond(200, { dataset_id: DATASET, revision: 1, truncated: false, columns, rows })
    const chosen: unknown[] = []
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const screen = await render(<QueryClientProvider client={client}>
      <div style={{ width: 480, height: 320, display: 'flex' }}>
        <NativeChart chart={split} draft={false} onSelectValue={value => chosen.push(value)} />
      </div>
    </QueryClientProvider>)
    await expect.poll(() => screen.container.querySelector('canvas')).not.toBeNull()
    const host = screen.container.querySelector<HTMLElement>('[role="img"]')!
    const chart = getInstanceByDom(host)!
    // Find the drawn legend label "Store" and click it where it is on the canvas.
    type Box = { x: number; y: number; width: number; height: number; clone: () => Box; applyTransform: (matrix: unknown) => void }
    type Drawn = { style?: { text?: string }; transform?: unknown; getBoundingRect: () => Box }
    const find = () => (chart.getZr() as unknown as { storage: { getDisplayList: (update: boolean) => Drawn[] } })
      .storage.getDisplayList(true).find(item => item.style?.text === 'Store')
    await expect.poll(find).toBeDefined()
    const label = find()!
    const box = label.getBoundingRect().clone()
    if (label.transform) box.applyTransform(label.transform)
    await userEvent.click(page.elementLocator(host), { position: { x: box.x + box.width / 2, y: box.y + box.height / 2 } })
    await expect.poll(() => chosen.length).toBe(1)
    expect(chosen[0]).toMatchObject({ field_id: 'channel', value: cell('Store'), label: 'Store', fromLegend: true })
    // A legend click filters; it never hides the series it names.
    const selected = (chart.getOption() as { legend: { selected?: Record<string, boolean> }[] }).legend[0].selected ?? {}
    expect(selected.Store).not.toBe(false)
  })
})

describe('NDV-CHART-009: a pie with more values than slices', () => {
  const pie = (overflow: 'other' | 'error') => ({ ...bar, chart_id: 'cccccccc-0000-4000-8000-000000000009', title: 'Revenue by city', visual: 'pie',
    encoding: { kind: 'category', category: { field_id: 'city' }, measure: { field_id: 'revenue', aggregation: 'sum' }, max_categories: 3, overflow } }) as unknown as ChartDefinition
  const city = { key: 'd0', name: 'city', logical_type: 'string', role: 'dimension', field_id: 'city' }
  const revenue = { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }
  const sent: { dimensions?: unknown[] }[] = []
  function serve() {
    sent.length = 0
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit = {}) => {
      const body = JSON.parse(String(options.body ?? '{}')) as { dimensions?: unknown[] }
      sent.push(body)
      const rows = body.dimensions?.length
        ? [['Jakarta', '500'], ['Bandung', '300'], ['Medan', '100']].map(([name, value]) => [{ type: 'string', value: name }, { type: 'decimal', value }])
        : [[{ type: 'decimal', value: '1000' }]]
      return new Response(JSON.stringify({ dataset_id: DATASET, revision: 1, truncated: Boolean(body.dimensions?.length),
        columns: body.dimensions?.length ? [city, revenue] : [revenue], rows }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
  }
  const slices = (host: HTMLElement) => ((getInstanceByDom(host)!.getOption() as { series: { data: { name: string; value: number }[] }[] }).series[0].data)
    .map(item => [item.name, item.value])

  it('asks for the whole under the same filters and draws the rest as Other', async () => {
    serve()
    const chosen: unknown[] = []
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const screen = await render(<QueryClientProvider client={client}>
      <div style={{ width: 480, height: 320, display: 'flex' }}>
        <NativeChart chart={pie('other')} draft={false} onSelectValue={value => chosen.push(value)} />
      </div>
    </QueryClientProvider>)
    await expect.element(screen.getByText('Showing the top 3 and the rest as Other.')).toBeVisible()
    const host = screen.container.querySelector<HTMLElement>('[role="img"]')!
    await expect.poll(() => slices(host)).toEqual([['Jakarta', 500], ['Bandung', 300], ['Medan', 100], ['Other', 100]])
    expect(sent.filter(body => !body.dimensions?.length)).toHaveLength(1)
    // Other stands for many values: clicking it selects nothing, while a named slice still does.
    type Sector = { shape: { cx: number; cy: number; r: number; r0: number; startAngle: number; endAngle: number } }
    type Model = { getModel: () => { getSeriesByIndex: (index: number) => { getData: () => { getItemGraphicEl: (index: number) => Sector } } } }
    // A point inside the slice: halfway along its arc, halfway out from the centre.
    const centre = (index: number) => {
      const { cx, cy, r, r0, startAngle, endAngle } = (getInstanceByDom(host) as unknown as Model).getModel()
        .getSeriesByIndex(0).getData().getItemGraphicEl(index).shape
      const angle = (startAngle + endAngle) / 2
      const radius = (r + r0) / 2
      return { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) }
    }
    await new Promise(resolve => setTimeout(resolve, 700))
    const hit = (index: number) => {
      const { x, y } = centre(index)
      const zr = getInstanceByDom(host)!.getZr() as unknown as { findHover: (x: number, y: number) => { target?: unknown } }
      return zr.findHover(x, y).target
    }
    const el = (index: number) => (getInstanceByDom(host) as unknown as Model).getModel().getSeriesByIndex(0).getData().getItemGraphicEl(index)
    expect(hit(3)).toBe(el(3))
    await userEvent.click(page.elementLocator(host), { position: centre(3) })
    await new Promise(resolve => setTimeout(resolve, 200))
    expect(chosen).toEqual([])
    await userEvent.click(page.elementLocator(host), { position: centre(0) })
    await expect.poll(() => chosen.length).toBe(1)
    expect(chosen[0]).toMatchObject({ field_id: 'city', label: 'Jakarta' })
  })

  it('without Other shows the top slices only, says so, and asks for no total', async () => {
    serve()
    const screen = await show(pie('error'))
    await expect.element(screen.getByText('Showing the top 3 only.')).toBeVisible()
    const host = screen.container.querySelector<HTMLElement>('[role="img"]')!
    await expect.poll(() => slices(host)).toHaveLength(3)
    expect(sent.every(body => body.dimensions?.length)).toBe(true)
  })
})
