import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { getInstanceByDom } from 'echarts/core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/auth-store'
import type { ChartDefinition, DatasetField } from '../contracts'
import { initialState, toChart } from '../charts/builder-model'
import { DashboardEditor } from './editor'
import '@/styles/index.css'

/*
 * Release-QA scenarios for the dashboard editor and viewer (QA_MATRIX.csv).
 * The server is a fetch fake that keeps one authoritative dashboard document,
 * applies committed operations with revision CAS and records every request.
 */

const DASHBOARD = 'dddddddd-0000-4000-8000-000000000001'
const DATASET = 'aaaaaaaa-0000-4000-8000-000000000001'
const KPI_CHART = 'cccccccc-0000-4000-8000-000000000001'
const REVENUE_CHART = 'cccccccc-0000-4000-8000-000000000002'
const BAR_CHART = 'cccccccc-0000-4000-8000-000000000003'
const PAGE = 'eeeeeeee-0000-4000-8000-000000000001'
const KPI_WIDGET = 'ffffffff-0000-4000-8000-000000000001'
const TEXT_WIDGET = 'ffffffff-0000-4000-8000-000000000002'
const BAR_WIDGET = 'ffffffff-0000-4000-8000-000000000003'
const REVENUE_WIDGET = 'ffffffff-0000-4000-8000-000000000004'

type Json = Record<string, unknown>
type Layout = { x: number; y: number; w: number; h: number }
type WidgetJson = Json & { widget_id: string; layout: Layout }
type PageJson = { page_id: string; title: string; widgets: WidgetJson[] }
type Call = { url: string; method: string; body: Json | null }

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

const field = (field_id: string, logical_type: DatasetField['logical_type'], semantic_role: DatasetField['semantic_role'] = 'dimension'): DatasetField =>
  ({ field_id, name: field_id, source_column: field_id, logical_type, physical_type: logical_type === 'decimal' ? 'DECIMAL(18,2)' : 'VARCHAR(32)', semantic_role, nullable: true })

function kpiChart(chartId: string, title: string, measure: string): ChartDefinition {
  return {
    schema_version: 2, chart_id: chartId, title, dataset_id: DATASET, dataset_revision: 1, visual: 'kpi',
    encoding: { kind: 'single_measure', measure: { field_id: measure, aggregation: 'sum', format: { kind: 'number', decimals: 0, locale: 'en-US' }, axis: 'left', series: 'bar' } },
  } as ChartDefinition
}

function barChart(chartId: string, title: string, measure = 'revenue'): ChartDefinition {
  const state = initialState({ chart_id: chartId, dataset_id: DATASET, dataset_revision: 1, fields: [field('region', 'string'), field(measure, 'decimal', 'measure')] })
  return toChart({ ...state, title })
}

const chartWidget = (widget_id: string, chart_id: string, layout: Layout, title = ''): WidgetJson =>
  ({ kind: 'chart', widget_id, title, locked: false, layout, chart_ref: { chart_id, revision: 1 } })
const textWidget = (widget_id: string, text: string, layout: Layout): WidgetJson =>
  ({ kind: 'text', widget_id, title: '', locked: false, layout, text, style: 'body' })

// ---- the fake server ----------------------------------------------------------------

const fetchMock = vi.fn()
const calls: Call[] = []
const operations: Json[] = []
const refused: Json[] = []
const events: { sequence: number; operation_id: string }[] = []
const clients: QueryClient[] = []
let permissionList: string[] = []
let charts: Record<string, ChartDefinition> = {}
let revision = 4
let publicationId: string | null = null
/** Fault for the next save: committed with the reply lost, or lost before it committed. */
let replyLost: 'after-commit' | 'before-commit' | null = null
/** Whether receipt lookups answer, as after the database is back. */
let receiptsAvailable = true
const receipts: Record<string, Json> = {}
let definition: Json & { pages: PageJson[]; title: string }
/** Answers a dataset query; the default derives the value from the request. */
let answer: (body: Json) => Promise<Response> | Response

const queries = () => calls.filter(call => call.url.endsWith(`/viz/datasets/${DATASET}/query`))
const measureOf = (body: Json | null) => ((body?.measures as { field_id: string }[] | undefined) ?? [])[0]?.field_id

function kpiResult(value: string, measure = 'units') {
  return json({ dataset_id: DATASET, revision: 1, columns: [{ key: 'm0', name: measure, logical_type: 'decimal', role: 'measure', field_id: measure, aggregation: 'sum' }],
    rows: [[{ type: 'decimal', value }]], truncated: false })
}

