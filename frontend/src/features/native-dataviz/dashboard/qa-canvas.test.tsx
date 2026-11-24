import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { getInstanceByDom, type ECharts } from 'echarts/core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cdp, page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { ThemeProvider } from '@/context/theme-provider'
import { useAuthStore } from '@/stores/auth-store'
import type { DashboardDefinition, DatasetQueryRequest, FilterTree, PageV2 } from '../contracts'
import { cellLabel, periodEnd, type DateGrain } from '../charts/chart-query'
import { DashboardDataTab } from '../dashboard-data-tab'
import { DashboardCanvas } from './canvas'
import { FilterBar } from './filters'
import { upcastPage, type Page } from './layout'
import '@/styles/index.css'

// Release QA for dashboard reading: drill, back, and long labels (docs/reports/native-dataviz/QA_MATRIX.csv).

const id = (value: number) => `aaaaaaaa-0000-4000-8000-${String(value).padStart(12, '0')}`
const DASHBOARD = id(901)
const DATASET = id(902)
const CHART = id(903)
const WIDGET = id(904)
const PAGE = id(905)
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const binding = (field_id: string) => ({ dataset_id: DATASET, dataset_revision: 1, field_id })

const LONG_TITLE = 'Pendapatan bersih per wilayah penjualan dan saluran distribusi — الإيرادات الصافية حسب المنطقة — 地域別・チャネル別の純売上高 2024–2026'
let chartTitle = 'Revenue by order date'
const chartDefinition = () => ({
  schema_version: 2, chart_id: CHART, title: chartTitle, dataset_id: DATASET, dataset_revision: 1, visual: 'bar',
  encoding: { kind: 'cartesian', dimensions: [{ field_id: 'order_date', date_grain: 'year' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }] },
})
const fields = [
  { field_id: 'order_date', name: 'order_date', source_column: 'order_date', logical_type: 'date', physical_type: 'DATE', semantic_role: 'dimension', nullable: true },
  { field_id: 'revenue', name: 'revenue', source_column: 'revenue', logical_type: 'decimal', physical_type: 'DECIMAL(18,2)', semantic_role: 'measure', nullable: true },
]
let datasetTitle = 'Orders'
const dataset = () => ({ resource_id: DATASET, revision: 1, metadata_revision: 1, grants_revision: 1, publication_id: null,
  definition: { dataset_id: DATASET, title: datasetTitle, fields, schema_fingerprint: 'a'.repeat(64),
    source: { kind: 'table', catalog: 'default_catalog', database: 'Sales', object_name: 'Orders', physical_id: '1001' } } })

/** Governed rows per date level: one finer level inside the first period of the coarser one. */
const ROWS: Record<DateGrain, [string, number][]> = {
  year: [['2024-01-01', 400], ['2025-01-01', 300]],
  quarter: [['2024-01-01', 100], ['2024-04-01', 300]],
  month: [['2024-01-01', 20], ['2024-02-01', 30], ['2024-03-01', 50]],
  week: [['2024-01-01', 12], ['2024-01-08', 8]],
  day: [['2024-01-01', 4], ['2024-01-02', 8]],
}
const requests: DatasetQueryRequest[] = []
let categoryLabels: string[] = []
/** A date grain the engine refuses for this role, as Ranger does for a denied detail. */
let deniedGrain: DateGrain | null = null

