import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { getInstanceByDom } from 'echarts/core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cdp, page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/auth-store'
import type { ChartDefinition, DashboardDefinitionV2, DatasetField, Geometry } from '../contracts'
import { initialState, toChart } from '../charts/builder-model'
import { DashboardEditor } from './editor'
import { NATIVE_DASHBOARD_ACTIVITY, NATIVE_DASHBOARD_CHANGED, NATIVE_DASHBOARD_OPEN, type LinkedSelection, type NativeDashboardActivity, type NativeDashboardOpen } from './events'
import { cellOf, upcastDashboard } from './layout'
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
/** Change notifications unreachable, as when the polling channel drops. */
let eventsDown = false
/** One events page to return instead of the server's, e.g. a repeat or a late page. */
let eventsOnce: Json | null = null
/** One head read to answer with an older snapshot, as a lagging read would. */
let staleHeadOnce: Json | null = null
const receipts: Record<string, Json> = {}
let definition: Json & { pages: PageJson[]; title: string }
/** Answers a dataset query; the default derives the value from the request. */
let answer: (body: Json) => Promise<Response> | Response
const exported: Json[] = []

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
  return { resource_id: DASHBOARD, revision, metadata_revision: revision, grants_revision: 1, publication_id: publicationId, definition: upcastDashboard(definition as unknown as DashboardDefinitionV2) }
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
    if (change.op === 'elements.arrange' && target) {
      for (const placement of change.placements as { element_id: string; geometry: Geometry }[]) {
        const widget = target.widgets.find(item => item.widget_id === placement.element_id)
        if (widget) widget.layout = cellOf(placement.geometry)
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
    if (eventsDown) return json({ detail: 'Service unavailable' }, 503)
    if (eventsOnce) { const page = eventsOnce; eventsOnce = null; return json(page) }
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
  if (url.includes(`/viz/dashboards/${DASHBOARD}?`)) {
    if (staleHeadOnce) { const head = staleHeadOnce; staleHeadOnce = null; return json(head) }
    return json(snapshot())
  }
  const chart = /\/viz\/charts\/([0-9a-f-]+)\?/.exec(url)
  if (chart && charts[chart[1]]) return json({ resource_id: chart[1], revision: 1, metadata_revision: 1, grants_revision: 1, publication_id: null, definition: charts[chart[1]] })
  if (url.endsWith(`/viz/datasets/${DATASET}/query`)) return answer(body!)
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/exports`)) {
    exported.push(body!)
    return permissionList.includes('export') ? json({ allowed: true }) : json({ detail: 'Not allowed' }, 403)
  }
  // Dataset definitions, presence and comments are not part of these scenarios.
  return json({ detail: 'Not found' }, 404)
}

async function mount(width = 1200, height = 720, open: { dashboardId?: string; initialSelections?: LinkedSelection[] } = {}) {
  const client = new QueryClient()
  clients.push(client)
  const screen = await render(<QueryClientProvider client={client}><TooltipProvider>
    <div data-testid='frame' style={{ height, width, display: 'flex' }}>
      <DashboardEditor dashboardId={open.dashboardId ?? DASHBOARD} workspaceId='personal' onBack={() => undefined}
        initialSelections={open.initialSelections} />
    </div>
  </TooltipProvider></QueryClientProvider>)
  return Object.assign(screen, { client })
}

function setWidgets(widgets: WidgetJson[]) {
  definition.pages = [{ page_id: PAGE, title: 'Overview', widgets }]
}

const item = (id: string, root: ParentNode = document) => root.querySelector<HTMLElement>(`[data-element-id="${id}"]`)!
/** The 12-column cell an item is drawn at, read back from its pixels on screen. */
const geometry = (id: string, root: ParentNode = document) => {
  const element = item(id, root)
  const frameBox = element.closest('[data-canvas-frame]')!.getBoundingClientRect()
  const box = element.getBoundingClientRect()
  const scale = frameBox.width / 1200
  return cellOf({ x: (box.left - frameBox.left) / scale, y: (box.top - frameBox.top) / scale, w: box.width / scale, h: box.height / scale, rotation: 0 })
}
const frame = (screen: Awaited<ReturnType<typeof mount>>) => screen.getByTestId('frame').element() as HTMLElement
const canvasMode = (root: ParentNode = document) => root.querySelector<HTMLElement>('.nova-dataviz-canvas')?.dataset.mode

/** Presses, moves and releases a pointer on an item, the way a person drags it. */
async function pointerDrag(target: HTMLElement, dx: number, dy: number) {
  const box = target.getBoundingClientRect()
  const x = box.left + box.width / 2
  const y = box.top + 12
  const init = (cx: number, cy: number, buttons: number): PointerEventInit => ({ bubbles: true, cancelable: true, clientX: cx, clientY: cy,
    button: 0, buttons, pointerId: 1, pointerType: 'mouse', isPrimary: true, view: window })
  target.dispatchEvent(new PointerEvent('pointerdown', init(x, y, 1)))
  for (let step = 1; step <= 12; step += 1) {
    document.dispatchEvent(new PointerEvent('pointermove', init(x + dx * step / 12, y + dy * step / 12, 1)))
    await wait(16)
  }
  document.dispatchEvent(new PointerEvent('pointerup', init(x + dx, y + dy, 0)))
  await wait(300)
}

const visibleResizeHandles = (root: ParentNode = document) => [...root.querySelectorAll<HTMLElement>('[data-handle]')]
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
  eventsDown = false
  eventsOnce = null
  staleHeadOnce = null
  for (const key of Object.keys(receipts)) delete receipts[key]
  answer = defaultAnswer
  exported.length = 0
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

    // A committed resize of the bar chart: one column wider, then one baseline row taller.
    await screen.getByRole('heading', { name: 'Revenue by region' }).click()
    await userEvent.keyboard('{Alt>}{Shift>}{ArrowRight}{/Shift}{/Alt}')
    await userEvent.keyboard('{Alt>}{Shift>}{ArrowDown}{/Shift}{/Alt}')
    await expect.poll(() => operations.length).toBe(2)
    expect(operations[1]).toMatchObject({ changes: [{ op: 'elements.arrange', placements: [
      { element_id: BAR_WIDGET, geometry: { x: 306, y: 6, w: 688, h: 212, rotation: 0 } },
    ] }] })
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
    await expect.poll(() => document.querySelector('[data-layout="phone"]')).not.toBeNull()
    await expect.element(screen.getByText('Widen the window to move or resize items.', { exact: false })).toBeVisible()
    // One column: both items start at the same left edge and are as wide.
    const kpiBox = item(KPI_WIDGET).getBoundingClientRect()
    const textBox = item(TEXT_WIDGET).getBoundingClientRect()
    expect([Math.round(kpiBox.left), Math.round(kpiBox.width)]).toEqual([Math.round(textBox.left), Math.round(textBox.width)])
    expect(textBox.top).toBeGreaterThanOrEqual(kpiBox.bottom - 1)
    frame(screen).style.width = '1200px'
    await expect.poll(() => document.querySelector('[data-layout="frame"]')).not.toBeNull()
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
    const canvas = document.querySelector<HTMLElement>('[data-canvas-frame]')!
    const box = canvas.getBoundingClientRect()
    const scale = box.width / 1200
    // The middle of the cell in column 5, row 4 (0-based), below the existing widgets.
    const at = { clientX: box.left + (5.5 * box.width) / 12, clientY: box.top + 4.5 * 72 * scale }
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
    const canvas = document.querySelector<HTMLElement>('[data-canvas-frame]')!
    const box = canvas.getBoundingClientRect()
    const scale = box.width / 1200
    const transfer = new DataTransfer()
    source.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: transfer }))
    const at = { clientX: box.left + (8.5 * box.width) / 12, clientY: box.top + 3.5 * 72 * scale }
    canvas.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer, ...at }))
    const preview = screen.getByTestId('drop-preview')
    await expect.element(preview).toBeInTheDocument()
    // A KPI is three columns by two rows, placed at the cell under the pointer.
    const shown = (preview.element() as HTMLElement).style
    // Drawn as the KPI's own cell: column 8 (0-based), three columns wide, at row 3, two rows tall.
    expect([parseFloat(shown.left), parseFloat(shown.width)].map(value => value / scale)).toEqual([expect.closeTo(806, 1), expect.closeTo(288, 1)])
    expect([parseFloat(shown.top), parseFloat(shown.height)].map(value => value / scale)).toEqual([expect.closeTo(222, 1), expect.closeTo(132, 1)])
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

    // This editor has not heard of Bea's change yet. Holding the change feed back
    // keeps its poll (every 4 s) from loading her revision before Undo is pressed,
    // which on a slow run turned the refused undo into an accepted one.
    eventsDown = true
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
    expect(canvasMode()).toBe('view')
    await userEvent.hover(item(KPI_WIDGET))
    expect(visibleResizeHandles()).toHaveLength(0)
    await pointerDrag(item(KPI_WIDGET), 320, 0)
    expect(geometry(KPI_WIDGET)).toEqual({ x: 0, y: 0, w: 3, h: 2 })
    expect(operations).toHaveLength(0)

    // The same drag in Edit mode does move and save, so the gesture above was real.
    await screen.getByRole('button', { name: 'Edit' }).click()
    await expect.poll(() => canvasMode()).toBe('edit')
    await userEvent.click(item(KPI_WIDGET))
    // The selected item shows its eight resize handles.
    await expect.poll(() => visibleResizeHandles(item(KPI_WIDGET)).length).toBe(8)
    await pointerDrag(item(KPI_WIDGET), 320, 0)
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ changes: [{ op: 'elements.arrange', placements: [{ element_id: KPI_WIDGET }] }] })
    expect(geometry(KPI_WIDGET).x).toBeGreaterThan(0)
    // Snapping kept the move on the page grid's 8-pixel baseline, measured from its margin.
    const saved = (operations[0].changes as { placements: { geometry: Geometry }[] }[])[0].placements[0].geometry
    expect([(saved.x - 6) % 8, (saved.y - 6) % 8, saved.w, saved.h]).toEqual([0, 0, 288, 132])

    // Back in View mode nothing can be dragged again.
    await screen.getByRole('button', { name: 'Done' }).click()
    await expect.poll(() => canvasMode()).toBe('view')
    await userEvent.hover(item(KPI_WIDGET))
    expect(visibleResizeHandles()).toHaveLength(0)
    const x = geometry(KPI_WIDGET).x
    await pointerDrag(item(KPI_WIDGET), -320, 0)
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
    await expect.poll(() => canvasMode()).toBe('edit')
    await expect.poll(() => barQueries().some(call => call.body?.draft === true)).toBe(true)
    const { host, chart } = await echartsIn(BAR_WIDGET)
    await wait(500)
    const before = barQueries().length

    await pointerDrag(item(BAR_WIDGET), 0, 300)
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ changes: [{ op: 'elements.arrange' }] })
    expect(geometry(BAR_WIDGET).y).toBeGreaterThan(0)
    // The item moved; React kept the chart: same host, same live instance, no new query.
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
    // NDV-ACL-020: interact without edit offers no way into the draft.
    await expect.element(screen.getByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
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

  it('NDV-CHART-020: a chart image is taken only once the filtered result has finished drawing', async () => {
    permissionList = ['view', 'interact', 'export']
    const UNITS_CHART = 'cccccccc-0000-4000-8000-000000000009'
    const UNITS_WIDGET = 'ffffffff-0000-4000-8000-000000000009'
    charts[UNITS_CHART] = barChart(UNITS_CHART, 'Units by region', 'units')
    setWidgets([
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 0, y: 0, w: 6, h: 4 }),
      chartWidget(UNITS_WIDGET, UNITS_CHART, { x: 6, y: 0, w: 6, h: 4 }),
    ])
    // The filtered units query is held until the test lets it answer.
    let release = () => undefined as void
    const held = new Promise<void>(resolve => { release = resolve })
    answer = async body => {
      const filtered = JSON.stringify(body.filters ?? null).includes('East')
      if (measureOf(body) === 'units' && filtered) {
        await held
        return json({ dataset_id: DATASET, revision: 1, columns: [
          { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
          { key: 'm0', name: 'units', logical_type: 'decimal', role: 'measure', field_id: 'units', aggregation: 'sum' },
        ], rows: [[{ type: 'string', value: 'East' }, { type: 'decimal', value: '4000' }]], truncated: false })
      }
      return defaultAnswer(body)
    }
    const saved: { name: string; blob: Blob; categories: unknown }[] = []
    const blobs: Blob[] = []
    vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { blobs.push(blob as Blob); return 'blob:test' })
    await page.viewport(1280, 900)
    const screen = await mount(1200, 720)
    const { chart: units } = await echartsIn(UNITS_WIDGET)
    const categories = () => ((units.getOption() as { xAxis: { data: unknown[] }[] }).xAxis[0].data)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      // What the chart showed at the moment the file was made.
      saved.push({ name: this.download, blob: blobs[blobs.length - 1], categories: categories() })
    })
    await expect.poll(categories).toEqual(['East', 'West'])

    await clickBar(BAR_WIDGET, 0, 10) // filter the dashboard to East
    await expect.element(screen.getByRole('button', { name: 'Clear selection East' })).toBeVisible()
    await expect.poll(() => item(UNITS_WIDGET).querySelector('[data-updating]')).not.toBeNull()
    await screen.getByRole('button', { name: 'Download Units by region' }).click()
    await page.getByRole('menuitem', { name: 'Image (PNG)' }).click()
    // While the filtered result is still on its way, nothing is checked or saved.
    await wait(600)
    expect(saved).toHaveLength(0)
    expect(exported).toHaveLength(0)

    release()
    await expect.poll(() => saved.length, { timeout: 15_000 }).toBe(1)
    expect(saved[0].name).toBe('Units by region.png')
    expect(saved[0].categories).toEqual(['East'])
    expect(exported).toEqual([{ format: 'png', chart_id: UNITS_CHART }])
    // A complete image: the drawn chart plus its title band and margins.
    const image = await createImageBitmap(saved[0].blob)
    const canvas = item(UNITS_WIDGET).querySelector('canvas')!
    expect(image.width).toBeGreaterThan(canvas.width)
    expect(image.height).toBeGreaterThan(canvas.height)
    expect(saved[0].blob.type).toBe('image/png')
  })

  it('NDV-INTER-013: one viewer\'s cross-filter stays theirs; another viewer of the same shared dashboard is unaffected', async () => {
    permissionList = ['view', 'interact']
    publicationId = '99999999-0000-4000-8000-000000000001'
    setWidgets([
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 0, y: 0, w: 6, h: 4 }),
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 6, y: 0, w: 3, h: 2 }),
    ])
    const published = clone(definition)
    // Two people with the same dashboard open, each with their own session state.
    const viewerA = await mount(1200, 420)
    const viewerB = await mount(1200, 420)
    // Each viewer's own part of the page.
    const inA = page.elementLocator(viewerA.container)
    const inB = page.elementLocator(viewerB.container)
    await expect.element(inA.getByTestId('kpi-value')).toHaveTextContent('15,257')
    await expect.element(inB.getByTestId('kpi-value')).toHaveTextContent('15,257')
    const before = calls.length

    await clickBar(BAR_WIDGET, 0, 10) // the first bar chart in the page is viewer A's
    await expect.element(inA.getByRole('button', { name: 'Clear selection East' })).toBeVisible()
    await expect.element(inA.getByTestId('kpi-value')).toHaveTextContent('4,000')
    await wait(600)
    // Viewer B keeps the full dashboard: no selection chip, the unfiltered total.
    await expect.element(inB.getByRole('button', { name: 'Clear selection East' })).not.toBeInTheDocument()
    await expect.element(inB.getByTestId('kpi-value')).toHaveTextContent('15,257')
    const sent = calls.slice(before)
    // The selection travels only in viewer A's own read queries, never in a write.
    expect(sent.filter(call => call.method !== 'GET' && !call.url.endsWith('/query') && !call.url.includes('/presence'))).toEqual([])
    expect(sent.filter(call => call.url.includes('/presence')).every(call => !JSON.stringify(call.body).includes('East'))).toBe(true)
    expect(operations).toHaveLength(0)
    expect(definition).toEqual(published)
  })

  it('NDV-INTER-008: a linked dashboard opens with the mapped value, under the viewer\'s own access', async () => {
    permissionList = ['view', 'interact']
    publicationId = '99999999-0000-4000-8000-000000000001'
    const OTHER = '99999999-0000-4000-8000-0000000000d1'
    const target = { dataset_id: DATASET, dataset_revision: 1, field_id: 'region' }
    setWidgets([
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 0, y: 0, w: 6, h: 4 }),
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 6, y: 0, w: 3, h: 2 }),
    ])
    definition.actions = [{ action_id: '99999999-0000-4000-8000-0000000000d2', kind: 'navigate', source_widget_id: BAR_WIDGET,
      target_widget_ids: [], target_dashboard_id: OTHER, target_workspace_id: 'personal', label: 'Open regional detail',
      mappings: [{ source: { dataset_id: DATASET, dataset_revision: 1, field_id: 'region' }, target }], run_on: 'menu' }]
    const opened: NativeDashboardOpen[] = []
    const listener = (event: Event) => opened.push((event as CustomEvent<NativeDashboardOpen>).detail)
    window.addEventListener(NATIVE_DASHBOARD_OPEN, listener)
    try {
      const screen = await mount()
      await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
      await clickBar(BAR_WIDGET, 0, 10)
      await page.getByRole('menuitem', { name: 'Open regional detail' }).click()
      expect(opened).toEqual([{ dashboardId: OTHER, workspaceId: 'personal',
        selections: [{ field: target, value: { type: 'string', value: 'East' }, label: 'East' }] }])
    } finally {
      window.removeEventListener(NATIVE_DASHBOARD_OPEN, listener)
    }
    await cleanup()

    // The target applies the carried value to its own charts, as a selection the viewer can clear.
    definition.actions = []
    const target_view = await mount(1200, 720, { initialSelections: opened[0].selections })
    await expect.element(target_view.getByTestId('kpi-value')).toHaveTextContent('4,000')
    await expect.element(target_view.getByRole('button', { name: 'Clear selection East' })).toBeVisible()
    expect(operations).toHaveLength(0)
    await cleanup()

    // A dashboard the viewer cannot open stays closed: the server refuses it and nothing is queried.
    const sent = queries().length
    const refused = await mount(1200, 720, { dashboardId: OTHER, initialSelections: opened[0].selections })
    await expect.element(refused.getByText('This dashboard is unavailable')).toBeVisible()
    expect(queries()).toHaveLength(sent)
  })

  it('NDV-INTER-014: a selection on a field whose source changed shows the change, never other rows', async () => {
    permissionList = ['view', 'interact']
    publicationId = '99999999-0000-4000-8000-000000000001'
    // The region column changed under the saved dataset: the server refuses every query that uses it.
    let changed = false
    answer = body => changed && JSON.stringify(body.filters ?? null).includes('region')
      ? json({ detail: 'SCHEMA_STALE: inspect the source again' }, 409)
      : defaultAnswer(body)
    setWidgets([
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 0, y: 0, w: 6, h: 4 }),
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 6, y: 0, w: 3, h: 2 }),
    ])
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    await echartsIn(BAR_WIDGET)
    changed = true
    await clickBar(BAR_WIDGET, 0, 10)
    const kpi = item(KPI_WIDGET)
    await expect.poll(() => kpi.querySelector('[role="alert"]')?.textContent ?? '').toContain(
      'The data source changed since this chart was saved. Someone who can edit the dashboard can review it in the Data tab.')
    // Neither the unfiltered total nor any other value stands in for the refused answer.
    expect(kpi.querySelector('[data-testid="kpi-value"]')).toBeNull()
    expect(item(BAR_WIDGET).querySelector('[role="alert"]')).toBeNull()
    expect(operations).toHaveLength(0)
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
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The data engine is not responding. Retry in a moment.')
    expect(screen.container.querySelector('[data-testid="kpi-value"]')).toBeNull()
    expect(screen.container.textContent).not.toContain('15,257')

    // Retry while still down stays an error; after recovery the fresh value shows.
    const sent = queries().length
    await screen.getByRole('button', { name: 'Retry' }).click()
    await expect.poll(() => queries().length).toBe(sent + 1)
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The data engine is not responding. Retry in a moment.')
    down = false
    await screen.getByRole('button', { name: 'Retry' }).click()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')

    // A new filter value whose query fails does not keep showing the unfiltered value.
    down = true
    await screen.getByRole('textbox', { name: 'Region contains' }).fill('East')
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The data engine is not responding. Retry in a moment.')
    expect(screen.container.querySelector('[data-testid="kpi-value"]')).toBeNull()
  })
})

// ---- revisions ----------------------------------------------------------------------

describe('QA multiple tabs', { timeout: 40_000 }, () => {
  it('NDV-REVIS-007 / NDV-OPS-005: missed change notifications are caught up from the event cursor', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await expect.poll(() => calls.filter(call => call.url.includes('/events')).length).toBeGreaterThanOrEqual(1)
    // The notification channel drops; two changes are saved elsewhere meanwhile.
    eventsDown = true
    otherUserCommits(() => { definition.title = 'Sales overview by Bea' })
    otherUserCommits(() => { definition.pages[0].widgets[0].layout = { x: 0, y: 2, w: 3, h: 2 } })
    const missed = calls.filter(call => call.url.includes('/events')).length
    await expect.poll(() => calls.filter(call => call.url.includes('/events')).length, { timeout: 10_000 }).toBeGreaterThan(missed)
    await expect.element(screen.getByRole('heading', { name: 'Sales overview' })).toBeVisible()
    // Back online: the next poll reads from where it left off and lands on the latest revision with both changes.
    eventsDown = false
    await expect.element(screen.getByRole('heading', { name: 'Sales overview by Bea' }), { timeout: 10_000 }).toBeVisible()
    await expect.poll(() => geometry(KPI_WIDGET)).toEqual({ x: 0, y: 2, w: 3, h: 2 })
    // Later polls ask from the newest change: nothing is fetched twice or skipped.
    const lastAfter = () => Number(new URL(calls.filter(call => call.url.includes('/events')).slice(-1)[0].url, 'http://test').searchParams.get('after'))
    await expect.poll(lastAfter, { timeout: 10_000 }).toBe(revision)
    expect(operations).toHaveLength(0)
  })

  it('NDV-REVIS-008: a repeated or late event and a lagging head never take the editor back', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await expect.poll(() => calls.filter(call => call.url.includes('/events')).length).toBeGreaterThanOrEqual(1)
    const before = snapshot()
    otherUserCommits(() => { definition.title = 'Sales overview by Bea' })
    // The first head read after the event lags behind it.
    staleHeadOnce = clone(before)
    const reads = () => calls.filter(call => call.url.includes(`/viz/dashboards/${DASHBOARD}?`)).length
    const start = reads()
    await expect.poll(reads, { timeout: 10_000 }).toBeGreaterThan(start)
    await expect.element(screen.getByRole('heading', { name: 'Sales overview' })).toBeVisible()
    // The cursor stayed, so the next poll reads again and gets the real head.
    await expect.element(screen.getByRole('heading', { name: 'Sales overview by Bea' }), { timeout: 10_000 }).toBeVisible()
    // A repeated event and a late page with an older cursor change nothing.
    eventsOnce = { events: [
      { event_id: crypto.randomUUID(), operation_id: events[events.length - 1].operation_id, sequence: revision, event_kind: 'edit', created_at: '2026-11-16T00:00:00Z' },
      { event_id: crypto.randomUUID(), operation_id: crypto.randomUUID(), sequence: revision - 2, event_kind: 'edit', created_at: '2026-11-16T00:00:00Z' },
    ], through: revision - 2, next_sequence: revision - 2 }
    staleHeadOnce = clone(before)
    await expect.poll(() => eventsOnce, { timeout: 10_000 }).toBeNull()
    await wait(600)
    await expect.element(screen.getByRole('heading', { name: 'Sales overview by Bea' })).toBeVisible()
    // Later polls ask from the newest sequence, never the late page's.
    const startCalls = calls.length
    await expect.poll(() => calls.slice(startCalls).some(call => call.url.includes('/events')), { timeout: 10_000 }).toBe(true)
    const next = calls.slice(startCalls).find(call => call.url.includes('/events'))!
    expect(Number(new URL(next.url, 'http://test').searchParams.get('after'))).toBe(revision)
    expect(operations).toHaveLength(0)
  })

  it('NDV-REVIS-010: two editor tabs of one person converge without a stale local override', async () => {
    // Each tab has its own screen; here both fit on one so both charts draw.
    await page.viewport(1280, 1600)
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
    const placements = (operations[1].changes as { placements: { element_id: string }[] }[])[0].placements
    expect(placements.map(placement => placement.element_id)).toEqual([KPI_WIDGET])
    expect(definition.pages[0].widgets.map(widget => widget.widget_id)).toEqual([KPI_WIDGET])
    await B.getByRole('button', { name: 'Done' }).click()

    // A then converges on B's revision too.
    await expect.poll(() => geometry(KPI_WIDGET, a), { timeout: 10_000 }).toEqual({ x: 1, y: 0, w: 3, h: 2 })
  })

  it('NDV-REVIS-010: a tab that edits before seeing the other tab’s change is refused, reloads and does not resubmit', async () => {
    await page.viewport(1280, 1600)
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
  it('NDV-GRID-019: a full page of 150 items draws only what is near the screen, queries at most 3 at a time, and accepts no 151st', async () => {
    const widgets: WidgetJson[] = []
    for (let index = 1; index <= 150; index += 1) {
      const id = `cccccccc-0000-4000-8000-${String(index).padStart(12, '0')}`
      charts[id] = kpiChart(id, `Chart ${index}`, `units_${index}`)
      widgets.push(chartWidget(`ffffffff-0000-4000-8000-${String(index).padStart(12, '0')}`, id,
        { x: ((index - 1) % 4) * 3, y: Math.floor((index - 1) / 4) * 2, w: 3, h: 2 }))
    }
    setWidgets(widgets)
    // Each answer takes a moment, so overlapping queries would show.
    let open = 0
    let most = 0
    answer = async body => {
      open += 1
      most = Math.max(most, open)
      await wait(40)
      open -= 1
      return defaultAnswer(body)
    }
    await page.viewport(1280, 900)
    const screen = await mount(1200, 720)
    const drawn = () => screen.container.querySelectorAll('[data-testid="kpi-value"]').length
    const waiting = () => screen.container.querySelectorAll('[data-waiting-chart]').length
    await expect.poll(drawn, { timeout: 15_000 }).toBeGreaterThan(0)
    await wait(800)
    // Every widget is laid out; only the rows near the screen draw and query.
    expect(screen.container.querySelectorAll('.nova-dataviz-canvas > [data-element-id]')).toHaveLength(150)
    const first = drawn()
    expect(first).toBeLessThan(60)
    expect(drawn() + waiting()).toBe(150)
    expect(queries()).toHaveLength(first)
    expect(most).toBeLessThanOrEqual(3)

    // Scrolling to the end draws the last widgets; each chart is still asked once.
    const scroller = screen.container.querySelector<HTMLElement>('[data-canvas-scroll]')!
    for (let step = 0; step < 60 && waiting(); step += 1) {
      scroller.scrollTop += 600
      await wait(150)
    }
    await expect.poll(drawn, { timeout: 30_000 }).toBe(150)
    await expect.poll(() => queries().length, { timeout: 15_000 }).toBe(150)
    expect(new Set(queries().map(call => measureOf(call.body))).size).toBe(150)
    expect(most).toBeLessThanOrEqual(3)
    expect(document.querySelectorAll('[data-testid="kpi-value"]')).toHaveLength(150)

    // The page is full: nothing more can be added to it.
    await screen.getByRole('button', { name: 'Edit' }).click()
    const toolbar = screen.getByRole('toolbar', { name: 'Add to dashboard' })
    for (const name of ['Chart', 'KPI', 'Table', 'Text']) await expect.element(toolbar.getByRole('button', { name, exact: true })).toBeDisabled()
    await expect.element(toolbar.getByText('This page has 150 items, the most a page can hold. Add a page for more.')).toBeVisible()
    expect(operations).toHaveLength(0)
  })

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
    // The whole 2,400px page is on screen, so every widget of the page draws.
    await page.viewport(1280, 2500)
    const screen = await mount(1200, 2400)
    const settled = async (sheet: PageJson) => {
      const kpis = sheet.widgets.filter(widget => Number(widget.widget_id.slice(-12)) % 5 !== 0).length
      const bars = sheet.widgets.length - kpis
      await expect.poll(() => screen.container.querySelectorAll('[data-testid="kpi-value"]').length, { timeout: 15_000 }).toBe(kpis)
      await expect.poll(() => [...screen.container.querySelectorAll<HTMLElement>('[role="img"]')]
        .filter(node => getInstanceByDom(node)).length, { timeout: 15_000 }).toBe(bars)
      const items = [...screen.container.querySelectorAll<HTMLElement>('.nova-dataviz-canvas > [data-element-id]')]
      expect(items).toHaveLength(sheet.widgets.length)
      // Every chart on screen belongs to this page; no other page's chart is drawn.
      const ids = new Set(sheet.widgets.map(widget => widget.widget_id))
      expect(items.every(node => ids.has(node.dataset.elementId!))).toBe(true)
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


// ---- The assistant's pointer --------------------------------------------------------------------

const neva = (detail: NativeDashboardActivity) => window.dispatchEvent(new CustomEvent(NATIVE_DASHBOARD_ACTIVITY, { detail }))
const propose = (widget_ids: string[], extra: Partial<NativeDashboardActivity> = {}) =>
  neva({ kind: 'proposed', tool_call_id: 'call-1', dashboard_id: DASHBOARD, action: 'change', widget_ids, page_id: PAGE, state: 'saving', ...extra } as NativeDashboardActivity)
const pointer = () => document.querySelector<HTMLElement>('[data-neva-pointer]')
const nevaStatus = (screen: Awaited<ReturnType<typeof mount>>) => screen.getByTestId('neva-status').element().textContent
const motion = (value: '' | 'reduce') => (cdp() as unknown as { send(method: string, params?: object): Promise<unknown> })
  .send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value }] })

describe('QA assistant pointer', { timeout: 30_000 }, () => {
  afterEach(async () => { await motion('') })

  it('NDV-AGENT-013: the pointer sits on the widget the change touches and says where it is', async () => {
    setWidgets([
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 0, y: 0, w: 3, h: 2 }),
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 3, y: 2, w: 6, h: 4 }),
    ])
    const screen = await mount()
    await expect.element(screen.getByText('Revenue by region').first()).toBeVisible()
    // A change on another dashboard is not shown here.
    propose([BAR_WIDGET], { dashboard_id: 'dddddddd-0000-4000-8000-0000000000ff' })
    await wait(200)
    expect(pointer()).toBeNull()
    propose([BAR_WIDGET])
    await expect.poll(() => pointer()?.dataset.arrived).toBe('true')
    await wait(800)
    // The pointer sits on the target's top edge, inside its width, and the
    // target is outlined; no other widget is.
    const target = item(BAR_WIDGET).getBoundingClientRect()
    const tip = pointer()!.getBoundingClientRect()
    expect(tip.left).toBeGreaterThanOrEqual(target.left)
    expect(tip.right).toBeLessThanOrEqual(target.right)
    expect(tip.top + tip.height / 2).toBeGreaterThanOrEqual(target.top)
    expect(tip.top + tip.height / 2).toBeLessThan(target.top + 24)
    expect(item(BAR_WIDGET).dataset.nevaTarget).toBe('saving')
    expect(item(KPI_WIDGET).dataset.nevaTarget).toBeUndefined()
    // The assistant acts without asking: no approval wait, it is saving at once.
    expect(pointer()!.textContent).toBe('Assistant · Saving…')
    expect(pointer()!.getAttribute('aria-hidden')).toBe('true')
    expect(nevaStatus(screen)).toBe('The assistant is saving: change “Revenue by region”.')
    expect(screen.container.textContent).not.toContain('approval')
    // Saved: the label follows, and the pointer leaves after the save.
    neva({ kind: 'status', tool_call_id: 'call-1', state: 'saved' })
    await expect.poll(() => pointer()?.textContent).toBe('Assistant · Saved')
    expect(nevaStatus(screen)).toBe('The assistant saved its change: change “Revenue by region”.')
    await expect.poll(() => pointer(), { timeout: 4000 }).toBeNull()
    // A new chart has no widget yet: the pointer goes below what is there.
    propose([], { tool_call_id: 'call-2', action: 'add' } as Partial<NativeDashboardActivity>)
    await expect.poll(() => pointer()?.dataset.arrived).toBe('true')
    await wait(800)
    expect(pointer()!.getBoundingClientRect().top).toBeGreaterThan(item(BAR_WIDGET).getBoundingClientRect().bottom - 16)
    expect(nevaStatus(screen)).toBe('The assistant is saving: add a chart.')
  })

  it('NDV-AGENT-014: a save that fails shows Not saved, never Saved, and changes nothing', async () => {
    const screen = await mount()
    await expect.element(screen.getByText('Units sold')).toBeVisible()
    const before = calls.length
    propose([KPI_WIDGET])
    await expect.poll(() => pointer()?.textContent).toBe('Assistant · Saving…')
    neva({ kind: 'status', tool_call_id: 'call-1', state: 'failed' })
    await expect.poll(() => pointer()?.dataset.nevaPointer).toBe('failed')
    expect(pointer()!.textContent).toBe('Assistant · Not saved')
    expect(item(KPI_WIDGET).dataset.nevaTarget).toBe('failed')
    expect(nevaStatus(screen)).toBe('The assistant could not save its change (change “Units sold”). Nothing was changed.')
    // A late "saved" for the same call cannot turn a failure into a success.
    neva({ kind: 'status', tool_call_id: 'call-1', state: 'saved' })
    await wait(200)
    expect(pointer()!.textContent).toBe('Assistant · Not saved')
    expect(screen.container.textContent).not.toContain('Assistant · Saved')
    // Nothing was written by the page for the assistant's attempt.
    expect(calls.slice(before).filter(call => call.method !== 'GET' && !call.url.endsWith('/query'))).toEqual([])
    // A stopped change says so too.
    propose([KPI_WIDGET], { tool_call_id: 'call-2' })
    neva({ kind: 'status', tool_call_id: 'call-2', state: 'stopped' })
    await expect.poll(() => pointer()?.textContent).toBe('Assistant · Stopped')
    expect(nevaStatus(screen)).toBe("The assistant's change was not made (change “Units sold”). Nothing was changed.")
  })

  it('NDV-AGENT-016 / NDV-A11Y-003: with reduced motion the pointer appears in place and its state is still announced', async () => {
    await motion('reduce')
    const screen = await mount()
    await expect.element(screen.getByText('Units sold')).toBeVisible()
    propose([KPI_WIDGET])
    // On the first frame it is already at the widget: no path from the chat side.
    await expect.poll(() => pointer()).not.toBeNull()
    expect(pointer()!.dataset.arrived).toBe('true')
    const style = getComputedStyle(pointer()!)
    expect(style.transitionDuration.split(',').every(value => parseFloat(value) === 0)).toBe(true)
    expect(pointer()!.style.opacity).toBe('')
    const target = item(KPI_WIDGET).getBoundingClientRect()
    expect(pointer()!.getBoundingClientRect().right).toBeLessThanOrEqual(target.right)
    expect(nevaStatus(screen)).toContain('The assistant is saving: change “Units sold”')
    expect(screen.getByTestId('neva-status').element().getAttribute('role')).toBe('status')
    // Without the preference the same pointer glides in from the side.
    await motion('')
    propose([KPI_WIDGET], { tool_call_id: 'call-2' })
    await expect.poll(() => pointer()?.dataset.arrived).toBe('true')
    expect(parseFloat(getComputedStyle(pointer()!).transitionDuration)).toBeGreaterThan(0)
  })

  it('NDV-AGENT-017: typing next to the widget the assistant points at keeps focus and every keystroke', async () => {
    setWidgets([
      chartWidget(KPI_WIDGET, KPI_CHART, { x: 0, y: 0, w: 3, h: 2 }),
      chartWidget(BAR_WIDGET, BAR_CHART, { x: 3, y: 0, w: 6, h: 4 }),
    ])
    const screen = await mount()
    await expect.element(screen.getByText('Revenue by region').first()).toBeVisible()
    await userEvent.dblClick(screen.getByRole('heading', { name: 'Sales overview' }))
    const input = screen.getByRole('textbox', { name: 'Dashboard title' })
    await expect.element(input).toHaveFocus()
    const field = input.element() as HTMLInputElement
    field.select()
    await userEvent.keyboard('Quarterly')
    propose([BAR_WIDGET])
    neva({ kind: 'status', tool_call_id: 'call-1', state: 'saving' })
    await userEvent.keyboard(' sales')
    await expect.poll(() => pointer()?.textContent).toBe('Assistant · Saving…')
    propose([KPI_WIDGET], { tool_call_id: 'call-2' })
    await userEvent.keyboard(' review')
    expect(document.activeElement).toBe(field)
    expect(field.value).toBe('Quarterly sales review')
    // The pointer can never take focus or a click.
    expect(pointer()!.querySelector('[tabindex], button, a, input')).toBeNull()
    expect(getComputedStyle(pointer()!).pointerEvents).toBe('none')
    // The widgets under it stay selectable with a click.
    await userEvent.keyboard('{Escape}')
  })
})


describe('QA Neva layout plan', { timeout: 40_000 }, () => {
  it('NDV-PERF-008: a twenty-step layout revision from Neva lands in place without a frame stall', async () => {
    const ids = Array.from({ length: 20 }, (_, index) => `ffffffff-0000-4000-8000-1000000000${String(index).padStart(2, '0')}`)
    // Four charts among sixteen notes, stacked one per row.
    setWidgets(ids.map((id, index) => index % 5 === 0
      ? chartWidget(id, index === 0 ? KPI_CHART : index === 5 ? REVENUE_CHART : BAR_CHART, { x: 0, y: index * 3, w: 6, h: 3 })
      : textWidget(id, `Note ${index}`, { x: 0, y: index * 3, w: 6, h: 3 })))
    await page.viewport(1280, 1400)
    const screen = await mount(1200, 1300)
    await expect.element(screen.getByText('Note 19')).toBeInTheDocument()
    await wait(500)
    const target = ids.map((id, index) => ({ id, layout: { x: (index % 2) * 6, y: Math.floor(index / 2) * 3, w: 6, h: 3 } }))
    // Neva's one batch: every widget moves in a single committed revision.
    otherUserCommits(() => commit([{ op: 'layout.apply', page_id: PAGE, placements: target.map(item => ({ widget_id: item.id, layout: item.layout })) }]))
    const long: number[] = []
    const observer = new PerformanceObserver(list => { for (const entry of list.getEntries()) long.push(entry.duration) })
    observer.observe({ type: 'longtask', buffered: false })
    const started = performance.now()
    window.dispatchEvent(new CustomEvent(NATIVE_DASHBOARD_CHANGED, { detail: { dashboard_id: DASHBOARD, revision } }))
    await expect.poll(() => target.every(({ id, layout }) => {
      const cell = geometry(id)
      return cell.x === layout.x && cell.y === layout.y && cell.w === layout.w && cell.h === layout.h
    }), { timeout: 10_000 }).toBe(true)
    const applied = performance.now() - started
    await wait(600)
    observer.disconnect()
    // Every widget is where the plan put it, drawn in place, after one reload.
    for (const { id, layout } of target) {
      expect(geometry(id)).toEqual(layout)
    }
    const reads = calls.filter(call => call.method === 'GET' && call.url.includes(`/viz/dashboards/${DASHBOARD}`) && !call.url.includes('/events'))
    expect(reads.length).toBeGreaterThan(0)
    // No main-thread task long enough to freeze the page (a stall is ~200 ms or more).
    console.info(`PERF-008 apply ${Math.round(applied)} ms, long tasks ${JSON.stringify(long.map(Math.round))}`)
    expect(Math.max(0, ...long)).toBeLessThan(200)
    expect(applied).toBeLessThan(3000)
  })
})