function barResult(measure = 'revenue') {
  return json({ dataset_id: DATASET, revision: 1, columns: [
    { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
    { key: 'm0', name: measure, logical_type: 'decimal', role: 'measure', field_id: measure, aggregation: 'sum' },
  ], rows: [[{ type: 'string', value: 'East' }, { type: 'decimal', value: '10' }], [{ type: 'string', value: 'West' }, { type: 'decimal', value: '20' }]], truncated: false })
}

function defaultAnswer(body: Json) {
  const measure = measureOf(body)!
  if ((body.dimensions as unknown[] | undefined)?.length) return barResult(measure)
  const filters = JSON.stringify(body.filters ?? null)
  if (measure === 'units') return kpiResult(filters.includes('East') ? '4000' : '15257')
  if (measure === 'revenue') return kpiResult('999', 'revenue')
  return kpiResult(measure.replace(/\D/g, '') || '1', measure)
}

function snapshot() {
  return { resource_id: DASHBOARD, revision, metadata_revision: revision, grants_revision: 1, publication_id: publicationId, definition }
}

/** Server-side application of committed changes, so a reload returns what was saved. */
function commit(changes: Json[]) {
  for (const change of changes) {
    const target = definition.pages.find(item => item.page_id === change.page_id)
    if (change.op === 'layout.apply' && target) {
      for (const placement of change.placements as { widget_id: string; layout: Layout }[]) {
        const widget = target.widgets.find(item => item.widget_id === placement.widget_id)
        if (widget) widget.layout = { ...placement.layout }
      }
    }
    if (change.op === 'widget.add' && target) target.widgets.push(clone(change.widget as WidgetJson))
    if (change.op === 'widget.remove' && target) target.widgets = target.widgets.filter(item => item.widget_id !== change.widget_id)
  }
}

/** Another person commits a change outside this browser. */
function otherUserCommits(mutate: () => void) {
  mutate()
  revision += 1
  events.push({ sequence: revision, operation_id: crypto.randomUUID() })
}

async function route(url: string, options: RequestInit = {}) {
  const method = (options.method ?? 'GET').toUpperCase()
  const body = options.body ? JSON.parse(String(options.body)) as Json : null
  calls.push({ url, method, body })
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/permissions`)) return json(permissionList)
  if (url.includes(`/viz/dashboards/${DASHBOARD}/events`)) {
    const after = Number(new URL(url, 'http://test').searchParams.get('after') ?? 0)
    const last = events[events.length - 1]?.sequence ?? revision
    return json({ events: events.filter(item => item.sequence > after).map(item => ({
      event_id: crypto.randomUUID(), operation_id: item.operation_id, sequence: item.sequence, event_kind: 'edit', created_at: '2026-11-16T00:00:00Z',
    })), through: last, next_sequence: last })
  }
  if (url.includes(`/viz/dashboards/${DASHBOARD}/datasets`)) return json({ dashboard_id: DASHBOARD, revision, datasets: [{ dataset_id: DATASET, revision: 1, title: 'Orders' }] })
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/operations`)) {
    const headers = options.headers as Record<string, string>
    if (body!.expected_revision !== revision) {
      refused.push(body!)
      return json({ detail: 'The dashboard changed; reload it.' }, 409)
    }
    const fault = replyLost
    replyLost = null
    if (fault === 'before-commit') return json({ detail: 'Metadata outcome requires reconciliation before retry' }, 503)
    operations.push(body!)
    commit(body!.changes as Json[])
    const base = revision
    revision += 1
    events.push({ sequence: revision, operation_id: headers['Idempotency-Key'] })
    const receipt = { operation_id: headers['Idempotency-Key'], resource_id: DASHBOARD, base_revision: base, new_revision: revision, event_sequence: revision, committed_at: '2026-11-16T00:00:00Z' }
    receipts[headers['Idempotency-Key']] = receipt
    if (fault === 'after-commit') return json({ detail: 'Metadata outcome requires reconciliation before retry' }, 503)
    return json(receipt)
  }
  const lookup = /\/viz\/operations\/([0-9a-f-]+)$/.exec(url)
  if (lookup) {
    if (!receiptsAvailable) return json({ detail: 'Metadata service is unavailable' }, 503)
    return receipts[lookup[1]] ? json(receipts[lookup[1]]) : json({ detail: 'Not found' }, 404)
  }
  if (url.includes(`/viz/dashboards/${DASHBOARD}?`)) return json(snapshot())
  const chart = /\/viz\/charts\/([0-9a-f-]+)\?/.exec(url)
  if (chart && charts[chart[1]]) return json({ resource_id: chart[1], revision: 1, metadata_revision: 1, grants_revision: 1, publication_id: null, definition: charts[chart[1]] })
  if (url.endsWith(`/viz/datasets/${DATASET}/query`)) return answer(body!)
  // Dataset definitions, presence and comments are not part of these scenarios.
  return json({ detail: 'Not found' }, 404)
}

async function mount(width = 1200, height = 720) {
  const client = new QueryClient()
  clients.push(client)
  const screen = await render(<QueryClientProvider client={client}><TooltipProvider>
    <div data-testid='frame' style={{ height, width, display: 'flex' }}>
      <DashboardEditor dashboardId={DASHBOARD} workspaceId='personal' onBack={() => undefined} />
    </div>
  </TooltipProvider></QueryClientProvider>)
  return Object.assign(screen, { client })
}

function setWidgets(widgets: WidgetJson[]) {
  definition.pages = [{ page_id: PAGE, title: 'Overview', widgets }]
}

type Grid = { getColumn: () => number; engine: { nodes: { id?: string; x?: number; y?: number; w?: number; h?: number }[] } }
const gridOf = (root: ParentNode = document) => (root.querySelector('.grid-stack') as unknown as { gridstack: Grid }).gridstack
const item = (id: string, root: ParentNode = document) => root.querySelector<HTMLElement>(`[gs-id="${id}"]`)!
const geometry = (id: string, root: ParentNode = document) => {
  const element = item(id, root)
  return { x: Number(element.getAttribute('gs-x') ?? 0), y: Number(element.getAttribute('gs-y') ?? 0),
    w: Number(element.getAttribute('gs-w') ?? 1), h: Number(element.getAttribute('gs-h') ?? 1) }
}
const frame = (screen: Awaited<ReturnType<typeof mount>>) => screen.getByTestId('frame').element() as HTMLElement

/** Presses, moves and releases the mouse on a widget, the way GridStack drags start. */
async function mouseDrag(target: HTMLElement, dx: number, dy: number) {
  const box = target.getBoundingClientRect()
  const x = box.left + box.width / 2
  const y = box.top + 12
  const init = (cx: number, cy: number, buttons: number): MouseEventInit => ({ bubbles: true, cancelable: true, clientX: cx, clientY: cy, button: 0, buttons, view: window })
  target.dispatchEvent(new MouseEvent('mousedown', init(x, y, 1)))
  for (let step = 1; step <= 12; step += 1) {
    document.dispatchEvent(new MouseEvent('mousemove', init(x + dx * step / 12, y + dy * step / 12, 1)))
    await wait(16)
  }
  document.dispatchEvent(new MouseEvent('mouseup', init(x + dx, y + dy, 0)))
  await wait(300)
}

const visibleResizeHandles = (root: ParentNode = document) => [...root.querySelectorAll<HTMLElement>('.ui-resizable-handle')]
  .filter(handle => handle.getBoundingClientRect().width > 0 && getComputedStyle(handle).display !== 'none')