async function route(input: RequestInfo | URL, options: RequestInit = {}) {
  const url = String(input)
  if (url.includes(`/viz/charts/${CHART}?`)) return json({ resource_id: CHART, revision: 1, metadata_revision: 1, grants_revision: 1, publication_id: null, definition: chartDefinition() })
  if (url.includes(`/viz/datasets/${DATASET}?`)) return json(dataset())
  if (url.endsWith(`/viz/datasets/${DATASET}/query`)) {
    const request = JSON.parse(String(options.body)) as DatasetQueryRequest
    requests.push(request)
    const grain = (request.dimensions?.[0]?.date_grain ?? 'year') as DateGrain
    if (grain === deniedGrain) return json({ detail: 'Access denied for this data' }, 403)
    const column = categoryLabels.length
      ? { key: 'd0', name: 'order_date', logical_type: 'string', role: 'dimension', field_id: 'order_date' }
      : { key: 'd0', name: 'order_date', logical_type: 'date', role: 'dimension', field_id: 'order_date', date_grain: grain }
    return json({ dataset_id: DATASET, revision: 1, truncated: false,
      columns: [column, { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }],
      rows: (categoryLabels.length ? categoryLabels.map((label, i) => [{ type: 'string', value: label }, { type: 'decimal', value: String(i + 1) }]) : null)
        ?? ROWS[grain].map(([day, value]) => [{ type: 'date', value: day }, { type: 'decimal', value: String(value) }]) })
  }
  if (url.includes(`/dashboards/${DASHBOARD}/datasets?`)) return json({ dashboard_id: DASHBOARD, revision: 3, datasets: [{ dataset_id: DATASET, revision: 1, title: datasetTitle }] })
  if (url.endsWith('/preview')) return json({ dataset_id: DATASET, revision: 1, fields, offset: 0, has_more: false, next_offset: null, rows: [] })
  if (url.endsWith('/catalogs')) return json({ catalogs: [] })
  throw new Error(`Unexpected request ${url}`)
}

const BASE: FilterTree = { expression: { type: 'predicate', field: binding('channel'), op: 'eq', values: [{ type: 'string', value: 'Online' }], source: 'global', stage: 'row' } as FilterTree['expression'] }
const pageWith = (title = ''): Page => upcastPage({ page_id: PAGE, title: 'Overview', widgets: [
  { kind: 'chart', widget_id: WIDGET, title, locked: false, layout: { x: 0, y: 0, w: 12, h: 5 }, chart_ref: { chart_id: CHART, revision: 1 } },
] } as unknown as PageV2)
const dashboardDefinition: DashboardDefinition = { schema_version: 3, dashboard_id: DASHBOARD, title: 'Sales', description: '', filters: [], actions: [],
  datasets: [{ dataset_id: DATASET, revision: 1 }], pages: [pageWith()] }
const clients: QueryClient[] = []
const onSelectValue = vi.fn()

async function mountCanvas(width = 1000, title = '') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const noop = () => undefined
  const screen = await render(<QueryClientProvider client={client}>
    <div style={{ width, height: 600, display: 'flex', flexDirection: 'column' }}>
      <DashboardCanvas definition={dashboardDefinition} page={pageWith(title)} draft={false} editing={false} selectedId={null}
        onSelect={noop} onLayout={noop} onEditChart={noop} onDuplicate={noop} onRemove={noop}
        contextFor={() => BASE} onSelectValue={onSelectValue} interactive />
    </div>
  </QueryClientProvider>)
  return { screen, client }
}

/** The request behind the chart currently on screen, read from the query cache. */
function shownRequest(client: QueryClient): DatasetQueryRequest {
  const active = client.getQueryCache().getAll()
    .filter(query => query.queryKey[1] === 'chart-data' && query.getObserversCount() > 0)
  expect(active).toHaveLength(1)
  return JSON.parse(String(active[0].queryKey[4])) as DatasetQueryRequest
}

async function chartOn(container: HTMLElement, labels: string[]): Promise<{ host: HTMLElement; chart: ECharts }> {
  let found: { host: HTMLElement; chart: ECharts } | null = null
  await expect.poll(() => {
    const host = container.querySelector<HTMLElement>('[role="img"]')
    const chart = host ? getInstanceByDom(host) : undefined
    const axis = (chart?.getOption() as { xAxis?: { data?: string[] }[] } | undefined)?.xAxis?.[0]?.data
    found = host && chart ? { host, chart } : null
    return axis
  }, { timeout: 5000 }).toEqual(labels)
  await new Promise(resolve => setTimeout(resolve, 600))
  return found!
}

const label = (day: string, grain: DateGrain) => cellLabel({ type: 'date', value: day }, { date_grain: grain })
const labels = (grain: DateGrain) => ROWS[grain].map(([day]) => label(day, grain))

async function drill(container: HTMLElement, grain: DateGrain, index: number, next: string) {
  const { host, chart } = await chartOn(container, labels(grain))
  const [category, value] = [labels(grain)[index], ROWS[grain][index][1]]
  const [x, y] = chart.convertToPixel({ seriesIndex: 0 }, [category, value / 2]) as number[]
  await userEvent.click(host.querySelector('canvas')!, { position: { x: Math.round(x), y: Math.round(y) } })
  await page.getByRole('menuitem', { name: 'Drill into this by' }).click()
  await page.getByRole('menuitem', { name: next }).click()
}