type Instance = NonNullable<ReturnType<typeof getInstanceByDom>>
async function echartsIn(widgetId: string): Promise<{ host: HTMLElement; chart: Instance }> {
  return vi.waitFor(() => {
    const host = item(widgetId).querySelector<HTMLElement>('[role="img"]')
    const chart = host && getInstanceByDom(host)
    const series = (chart?.getOption() as { series?: unknown[] } | undefined)?.series
    if (!host || !chart || !series?.length) throw new Error('chart not drawn yet')
    return { host, chart }
  }, { timeout: 5000 })
}

/** A real pointer click in the middle of one bar of a vertical bar chart. */
async function clickBar(widgetId: string, index: number, value: number) {
  const { host, chart } = await echartsIn(widgetId)
  await wait(700) // let the entry animation settle so the bar is where its value says
  const [x, y] = chart.convertToPixel({ seriesIndex: 0 }, [index, value / 2]) as number[]
  await userEvent.click(page.elementLocator(host), { position: { x, y } })
}

const barOpacity = (chart: Instance) =>
  ((chart.getOption() as { series: { data: ({ itemStyle?: { opacity?: number } } | number)[] }[] }).series[0].data)
    .map(datum => typeof datum === 'object' && datum ? datum.itemStyle?.opacity ?? 1 : 1)

beforeEach(() => {
  permissionList = ['view', 'interact', 'comment', 'edit', 'publish', 'manage']
  calls.length = 0
  operations.length = 0
  refused.length = 0
  events.length = 0
  revision = 4
  publicationId = null
  replyLost = null
  receiptsAvailable = true
  for (const key of Object.keys(receipts)) delete receipts[key]
  answer = defaultAnswer
  charts = {
    [KPI_CHART]: kpiChart(KPI_CHART, 'Units sold', 'units'),
    [REVENUE_CHART]: kpiChart(REVENUE_CHART, 'Revenue', 'revenue'),
    [BAR_CHART]: barChart(BAR_CHART, 'Revenue by region'),
  }
  definition = {
    schema_version: 2, dashboard_id: DASHBOARD, title: 'Sales overview', description: '', columns: 12,
    datasets: [{ dataset_id: DATASET, revision: 1 }], filters: [], actions: [], pages: [],
  }
  setWidgets([
    chartWidget(KPI_WIDGET, KPI_CHART, { x: 0, y: 0, w: 3, h: 2 }),
    textWidget(TEXT_WIDGET, 'Plain notes', { x: 3, y: 0, w: 9, h: 2 }),
  ])
  useAuthStore.getState().auth.setAccessToken('qa-editor-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  fetchMock.mockReset().mockImplementation(route)
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  await page.viewport(1280, 720)
})

// ---- grid ---------------------------------------------------------------------------

describe('QA grid', { timeout: 30_000 }, () => {
  it('NDV-CHART-015 / NDV-CACHE-012: resizing chart widgets redraws locally and never re-queries', async () => {
    setWidgets([
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 0, y: 0, w: 3, h: 2 }),
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 3, y: 0, w: 6, h: 3 }),
    ])
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    // Editing reads the draft, which is its own query; measure from there.
    await screen.getByRole('button', { name: 'Edit' }).click()
    await expect.poll(() => queries().filter(call => call.body?.draft === true).length).toBe(2)
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    const { host, chart } = await echartsIn(BAR_WIDGET)
    const before = queries().length
    const width = chart.getWidth()
    const height = chart.getHeight()

    // A committed resize of the bar chart (keyboard resize goes through layout.apply).
    await screen.getByRole('heading', { name: 'Revenue by region' }).click()
    await userEvent.keyboard('{Alt>}{Shift>}{ArrowRight}{/Shift}{/Alt}')
    await userEvent.keyboard('{Alt>}{Shift>}{ArrowDown}{/Shift}{/Alt}')
    await expect.poll(() => operations.length).toBe(2)
    expect(operations[1]).toMatchObject({ changes: [{ op: 'layout.apply', placements: expect.arrayContaining([
      { widget_id: BAR_WIDGET, layout: { x: 3, y: 0, w: 7, h: 4 } },
    ]) }] })
    // ECharts follows its container: the same drawing is resized in place.
    await expect.poll(() => chart.getWidth()).toBeGreaterThan(width)
    await expect.poll(() => chart.getHeight()).toBeGreaterThan(height)
    expect(getInstanceByDom(host)).toBe(chart)
    expect(queries()).toHaveLength(before)

    // One hundred container resizes with unchanged data and filters.
    for (let index = 0; index < 100; index += 1) {
      frame(screen).style.width = `${index % 2 ? 1200 : 1000}px`
      await new Promise(requestAnimationFrame)
    }
    frame(screen).style.width = '1100px'
    await expect.poll(() => Math.abs(chart.getWidth() - host.clientWidth)).toBeLessThanOrEqual(1)
    await wait(300)
    expect(queries()).toHaveLength(before)
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
  })

  it('NDV-GRID-008: a phone-width view returns to the original desktop layout with no save', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await expect.element(screen.getByRole('toolbar', { name: 'Add to dashboard' })).toBeVisible()
    frame(screen).style.width = '400px'
    await expect.poll(() => gridOf().getColumn()).toBe(1)
    await expect.element(screen.getByText('Widen the window to move or resize widgets.', { exact: false })).toBeVisible()
    expect(geometry(KPI_WIDGET).x).toBe(0)
    expect(geometry(TEXT_WIDGET).x).toBe(0)
    frame(screen).style.width = '1200px'
    await expect.poll(() => gridOf().getColumn()).toBe(12)
    await expect.poll(() => [geometry(KPI_WIDGET), geometry(TEXT_WIDGET)]).toEqual([{ x: 0, y: 0, w: 3, h: 2 }, { x: 3, y: 0, w: 9, h: 2 }])
    await wait(500)
    expect(operations).toHaveLength(0)
    expect(refused).toHaveLength(0)
  })

  describe('NDV-OPS-001: a save whose reply is lost', () => {
    async function addNote(screen: Awaited<ReturnType<typeof mount>>, text: string) {
      await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
      await screen.getByRole('button', { name: 'Edit' }).click()
      await screen.getByRole('toolbar', { name: 'Add to dashboard' }).getByRole('button', { name: 'Text' }).click()
      await page.getByRole('textbox').fill(text)
      await page.getByRole('button', { name: 'Add text' }).click()
    }
    const saves = () => calls.filter(call => call.url.endsWith(`/viz/dashboards/${DASHBOARD}/operations`))

    it('keeps a change that was saved, found by its receipt, and never sends it twice', async () => {
      replyLost = 'after-commit'
      const screen = await mount()
      await addNote(screen, 'Saved before the reply was lost')
      await expect.element(screen.getByText('Saved before the reply was lost')).toBeVisible()
      await expect.poll(() => calls.some(call => call.url.includes('/viz/operations/'))).toBe(true)
      expect(saves()).toHaveLength(1)
      expect(operations).toHaveLength(1)
      // The next change builds on the saved revision instead of conflicting with it.
      await screen.getByRole('toolbar', { name: 'Add to dashboard' }).getByRole('button', { name: 'Text' }).click()
      await page.getByRole('textbox').fill('Second note')
      await page.getByRole('button', { name: 'Add text' }).click()
      await expect.poll(() => operations.length).toBe(2)
      expect(refused).toHaveLength(0)
      expect(definition.pages[0].widgets.filter(widget => widget.kind === 'text')).toHaveLength(3)
    })

    it('takes back a change that was not saved', async () => {
      replyLost = 'before-commit'
      const screen = await mount()
      await addNote(screen, 'Lost before it was saved')
      await expect.poll(() => calls.some(call => call.url.includes('/viz/operations/'))).toBe(true)
      await expect.element(screen.getByText('Lost before it was saved')).not.toBeInTheDocument()
      expect(operations).toHaveLength(0)
      expect(saves()).toHaveLength(1)
    })

    it('reloads what is saved when the result cannot be confirmed yet', async () => {
      replyLost = 'after-commit'
      receiptsAvailable = false
      const screen = await mount()
      const reads = () => calls.filter(call => call.url.includes(`/viz/dashboards/${DASHBOARD}?`)).length
      await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
      const before = reads()
      await addNote(screen, 'Saved while receipts were down')
      await expect.poll(reads).toBeGreaterThan(before)
      await expect.element(screen.getByText('All changes saved')).toBeVisible()
      // The reload shows the server's copy, which has the change; nothing was sent again.
      await expect.element(screen.getByText('Saved while receipts were down')).toBeVisible()
      expect(saves()).toHaveLength(1)
    })
  })

  it('NDV-GRID-001: dragging Text from the toolbar onto a cell adds one widget there in one save', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    const source = screen.getByRole('toolbar', { name: 'Add to dashboard' }).getByRole('button', { name: 'Text' }).element() as HTMLElement
    const canvas = document.querySelector<HTMLElement>('.nova-dataviz-canvas')!
    const box = canvas.getBoundingClientRect()
    // The middle of the cell in column 5, row 4 (0-based), below the existing widgets.
    const at = { clientX: box.left + (5.5 * box.width) / 12, clientY: box.top + 4.5 * 72 }
    const transfer = new DataTransfer()
    source.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: transfer }))
    canvas.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer, ...at }))
    // A full-width text block cannot start at column 5; the preview shows where it will land.
    await expect.element(screen.getByTestId('drop-preview')).toBeInTheDocument()
    canvas.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer, ...at }))
    source.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer: transfer }))
    await expect.element(screen.getByTestId('drop-preview')).not.toBeInTheDocument()
    expect(operations).toHaveLength(0)

    await page.getByRole('textbox').fill('Dropped note')
    await page.getByRole('button', { name: 'Add text' }).click()
    await expect.poll(() => operations.length).toBe(1)
    const changes = operations[0].changes as { op: string; widget: { widget_id: string } }[]
    expect(changes).toHaveLength(1)
    expect(changes[0]).toMatchObject({ op: 'widget.add', page_id: PAGE,
      widget: { kind: 'text', text: 'Dropped note', layout: { x: 0, y: 4, w: 12, h: 2 } } })
    await expect.element(screen.getByText('Dropped note')).toBeVisible()
    expect(geometry(changes[0].widget.widget_id)).toEqual({ x: 0, y: 4, w: 12, h: 2 })
  })

  it('NDV-GRID-001: a dropped KPI previews its own size at the cell and opens the chart builder', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    const toolbar = screen.getByRole('toolbar', { name: 'Add to dashboard' })
    const source = toolbar.getByRole('button', { name: 'KPI' }).element() as HTMLElement
    const canvas = document.querySelector<HTMLElement>('.nova-dataviz-canvas')!
    const box = canvas.getBoundingClientRect()
    const transfer = new DataTransfer()
    source.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: transfer }))
    const at = { clientX: box.left + (8.5 * box.width) / 12, clientY: box.top + 3.5 * 72 }
    canvas.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer, ...at }))
    const preview = screen.getByTestId('drop-preview')
    await expect.element(preview).toBeInTheDocument()
    // A KPI is three columns by two rows, placed at the cell under the pointer.
    const shown = (preview.element() as HTMLElement).style
    expect([parseFloat(shown.left), parseFloat(shown.width)]).toEqual([expect.closeTo(800 / 12, 3), 25])
    expect([shown.top, shown.height]).toEqual([`${3 * 72}px`, `${2 * 72}px`])
    canvas.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer, ...at }))
    await expect.element(page.getByRole('heading', { name: 'New chart' })).toBeVisible()
    expect(operations).toHaveLength(0)
  })

  it('NDV-GRID-011: Duplicate adds a new widget ID that shares the original chart', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await screen.getByRole('button', { name: 'Units sold actions' }).click()
    await page.getByRole('menuitem', { name: 'Duplicate' }).click()
    await expect.poll(() => operations.length).toBe(1)
    const [change] = operations[0].changes as { op: string; page_id: string; widget: WidgetJson & { chart_ref: unknown } }[]
    expect(change).toMatchObject({ op: 'widget.add', page_id: PAGE, widget: { kind: 'chart', chart_ref: { chart_id: KPI_CHART, revision: 1 } } })
    expect(change.widget.widget_id).toMatch(/^[0-9a-f-]{36}$/)
    expect(change.widget.widget_id).not.toBe(KPI_WIDGET)
    await expect.poll(() => screen.container.querySelectorAll('[data-testid="kpi-value"]').length).toBe(2)
    // The original keeps its own ID and chart.
    expect(definition.pages[0].widgets.find(widget => widget.widget_id === KPI_WIDGET)).toMatchObject({ chart_ref: { chart_id: KPI_CHART, revision: 1 } })
  })

  it('NDV-GRID-013: Undo after someone else changed the dashboard shows the conflict and overwrites nothing', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await screen.getByRole('button', { name: 'Widget actions' }).click()
    await page.getByRole('menuitem', { name: 'Remove' }).click()
    await expect.poll(() => operations.length).toBe(1)
    await expect.element(screen.getByText('Plain notes')).not.toBeInTheDocument()

    otherUserCommits(() => {
      definition.title = 'Sales overview by Bea'
      definition.pages[0].widgets[0].layout = { x: 0, y: 0, w: 4, h: 2 }
    })
    const serverAfterBea = clone(definition)
    await screen.getByRole('button', { name: 'Undo' }).click()
    await expect.element(screen.getByText('Reloaded after someone else’s change')).toBeVisible()
    await expect.element(screen.getByRole('heading', { name: 'Sales overview by Bea' })).toBeVisible()
    expect(refused).toHaveLength(1)
    expect(refused[0]).toMatchObject({ expected_revision: 5, changes: [{ op: 'widget.add', widget: { widget_id: TEXT_WIDGET } }] })
    // No automatic retry with the newer revision, and the server keeps the other change.
    await wait(1000)
    expect(operations).toHaveLength(1)
    expect(refused).toHaveLength(1)
    expect(definition).toEqual(serverAfterBea)
    await expect.element(screen.getByText('Plain notes')).not.toBeInTheDocument()
    await expect.poll(() => geometry(KPI_WIDGET)).toEqual({ x: 0, y: 0, w: 4, h: 2 })
  })

  it('NDV-GRID-016: View mode has no resize handles and a drag moves or saves nothing', async () => {
    setWidgets([
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 0, y: 0, w: 3, h: 2 }),
      textWidget(TEXT_WIDGET, 'Plain notes', { x: 6, y: 0, w: 6, h: 2 }),
    ])
    publicationId = '99999999-0000-4000-8000-000000000001'
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    const grid = screen.container.querySelector<HTMLElement>('.grid-stack')!
    expect(grid.dataset.mode).toBe('view')
    expect(grid.classList).toContain('grid-stack-static')
    await userEvent.hover(item(KPI_WIDGET))
    expect(visibleResizeHandles()).toHaveLength(0)
    await mouseDrag(item(KPI_WIDGET).querySelector<HTMLElement>('.grid-stack-item-content')!, 320, 0)
    expect(geometry(KPI_WIDGET)).toEqual({ x: 0, y: 0, w: 3, h: 2 })
    expect(operations).toHaveLength(0)

    // The same drag in Edit mode does move and save, so the gesture above was real.
    await screen.getByRole('button', { name: 'Edit' }).click()
    await expect.poll(() => grid.classList.contains('grid-stack-static')).toBe(false)
    await userEvent.hover(item(TEXT_WIDGET))
    await userEvent.hover(item(KPI_WIDGET))
    await expect.poll(() => visibleResizeHandles(item(KPI_WIDGET)).length).toBeGreaterThan(0)
    await mouseDrag(item(KPI_WIDGET).querySelector<HTMLElement>('.grid-stack-item-content')!, 320, 0)
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ changes: [{ op: 'layout.apply' }] })
    expect(geometry(KPI_WIDGET).x).toBeGreaterThan(0)

    // Back in View mode the grid is static again.
    await screen.getByRole('button', { name: 'Done' }).click()
    await expect.poll(() => grid.classList.contains('grid-stack-static')).toBe(true)
    await userEvent.hover(item(TEXT_WIDGET))
    await userEvent.hover(item(KPI_WIDGET))
    expect(visibleResizeHandles()).toHaveLength(0)
    const x = geometry(KPI_WIDGET).x
    await mouseDrag(item(KPI_WIDGET).querySelector<HTMLElement>('.grid-stack-item-content')!, -320, 0)
    expect(geometry(KPI_WIDGET).x).toBe(x)
    expect(operations).toHaveLength(1)
  })
})