/** The base dashboard filter, then one [from, to) period per drilled step. */
function periods(request: DatasetQueryRequest) {
  const expression = request.filters!.expression as { type: string; children: { type: string; op?: string; values?: { value: string }[]; children?: { op: string; values: { value: string }[] }[] }[] }
  expect(expression.type).toBe('and')
  expect(expression.children[0]).toEqual(BASE.expression)
  return expression.children.slice(1).map(child => {
    expect(child.type).toBe('and')
    return child.children!.map(part => `${part.op} ${part.values[0].value}`).join(' & ')
  })
}

const heading = (container: HTMLElement) => container.querySelector('h3')!

beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('qa-canvas-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  requests.length = 0
  categoryLabels = []
  deniedGrain = null
  chartTitle = 'Revenue by order date'
  datasetTitle = 'Orders'
  onSelectValue.mockReset()
  vi.stubGlobal('fetch', vi.fn(route))
})

afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
})

describe('NDV-A11Y-003 reduced motion', () => {
  // The Playwright provider's CDP session; its typing lives in that provider's package.
  const session = () => cdp() as unknown as { send(method: string, params?: object): Promise<unknown> }
  const motion = (value: 'reduce' | 'no-preference') => session().send('Emulation.setEmulatedMedia',
    { features: [{ name: 'prefers-reduced-motion', value }] })
  const COLOURS = /^(color|background-color|border-color|outline-color|text-decoration-color|fill|stroke|--tw-[a-z-]*|all)$/

  /** Elements that would move: a running animation, or a timed transition of anything but colour. */
  function moving(root: HTMLElement): string[] {
    const found: string[] = []
    for (const element of [root, ...root.querySelectorAll<HTMLElement>('*')]) {
      const style = getComputedStyle(element)
      const animated = style.animationName !== 'none' && style.animationDuration.split(',').some(value => parseFloat(value) > 0)
      const properties = style.transitionProperty.split(',').map(value => value.trim())
      const durations = style.transitionDuration.split(',').map(value => parseFloat(value))
      const transitions = properties.filter((property, i) => (durations[i % durations.length] ?? 0) > 0 && !COLOURS.test(property))
      if (animated || transitions.length) found.push(`${element.tagName.toLowerCase()}.${element.className}: ${style.animationName} ${transitions.join(' ')}`)
    }
    return found
  }

  it('draws charts and places widgets without animation, and nothing on the canvas moves', async () => {
    let pending = true
    const held = new Promise<void>(resolve => { setTimeout(resolve, 800) })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      // The first query waits a moment, so the loading placeholder is on screen.
      if (pending && String(input).endsWith('/query')) { pending = false; await held }
      return route(input, options)
    }))
    await motion('reduce')
    try {
      const { screen } = await mountCanvas()
      const container = screen.container
      await expect.poll(() => container.querySelector('[aria-busy="true"]')).not.toBeNull()
      expect(moving(container)).toEqual([])
      const { chart } = await chartOn(container, labels('year'))
      expect((chart.getOption() as { animation?: boolean }).animation).toBe(false)
      expect(container.querySelector('.grid-stack')!.classList.contains('grid-stack-animate')).toBe(false)
      expect(moving(container)).toEqual([])
    } finally {
      await motion('no-preference')
    }
    // Without the preference the same canvas animates, so the checks above can fail.
    await cleanup()
    const { screen } = await mountCanvas()
    const { chart } = await chartOn(screen.container, labels('year'))
    expect((chart.getOption() as { animation?: boolean }).animation).not.toBe(false)
    expect(screen.container.querySelector('.grid-stack')!.classList.contains('grid-stack-animate')).toBe(true)
  })
})