// ---- filters and interactions -------------------------------------------------------

describe('QA filters and interactions', { timeout: 30_000 }, () => {
  it('NDV-GRID-009: moving a chart keeps the same drawn chart and the viewer\'s selection', async () => {
    setWidgets([
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 0, y: 0, w: 6, h: 4 }),
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 6, y: 0, w: 3, h: 2 }),
    ])
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    // Marks select values while viewing; the selection is viewer state and survives Edit.
    await clickBar(BAR_WIDGET, 0, 10)
    await expect.element(screen.getByRole('button', { name: 'Clear selection East' })).toBeVisible()
    const barQueries = () => queries().filter(call => (call.body?.dimensions as unknown[] | undefined)?.length)
    // Edit shows the draft, so the chart is read and drawn again once for it.
    await screen.getByRole('button', { name: 'Edit' }).click()
    const grid = screen.container.querySelector<HTMLElement>('.grid-stack')!
    await expect.poll(() => grid.classList.contains('grid-stack-static')).toBe(false)
    await expect.poll(() => barQueries().some(call => call.body?.draft === true)).toBe(true)
    const { host, chart } = await echartsIn(BAR_WIDGET)
    await wait(500)
    const before = barQueries().length

    await userEvent.hover(item(BAR_WIDGET))
    await mouseDrag(item(BAR_WIDGET).querySelector<HTMLElement>('.grid-stack-item-content')!, 0, 300)
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ changes: [{ op: 'layout.apply' }] })
    expect(geometry(BAR_WIDGET).y).toBeGreaterThan(0)
    // GridStack moved the element; React kept the chart: same host, same live instance, no new query.
    expect(item(BAR_WIDGET).querySelector('[role="img"]')).toBe(host)
    expect(getInstanceByDom(host)).toBe(chart)
    expect(chart.getDom()).toBe(host)
    expect(barQueries()).toHaveLength(before)
    await expect.element(screen.getByRole('button', { name: 'Clear selection East' })).toBeVisible()
    await expect.poll(() => barOpacity(chart)).toEqual([1, 0.3])
  })

  it('NDV-INTER-011: the keyboard reaches a mark and Enter does what a click does', async () => {
    permissionList = ['view', 'interact']
    publicationId = '99999999-0000-4000-8000-000000000001'
    setWidgets([
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 0, y: 0, w: 6, h: 4 }),
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 6, y: 0, w: 3, h: 2 }),
    ])
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    const { chart } = await echartsIn(BAR_WIDGET)
    const group = screen.getByRole('group', { name: /Revenue by region: bar chart\. Use the arrow keys/ })
    ;(group.element() as HTMLElement).focus()
    expect(document.activeElement).toBe(group.element())
    await userEvent.keyboard('{ArrowRight}')
    await expect.element(group.getByText('East, 1 of 2')).toBeInTheDocument()
    await userEvent.keyboard('{ArrowRight}')
    await expect.element(group.getByText('West, 2 of 2')).toBeInTheDocument()
    await userEvent.keyboard('{ArrowLeft}')
    await expect.element(group.getByText('East, 1 of 2')).toBeInTheDocument()
    await userEvent.keyboard('{Enter}')
    // The same result as clicking the East bar: one selection, the KPI re-queried with it, the source dimmed.
    await expect.element(screen.getByRole('button', { name: 'Clear selection East' })).toBeVisible()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('4,000')
    await expect.poll(() => barOpacity(chart)).toEqual([1, 0.3])
    // Escape leaves the mark; a chart that cannot filter is not a keyboard stop.
    ;(group.element() as HTMLElement).focus()
    await userEvent.keyboard('{ArrowRight}{Escape}')
    await expect.element(group.getByText('East, 1 of 2')).not.toBeInTheDocument()
    expect(item(KPI_WIDGET).querySelector('[role="group"][tabindex="0"]')).toBeNull()
  })

  it('NDV-FILTER-021 / NDV-INTER-010: a cross-filter highlights its source without re-querying it, and reset clears only viewer state', async () => {
    permissionList = ['view', 'interact']
    publicationId = '99999999-0000-4000-8000-000000000001'
    setWidgets([
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 0, y: 0, w: 6, h: 4 }),
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 6, y: 0, w: 3, h: 2 }),
    ])
    const published = clone(definition)
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    const { chart } = await echartsIn(BAR_WIDGET)
    const barQueries = () => queries().filter(call => (call.body?.dimensions as unknown[] | undefined)?.length)
    await expect.poll(() => barQueries().length).toBe(1)
    expect(barOpacity(chart)).toEqual([1, 1])

    await clickBar(BAR_WIDGET, 0, 10)
    await expect.element(screen.getByText('Selected', { exact: false })).toBeVisible()
    await expect.element(screen.getByRole('button', { name: 'Clear selection East' })).toBeVisible()
    // The other widget re-queries with the selection; it never widens beyond the role.
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('4,000')
    const filtered = queries().filter(call => measureOf(call.body) === 'units' && JSON.stringify(call.body?.filters ?? null).includes('East'))
    expect(filtered).toHaveLength(1)
    expect(filtered[0].body).toMatchObject({ filters: { expression: { type: 'predicate', op: 'eq', field: { field_id: 'region' }, values: [{ type: 'string', value: 'East' }] } } })
    // Highlight only on the source: no new query, unselected marks dimmed.
    await expect.poll(() => barOpacity(chart)).toEqual([1, 0.3])
    expect(barQueries()).toHaveLength(1)
    expect(JSON.stringify(barQueries()[0].body)).not.toContain('East')

    const sentBeforeReset = queries().length
    await screen.getByRole('button', { name: 'Reset' }).click()
    await expect.element(screen.getByRole('button', { name: 'Clear selection East' })).not.toBeInTheDocument()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    await expect.poll(() => barOpacity(chart)).toEqual([1, 1])
    await wait(400)
    // Anything sent after the reset carries no selection predicate.
    expect(queries().slice(sentBeforeReset).every(call => !JSON.stringify(call.body).includes('East'))).toBe(true)
    // Viewer state only: nothing was written to the published dashboard.
    expect(calls.filter(call => call.method !== 'GET' && !call.url.endsWith('/query') && !call.url.includes('/presence'))).toEqual([])
    expect(operations).toHaveLength(0)
    expect(definition).toEqual(published)
  })

  it('NDV-FILTER-022: of two rapid filter values only the newest generation is rendered', async () => {
    definition.filters = [{
      filter_id: 'f0000000-0000-4000-8000-000000000001', title: 'Region', kind: 'text_entry',
      field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'region' }, scope: { kind: 'global' },
      mappings: [], defaults: [], allow_all: true, include_null: false, searchable: true, depends_on: [],
    }]
    let releaseOlder: () => void = () => undefined
    let olderAnswered = false
    answer = body => {
      const filters = JSON.stringify(body.filters ?? null)
      if (filters.includes('Jakarta')) {
        return new Promise<Response>(resolve => { releaseOlder = () => { olderAnswered = true; resolve(kpiResult('111')) } })
      }
      if (filters.includes('Bali')) return kpiResult('222')
      if (filters.includes('Medan')) return kpiResult('333')
      return kpiResult('15257')
    }
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    const input = screen.getByRole('textbox', { name: 'Region contains' })
    const withValue = (value: string) => queries().filter(call => JSON.stringify(call.body?.filters ?? null).includes(value))

    // Two values typed in quick succession: only the newer one is queried and shown.
    await input.fill('Medan')
    await input.fill('Bali')
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('222')
    expect(withValue('Medan')).toHaveLength(0)

    // An older query that answers after a newer one never replaces it.
    await input.fill('Jakarta')
    await expect.poll(() => withValue('Jakarta').length).toBe(1)
    await input.fill('Bali')
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('222')
    releaseOlder()
    await expect.poll(() => olderAnswered).toBe(true)
    await wait(500)
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('222')
    expect(screen.container.textContent).not.toContain('111')
  })
})

// ---- query cache and failures -------------------------------------------------------

describe('QA query cache', () => {
  it('NDV-CACHE-005: four widgets of one chart send one query and each renders the value', async () => {
    setWidgets([0, 1, 2, 3].map(index => chartWidget(`ffffffff-0000-4000-8000-00000000010${index}`, KPI_CHART, { x: index * 3, y: 0, w: 3, h: 2 })))
    const screen = await mount()
    await expect.poll(() => [...screen.container.querySelectorAll('[data-testid="kpi-value"]')].map(node => node.textContent))
      .toEqual(['15,257', '15,257', '15,257', '15,257'])
    await wait(300)
    expect(queries()).toHaveLength(1)
  })

  it('NDV-CACHE-006: one widget timing out shows its own message while the others render', async () => {
    answer = body => measureOf(body) === 'revenue' && !(body.dimensions as unknown[] | undefined)?.length
      ? json({ detail: 'The query exceeded its time limit.' }, 504)
      : defaultAnswer(body)
    setWidgets([
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 0, y: 0, w: 3, h: 2 }),
      chartWidget(REVENUE_WIDGET, REVENUE_CHART, { x: 3, y: 0, w: 3, h: 2 }),
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 6, y: 0, w: 6, h: 4 }),
    ])
    charts[BAR_CHART] = barChart(BAR_CHART, 'Units by region', 'units')
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    const timedOut = item(REVENUE_WIDGET)
    await expect.poll(() => timedOut.querySelector('[role="alert"]')?.textContent ?? '').toContain('The query timed out. Add a filter or reduce the row limit.')
    expect(timedOut.querySelector('[data-testid="kpi-value"]')).toBeNull()
    expect(item(KPI_WIDGET).querySelector('[role="alert"]')).toBeNull()
    await echartsIn(BAR_WIDGET)
    expect(item(BAR_WIDGET).querySelector('[role="alert"]')).toBeNull()
    expect(screen.container.querySelectorAll('[role="alert"]')).toHaveLength(1)
  })
})