describe('NDV-INTER-009 drilling into detail the role cannot read', () => {
  it('shows the refusal and no rows, not even the level above, and Back returns to what the role can read', async () => {
    deniedGrain = 'quarter'
    const { screen } = await mountCanvas()
    const container = screen.container
    await chartOn(container, labels('year'))
    await drill(container, 'year', 0, 'Order date by quarter')
    await expect.element(screen.getByText('This data is unavailable with your current role.')).toBeVisible()
    expect(requests.some(request => request.dimensions?.[0]?.date_grain === 'quarter')).toBe(true)
    // Nothing from the refused query or the previous level stays drawn under the drilled title.
    expect(heading(container).textContent).toContain('›')
    const host = container.querySelector<HTMLElement>('[role="img"]')
    const drawn = host ? getInstanceByDom(host) : undefined
    expect(drawn).toBeUndefined()
    expect(container.querySelectorAll('tbody tr')).toHaveLength(0)
    // Only the title names the clicked year; no value of the previous level is shown.
    const body = (container.textContent ?? '').replace(heading(container).textContent ?? '', '')
    for (const year of labels('year')) expect(body).not.toContain(year)

    await screen.getByRole('button', { name: 'Back up one level' }).click()
    await chartOn(container, labels('year'))
    expect(heading(container).textContent).toBe('Revenue by order date')
  })
})

describe('NDV-INTER-007 / NDV-INTER-012 date drill-down and back', () => {
  it('regroups and narrows at every level from year to day, then restores each level on Back', async () => {
    const { screen, client } = await mountCanvas()
    const container = screen.container
    const base = 'Revenue by order date'
    await chartOn(container, labels('year'))
    expect(heading(container).textContent).toBe(base)
    expect(shownRequest(client).dimensions).toEqual([{ field_id: 'order_date', date_grain: 'year' }])
    expect(shownRequest(client).filters).toEqual(BASE)

    const path: { grain: DateGrain; option: string; next: DateGrain }[] = [
      { grain: 'year', option: 'Order date by quarter', next: 'quarter' },
      { grain: 'quarter', option: 'Order date by month', next: 'month' },
      { grain: 'month', option: 'Order date by week', next: 'week' },
      { grain: 'week', option: 'Order date by day', next: 'day' },
    ]
    const crumbs: string[] = []
    const bounds: string[] = []
    const snapshots: { title: string; request: DatasetQueryRequest; labels: string[] }[] = [
      { title: base, request: shownRequest(client), labels: labels('year') },
    ]
    for (const step of path) {
      await drill(container, step.grain, 0, step.option)
      const [from] = ROWS[step.grain][0]
      crumbs.push(label(from, step.grain))
      bounds.push(`gte ${from} & lt ${periodEnd(from, step.grain)}`)
      await chartOn(container, labels(step.next))
      // Breadcrumb: the chart title, then every value drilled into, in order.
      expect(heading(container).textContent).toBe([base, ...crumbs].join(' › '))
      const request = shownRequest(client)
      expect(request.dimensions).toEqual([{ field_id: 'order_date', date_grain: step.next }])
      expect(request.measures).toEqual([{ field_id: 'revenue', aggregation: 'sum' }])
      expect(periods(request)).toEqual(bounds)
      // The request actually sent matches the one on screen.
      expect(requests[requests.length - 1].dimensions).toEqual(request.dimensions)
      expect(requests[requests.length - 1].filters).toMatchObject(request.filters!)
      snapshots.push({ title: heading(container).textContent!, request, labels: labels(step.next) })
    }
    expect(bounds).toEqual([
      'gte 2024-01-01 & lt 2025-01-01', 'gte 2024-01-01 & lt 2024-04-01',
      'gte 2024-01-01 & lt 2024-02-01', 'gte 2024-01-01 & lt 2024-01-08',
    ])
    // Drilling never filters the rest of the dashboard.
    expect(onSelectValue).not.toHaveBeenCalled()
    await expect.element(screen.getByRole('button', { name: 'Reset' })).toBeVisible()

    // Back, one level at a time, restores the previous grouping, filters and breadcrumb.
    for (let level = snapshots.length - 2; level >= 0; level--) {
      await screen.getByRole('button', { name: 'Back up one level' }).click()
      await chartOn(container, snapshots[level].labels)
      expect(heading(container).textContent).toBe(snapshots[level].title)
      expect(shownRequest(client)).toEqual(snapshots[level].request)
    }
    expect(container.querySelector('[aria-label="Back up one level"]')).toBeNull()
    expect(shownRequest(client).filters).toEqual(BASE)
  }, 30_000) // Five levels down and back: longer than one interaction.
})