// ---- operations ---------------------------------------------------------------------

describe('QA dependency failure', () => {
  it('NDV-OPS-010: a failed refresh shows an error, never the earlier rows as current', async () => {
    definition.filters = [{
      filter_id: 'f0000000-0000-4000-8000-000000000001', title: 'Region', kind: 'text_entry',
      field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'region' }, scope: { kind: 'global' },
      mappings: [], defaults: [], allow_all: true, include_null: false, searchable: true, depends_on: [],
    }]
    let down = false
    answer = body => down ? json({ detail: 'Analytical engine unavailable' }, 503) : defaultAnswer(body)
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')

    // The same request refreshed while the engine is down.
    down = true
    await screen.client.invalidateQueries({ queryKey: ['native-dataviz', 'chart-data'] })
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The chart could not be loaded.')
    expect(screen.container.querySelector('[data-testid="kpi-value"]')).toBeNull()
    expect(screen.container.textContent).not.toContain('15,257')

    // Retry while still down stays an error; after recovery the fresh value shows.
    const sent = queries().length
    await screen.getByRole('button', { name: 'Retry' }).click()
    await expect.poll(() => queries().length).toBe(sent + 1)
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The chart could not be loaded.')
    down = false
    await screen.getByRole('button', { name: 'Retry' }).click()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')

    // A new filter value whose query fails does not keep showing the unfiltered value.
    down = true
    await screen.getByRole('textbox', { name: 'Region contains' }).fill('East')
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The chart could not be loaded.')
    expect(screen.container.querySelector('[data-testid="kpi-value"]')).toBeNull()
  })
})

// ---- revisions ----------------------------------------------------------------------