describe('NDV-A11Y-005 long and multi-language labels', () => {
  it('truncates a long widget title inside its card and keeps the full title as its accessible name', async () => {
    const long = ['Pendapatan bersih per wilayah penjualan Indonesia Timur dan Barat', 'الإيرادات الصافية حسب المنطقة', '地域別の純売上高']
    categoryLabels = long
    const { screen } = await mountCanvas(420, LONG_TITLE)
    const container = screen.container
    await chartOn(container, long)
    const title = heading(container)
    expect(title.textContent).toBe(LONG_TITLE)
    expect(title.getAttribute('title')).toBe(LONG_TITLE)
    expect(getComputedStyle(title).textOverflow).toBe('ellipsis')
    expect(title.scrollWidth).toBeGreaterThan(title.clientWidth)
    const card = container.querySelector<HTMLElement>('.grid-stack-item-content')!
    const box = card.getBoundingClientRect()
    // Nothing drawn sticks out of the card (the screen-reader table is clipped to nothing).
    const visible = [...card.querySelectorAll<HTMLElement>('*')].filter(el => !el.closest('.sr-only'))
    expect(visible.filter(el => el.getBoundingClientRect().right > box.right + 0.5).map(el => el.tagName)).toEqual([])
    expect(getComputedStyle(card).overflowX).toBe('hidden')
    const scroller = container.querySelector<HTMLElement>('.overflow-y-auto')!
    expect(scroller.scrollWidth).toBeLessThanOrEqual(scroller.clientWidth)
    // Axis labels are shortened on the canvas; the data table keeps every full label.
    for (const text of long) await expect.element(screen.getByRole('cell', { name: text, exact: true })).toBeInTheDocument()
  })

  it('keeps a long filter chip inside its maximum width and names it in full', async () => {
    const filter = { filter_id: 'f1', title: LONG_TITLE, kind: 'multi_value', field: binding('order_date'), scope: { kind: 'global' },
      mappings: [], defaults: [], allow_all: true, include_null: false, searchable: true, depends_on: [] }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    clients.push(client)
    const screen = await render(<QueryClientProvider client={client}><div style={{ width: 360 }}>
      <FilterBar definition={{ ...dashboardDefinition, filters: [filter] } as unknown as DashboardDefinition} values={{}} onChange={() => undefined}
        crossFilters={[]} onClearCross={() => undefined} editing={false} onEdit={() => undefined} draft={false} />
    </div></QueryClientProvider>)
    const chip = screen.getByRole('button', { name: `${LONG_TITLE} filter` })
    await expect.element(chip).toBeVisible()
    const element = chip.element() as HTMLElement
    const text = element.querySelector<HTMLElement>('.truncate')!
    expect(getComputedStyle(text).textOverflow).toBe('ellipsis')
    expect(text.scrollWidth).toBeGreaterThan(text.clientWidth)
    expect(element.getBoundingClientRect().width).toBeLessThanOrEqual(288)
    const bar = screen.getByRole('toolbar', { name: 'Dashboard filters' }).element() as HTMLElement
    expect(bar.scrollWidth).toBeLessThanOrEqual(bar.clientWidth)
  })

  it('clamps a long dataset name in the Data tab list and keeps the full name', async () => {
    datasetTitle = LONG_TITLE
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    clients.push(client)
    const screen = await render(<ThemeProvider defaultTheme='light' storageKey='qa-canvas-test-theme'><QueryClientProvider client={client}>
      <div style={{ width: 'calc(100vw - 32px)', height: 'calc(100dvh - 32px)', display: 'flex', minHeight: 0, minWidth: 0 }}>
        <DashboardDataTab dashboardId={DASHBOARD} workspaceId='personal:alice' revision={3} onCommitted={() => undefined} onReload={() => undefined} />
      </div>
    </QueryClientProvider></ThemeProvider>)
    const item = screen.getByRole('navigation', { name: 'Choose a dataset' }).getByRole('button', { name: LONG_TITLE })
    await expect.element(item).toBeVisible()
    const button = item.element() as HTMLElement
    const name = button.querySelector<HTMLElement>('.line-clamp-2')!
    expect(name.getAttribute('title')).toBe(LONG_TITLE)
    expect(name.scrollHeight).toBeGreaterThan(name.clientHeight)
    expect(button.scrollWidth).toBeLessThanOrEqual(button.clientWidth)
  })
})