describe('QA multiple tabs', { timeout: 40_000 }, () => {
  it('NDV-REVIS-010: two editor tabs of one person converge without a stale local override', async () => {
    const tabA = await mount()
    const tabB = await mount()
    await expect.poll(() => document.querySelectorAll('[data-testid="kpi-value"]').length).toBe(2)
    const a = tabA.container
    const b = tabB.container
    const A = page.elementLocator(a)
    const B = page.elementLocator(b)
    // Both tabs have taken their first events page before A edits.
    await expect.poll(() => calls.filter(call => call.url.includes('/events')).length).toBeGreaterThanOrEqual(2)

    await A.getByRole('button', { name: 'Edit' }).click()
    await A.getByRole('button', { name: 'Widget actions' }).click()
    await page.getByRole('menuitem', { name: 'Remove' }).click()
    await expect.poll(() => operations.length).toBe(1)
    await A.getByRole('button', { name: 'Done' }).click()
    expect(revision).toBe(5)

    // B picks up A's committed change from the event stream.
    await expect.poll(() => b.textContent?.includes('Plain notes'), { timeout: 10_000 }).toBe(false)
    expect(a.textContent).not.toContain('Plain notes')

    // B's next edit is based on A's revision and does not bring back the removed widget.
    await B.getByRole('button', { name: 'Edit' }).click()
    await B.getByTestId('kpi-value').click()
    await userEvent.keyboard('{Alt>}{ArrowRight}{/Alt}')
    await expect.poll(() => operations.length).toBe(2)
    expect(refused).toHaveLength(0)
    expect(operations[1]).toMatchObject({ expected_revision: 5 })
    const placements = (operations[1].changes as { placements: { widget_id: string }[] }[])[0].placements
    expect(placements.map(placement => placement.widget_id)).toEqual([KPI_WIDGET])
    expect(definition.pages[0].widgets.map(widget => widget.widget_id)).toEqual([KPI_WIDGET])
    await B.getByRole('button', { name: 'Done' }).click()

    // A then converges on B's revision too.
    await expect.poll(() => geometry(KPI_WIDGET, a), { timeout: 10_000 }).toEqual({ x: 1, y: 0, w: 3, h: 2 })
  })

  it('NDV-REVIS-010: a tab that edits before seeing the other tab’s change is refused, reloads and does not resubmit', async () => {
    const tabA = await mount()
    const tabB = await mount()
    const A = page.elementLocator(tabA.container)
    const B = page.elementLocator(tabB.container)
    await expect.poll(() => document.querySelectorAll('[data-testid="kpi-value"]').length).toBe(2)
    await A.getByRole('button', { name: 'Edit' }).click()
    await B.getByRole('button', { name: 'Edit' }).click()
    await A.getByRole('button', { name: 'Widget actions' }).click()
    await page.getByRole('menuitem', { name: 'Remove' }).click()
    await expect.poll(() => operations.length).toBe(1)
    await A.getByRole('button', { name: 'Done' }).click()
    // B still shows the old draft and moves the text widget that A removed.
    expect(tabB.container.textContent).toContain('Plain notes')
    await B.getByText('Plain notes').click()
    await userEvent.keyboard('{Alt>}{ArrowDown}{/Alt}')
    await expect.poll(() => refused.length).toBe(1)
    expect(refused[0]).toMatchObject({ expected_revision: 4 })
    await expect.element(B.getByText('Reloaded after someone else’s change')).toBeVisible()
    await expect.poll(() => tabB.container.textContent?.includes('Plain notes')).toBe(false)
    await wait(1000)
    expect(operations).toHaveLength(1)
    expect(refused).toHaveLength(1)
    expect(definition.pages[0].widgets.map(widget => widget.widget_id)).toEqual([KPI_WIDGET])
  })
})

// ---- scale --------------------------------------------------------------------------

describe('QA scale', { timeout: 90_000 }, () => {
  it('NDV-PERF-003: fifty widgets over three pages render and query lazily, page by page, with stable DOM and chart instances', async () => {
    const pages: PageJson[] = []
    let index = 0
    for (const [p, count] of [17, 17, 16].entries()) {
      const widgets: WidgetJson[] = []
      for (let slot = 0; slot < count; slot += 1) {
        index += 1
        const id = `cccccccc-0000-4000-8000-${String(index).padStart(12, '0')}`
        const measure = `units_${index}`
        charts[id] = index % 5 === 0 ? barChart(id, `Chart ${index}`, measure) : kpiChart(id, `Chart ${index}`, measure)
        widgets.push(chartWidget(`ffffffff-0000-4000-8000-${String(index).padStart(12, '0')}`, id,
          { x: (slot % 4) * 3, y: Math.floor(slot / 4) * 3, w: 3, h: 3 }))
      }
      pages.push({ page_id: `eeeeeeee-0000-4000-8000-00000000000${p + 1}`, title: `Page ${p + 1}`, widgets })
    }
    definition.pages = pages
    const measuresOf = (sheet: PageJson) => sheet.widgets.map(widget => `units_${Number(widget.widget_id.slice(-12))}`)
    const sentMeasures = () => queries().map(call => measureOf(call.body))
    const screen = await mount(1200, 2400)
    const settled = async (sheet: PageJson) => {
      const kpis = sheet.widgets.filter(widget => Number(widget.widget_id.slice(-12)) % 5 !== 0).length
      const bars = sheet.widgets.length - kpis
      await expect.poll(() => screen.container.querySelectorAll('[data-testid="kpi-value"]').length, { timeout: 15_000 }).toBe(kpis)
      await expect.poll(() => [...screen.container.querySelectorAll<HTMLElement>('[role="img"]')]
        .filter(node => getInstanceByDom(node)).length, { timeout: 15_000 }).toBe(bars)
      expect(gridOf().engine.nodes).toHaveLength(sheet.widgets.length)
      expect(screen.container.querySelectorAll('.grid-stack > .grid-stack-item')).toHaveLength(sheet.widgets.length)
      // Every chart on screen belongs to this page; no other page's chart is drawn.
      const ids = new Set(sheet.widgets.map(widget => widget.widget_id))
      expect([...screen.container.querySelectorAll('.grid-stack > .grid-stack-item')].every(node => ids.has(node.getAttribute('gs-id')!))).toBe(true)
      await wait(300)
      return { nodes: document.querySelectorAll('*').length, charts: document.querySelectorAll('[_echarts_instance_]').length }
    }

    const first = await settled(pages[0])
    // Only the active page has queried.
    expect(new Set(sentMeasures())).toEqual(new Set(measuresOf(pages[0])))
    expect(queries()).toHaveLength(17)

    const heap = () => (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize
    const samples: { nodes: number; charts: number }[][] = []
    const heaps: (number | undefined)[] = []
    for (let cycle = 0; cycle < 2; cycle += 1) {
      const round: { nodes: number; charts: number }[] = []
      for (const [p, sheet] of pages.entries()) {
        if (cycle || p) await screen.getByRole('button', { name: sheet.title, exact: true }).click()
        round.push(cycle || p ? await settled(sheet) : first)
        if (cycle === 0 && p === 1) {
          expect(new Set(sentMeasures())).toEqual(new Set([...measuresOf(pages[0]), ...measuresOf(pages[1])]))
        }
      }
      samples.push(round)
      heaps.push(heap())
    }
    // Each page's queries ran once; revisiting reads the cache.
    expect(queries()).toHaveLength(50)
    expect(new Set(sentMeasures()).size).toBe(50)
    // The same page shows the same number of elements and chart instances each time: nothing accumulates.
    expect(samples[1]).toEqual(samples[0])
    const [early, late] = heaps
    if (early !== undefined && late !== undefined) expect(late - early).toBeLessThan(32 * 1024 * 1024)
  })
})
