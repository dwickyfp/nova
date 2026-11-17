import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/auth-store'
import { DashboardEditor } from './editor'
import { NATIVE_DASHBOARD_CHANGED } from './events'
import '@/styles/index.css'

const DASHBOARD = 'dddddddd-0000-4000-8000-000000000001'
const DATASET = 'aaaaaaaa-0000-4000-8000-000000000001'
const CHART = 'cccccccc-0000-4000-8000-000000000001'
const PAGE = 'eeeeeeee-0000-4000-8000-000000000001'
const KPI_WIDGET = 'ffffffff-0000-4000-8000-000000000001'
const TEXT_WIDGET = 'ffffffff-0000-4000-8000-000000000002'
const fetchMock = vi.fn()
let permissionList: string[] = []
const exports: Record<string, unknown>[] = []
const clients: QueryClient[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const kpi = {
  schema_version: 2, chart_id: CHART, title: 'Units sold', dataset_id: DATASET, dataset_revision: 1, visual: 'kpi',
  encoding: { kind: 'single_measure', measure: { field_id: 'units', aggregation: 'sum', format: { kind: 'number', decimals: 0, locale: 'en-US' }, axis: 'left', series: 'bar' } },
}
let revision = 4
let definition: Record<string, unknown>
const operations: Record<string, unknown>[] = []

function dashboard() {
  return { resource_id: DASHBOARD, revision, metadata_revision: revision, grants_revision: 1, publication_id: null, definition }
}

async function route(url: string, options: RequestInit = {}) {
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/permissions`)) return json(permissionList)
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/exports`)) {
    exports.push(JSON.parse(String(options.body)))
    return permissionList.includes('export') ? json({ allowed: true }) : json({ detail: 'Not allowed' }, 403)
  }
  if (url.includes(`/viz/dashboards/${DASHBOARD}/events`)) return json({ events: [], through: 0, next_sequence: 0 })
  if (url.includes(`/viz/dashboards/${DASHBOARD}/datasets`)) return json({ dashboard_id: DASHBOARD, revision, datasets: [{ dataset_id: DATASET, revision: 1, title: 'Orders' }] })
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/operations`)) {
    const body = JSON.parse(String(options.body))
    operations.push(body)
    const headers = options.headers as Record<string, string>
    const base = revision
    revision += 1
    return json({ operation_id: headers['Idempotency-Key'], resource_id: DASHBOARD, base_revision: base, new_revision: revision, event_sequence: revision, committed_at: '2026-11-16T00:00:00Z' })
  }
  if (url.includes(`/viz/dashboards/${DASHBOARD}?`)) return json(dashboard())
  if (url.includes(`/viz/charts/${CHART}?`)) return json({ resource_id: CHART, revision: 1, metadata_revision: 1, grants_revision: 1, publication_id: null, definition: kpi })
  if (url.endsWith(`/viz/datasets/${DATASET}/query`)) {
    return json({ dataset_id: DATASET, revision: 1, columns: [{ key: 'm0', name: 'units', logical_type: 'decimal', role: 'measure', field_id: 'units', aggregation: 'sum' }],
      rows: [[{ type: 'decimal', value: '15257' }]], truncated: false })
  }
  throw new Error(`Unexpected request ${url}`)
}

async function mount(width = 1200) {
  const client = new QueryClient()
  clients.push(client)
  return render(<QueryClientProvider client={client}><TooltipProvider>
    <div data-testid='frame' style={{ height: 720, width, display: 'flex' }}>
      <DashboardEditor dashboardId={DASHBOARD} workspaceId='personal' onBack={() => undefined} />
    </div>
  </TooltipProvider></QueryClientProvider>)
}

beforeEach(() => {
  permissionList = ['view', 'interact', 'comment', 'edit', 'publish', 'manage']
  exports.length = 0
  revision = 4
  operations.length = 0
  definition = {
    schema_version: 2, dashboard_id: DASHBOARD, title: 'Sales overview', description: '', columns: 12,
    datasets: [{ dataset_id: DATASET, revision: 1 }], filters: [], actions: [],
    pages: [{ page_id: PAGE, title: 'Overview', widgets: [
      { kind: 'chart', widget_id: KPI_WIDGET, title: '', locked: false, layout: { x: 0, y: 0, w: 3, h: 2 }, chart_ref: { chart_id: CHART, revision: 1 } },
      { kind: 'text', widget_id: TEXT_WIDGET, title: '', locked: false, layout: { x: 3, y: 0, w: 9, h: 2 }, text: '<b>Plain</b> notes', style: 'body' },
    ] }],
  }
  useAuthStore.getState().auth.setAccessToken('editor-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  fetchMock.mockReset().mockImplementation(route)
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
  await page.viewport(1280, 720)
})

describe('native dashboard editor', () => {
  it('renders governed widget values and text without interpreting markup', async () => {
    const screen = await mount()
    await expect.element(screen.getByRole('heading', { name: 'Sales overview' })).toBeVisible()
    await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('15,257')
    await expect.element(screen.getByText('<b>Plain</b> notes')).toBeVisible()
    expect(document.querySelector('.grid-stack b')).toBeNull()
    const query = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/query'))!
    expect(JSON.parse(query[1].body)).toMatchObject({ revision: 1, draft: false, measures: [{ field_id: 'units', aggregation: 'sum' }] })
  })

  it('commits a removal with the read revision and undoes it as a new revision', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await expect.element(screen.getByRole('toolbar', { name: 'Add to dashboard' })).toBeVisible()
    await screen.getByRole('button', { name: 'Widget actions' }).click()
    await page.getByRole('menuitem', { name: 'Remove' }).click()
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ expected_revision: 4, changes: [{ op: 'widget.remove', page_id: PAGE, widget_id: TEXT_WIDGET }] })
    await expect.element(screen.getByText('<b>Plain</b> notes')).not.toBeInTheDocument()
    await screen.getByRole('button', { name: 'Undo' }).click()
    await expect.poll(() => operations.length).toBe(2)
    expect(operations[1]).toMatchObject({ expected_revision: 5, changes: [{ op: 'widget.add', widget: { widget_id: TEXT_WIDGET } }] })
    await expect.element(screen.getByText('<b>Plain</b> notes')).toBeVisible()
    await expect.element(screen.getByRole('button', { name: 'Redo' })).toBeEnabled()
  })

  it('moves and resizes the selected widget from the keyboard, never into another widget', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await screen.getByText('<b>Plain</b> notes').click()
    await userEvent.keyboard('{Alt>}{ArrowDown}{/Alt}')
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ changes: [{ op: 'layout.apply', page_id: PAGE, placements: expect.arrayContaining([
      { widget_id: TEXT_WIDGET, layout: { x: 3, y: 1, w: 9, h: 2 } },
      { widget_id: KPI_WIDGET, layout: { x: 0, y: 0, w: 3, h: 2 } },
    ]) }] })
    await expect.element(screen.getByText('Moved: column 4, row 2, 9 columns wide, 2 rows tall')).toBeInTheDocument()
    // Widening the KPI would overlap the text widget beside it: nothing is sent.
    await screen.getByTestId('kpi-value').click()
    await userEvent.keyboard('{Alt>}{Shift>}{ArrowRight}{/Shift}{/Alt}')
    await expect.element(screen.getByText('Another widget is in the way')).toBeInTheDocument()
    expect(operations).toHaveLength(1)
  })

  it('shows the published defaults read-only to someone who may view but not interact', async () => {
    permissionList = ['view']
    definition.filters = [{
      filter_id: 'f0000000-0000-4000-8000-000000000001', title: 'Region', kind: 'multi_value',
      field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'region' }, scope: { kind: 'global' },
      mappings: [], defaults: [{ type: 'string', value: 'Jakarta' }], allow_all: true, include_null: false,
      searchable: true, depends_on: [],
    }]
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await expect.element(screen.getByLabelText('Region filter, set by the dashboard')).toHaveTextContent('Jakarta')
    await expect.element(screen.getByText('Your access shows the dashboard as published')).toBeVisible()
    await expect.element(screen.getByRole('button', { name: 'Region filter' })).not.toBeInTheDocument()
    await expect.element(screen.getByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    // The chart runs with the dashboard's default, which a view-only person cannot change.
    await expect.poll(() => fetchMock.mock.calls.some(([url, init]) =>
      String(url).endsWith('/query') && String(init?.body).includes('Jakarta'))).toBe(true)
  })

  it('shows a six-column tablet view scaled from the saved layout and never saves it', async () => {
    const screen = await mount(760)
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    const grid = () => (document.querySelector('.grid-stack') as unknown as { gridstack: { getColumn: () => number } }).gridstack
    const width = (id: string) => document.querySelector(`[gs-id="${id}"]`)?.getAttribute('gs-w')
    await expect.poll(() => grid().getColumn()).toBe(6)
    expect(Number(width(TEXT_WIDGET))).toBeLessThanOrEqual(6)
    // Back at desktop width the saved 12-column layout returns unchanged.
    ;(screen.getByTestId('frame').element() as HTMLElement).style.width = '1200px'
    await expect.poll(() => grid().getColumn()).toBe(12)
    await expect.poll(() => [width(KPI_WIDGET), width(TEXT_WIDGET)]).toEqual(['3', '9'])
    expect(operations).toHaveLength(0)
  })

  it('keeps a saved gap: a widget saved lower down reloads exactly where it was saved', async () => {
    const page0 = (definition.pages as { widgets: { widget_id: string; layout: Record<string, number> }[] }[])[0]
    page0.widgets[1].layout = { x: 3, y: 3, w: 9, h: 2 }
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    const item = () => document.querySelector(`[gs-id="${TEXT_WIDGET}"]`)
    await expect.poll(() => [item()?.getAttribute('gs-x'), item()?.getAttribute('gs-y')]).toEqual(['3', '3'])
    expect(operations).toHaveLength(0)
  })

  it('returns focus to the button that opened the filters dialog', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    const opener = screen.getByRole('button', { name: 'Filters', exact: true })
    await opener.click()
    await expect.element(page.getByRole('dialog', { name: 'Dashboard filters' })).toBeVisible()
    await userEvent.keyboard('{Escape}')
    await expect.element(page.getByRole('dialog', { name: 'Dashboard filters' })).not.toBeInTheDocument()
    await expect.element(opener).toHaveFocus()
  })

  it('reaches an existing widget with Tab and moves it with Alt and an arrow', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    const frame = document.querySelector<HTMLElement>(`[gs-id="${TEXT_WIDGET}"] .grid-stack-item-content`)!
    expect(frame.tabIndex).toBe(0)
    frame.focus()
    await userEvent.keyboard('{Alt>}{ArrowDown}{/Alt}')
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ changes: [{ op: 'layout.apply', placements: expect.arrayContaining([
      { widget_id: TEXT_WIDGET, layout: { x: 3, y: 1, w: 9, h: 2 } }]) }] })
  })

  it('shows every grid cell while editing, lined up with the widgets, and none in view', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    expect(document.querySelector('[data-testid="canvas-cells"]')).toBeNull()
    await screen.getByRole('button', { name: 'Edit' }).click()
    const cells = () => [...document.querySelectorAll<HTMLElement>('[data-testid="canvas-cells"] > span')]
    await expect.poll(() => cells().length).toBeGreaterThan(0)
    expect(cells().length % 12).toBe(0)
    for (const item of document.querySelectorAll<HTMLElement>('.nova-dataviz-canvas > .grid-stack-item')) {
      const [x, y, w, h] = ['gs-x', 'gs-y', 'gs-w', 'gs-h'].map(name => Number(item.getAttribute(name)))
      // Room is kept below the lowest widget to drag or resize into.
      expect(cells().length / 12).toBeGreaterThanOrEqual(y + h + 3)
      const content = item.querySelector('.grid-stack-item-content')!.getBoundingClientRect()
      const first = cells()[y * 12 + x].getBoundingClientRect()
      const last = cells()[(y + h - 1) * 12 + x + w - 1].getBoundingClientRect()
      expect([content.left - first.left, content.top - first.top, content.right - last.right, content.bottom - last.bottom]
        .map(value => Math.abs(value) < 1)).toEqual([true, true, true, true])
    }
    expect(operations).toHaveLength(0)
  })

  it('runs every chart again when asked to refresh the data', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    const queries = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/query')).length
    const before = queries()
    await screen.getByRole('button', { name: 'Refresh data' }).click()
    await expect.poll(queries).toBe(before + 1)
    expect(operations).toHaveLength(0)
  })

  it('reloads when Neva reports a committed change to this dashboard', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    revision = 6
    definition = { ...definition, title: 'Sales overview by Neva' }
    window.dispatchEvent(new CustomEvent(NATIVE_DASHBOARD_CHANGED, { detail: { dashboard_id: DASHBOARD, revision: 6 } }))
    await expect.element(screen.getByRole('heading', { name: 'Sales overview by Neva' })).toBeVisible()
    // A widget Neva changed is pointed out; unchanged widgets are not.
    definition = { ...definition, pages: [{ ...(definition.pages as { widgets: Record<string, unknown>[] }[])[0],
      widgets: [(definition.pages as { widgets: Record<string, unknown>[] }[])[0].widgets[0],
        { ...(definition.pages as { widgets: Record<string, unknown>[] }[])[0].widgets[1], text: 'Neva wrote this' }] }] } as Record<string, unknown>
    revision = 7
    window.dispatchEvent(new CustomEvent(NATIVE_DASHBOARD_CHANGED, { detail: { dashboard_id: DASHBOARD, revision: 7 } }))
    await expect.element(screen.getByText('Neva wrote this')).toBeVisible()
    await expect.element(screen.getByText('Changed by Neva')).toBeVisible()
    expect(screen.container.querySelectorAll('[role="status"]').length).toBeGreaterThan(0)
  })

  it('offers downloads only with the Export permission', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    expect(screen.container.querySelector('[aria-label="Download"]')).toBeNull()
    expect(screen.container.querySelector('[aria-label^="Download "]')).toBeNull()
  })

  it('checks Export, then saves the chart data and the page workbook from governed results', async () => {
    permissionList = [...permissionList, 'export']
    const saved: { name: string; blob: Blob }[] = []
    const blobs: Blob[] = []
    vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { blobs.push(blob as Blob); return 'blob:test' })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push({ name: this.download, blob: blobs[blobs.length - 1] })
    })
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Download Units sold' }).click()
    // A KPI is text, so it has no image to download.
    await expect.element(page.getByRole('menuitem', { name: 'Data (CSV)' })).toBeVisible()
    expect(document.querySelector('[role="menuitem"][aria-label="Image (PNG)"]')).toBeNull()
    await page.getByRole('menuitem', { name: 'Data (CSV)' }).click()
    await expect.poll(() => saved.length).toBe(1)
    expect(saved[0].name).toBe('Units sold.csv')
    // A byte-order mark first, so spreadsheet apps read the file as UTF-8.
    expect([...new Uint8Array(await saved[0].blob.arrayBuffer()).slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf])
    expect(await saved[0].blob.text()).toBe('Sum of units\r\n15257\r\n')
    expect(exports[0]).toEqual({ format: 'csv', chart_id: CHART })

    await screen.getByRole('button', { name: 'Download', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Data of this page (Excel)' }).click()
    await expect.poll(() => saved.length).toBe(2)
    expect(saved[1].name).toBe('Sales overview - Overview.xlsx')
    expect(exports[1]).toEqual({ format: 'xlsx' })
    const XLSX = await import('xlsx')
    const book = XLSX.read(await saved[1].blob.arrayBuffer())
    expect(book.SheetNames).toEqual(['Units sold'])
    expect(XLSX.utils.sheet_to_json(book.Sheets['Units sold'], { header: 1 })).toEqual([['Sum of units'], [15257]])
  })

  it('saves nothing when the server refuses the download', async () => {
    permissionList = [...permissionList, 'export']
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    // Access changed after the page loaded.
    permissionList = permissionList.filter(item => item !== 'export')
    const downloads: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      if (this.download) downloads.push(this.download)
    })
    await screen.getByRole('button', { name: 'Download Units sold' }).click()
    await page.getByRole('menuitem', { name: 'Data (CSV)' }).click()
    await expect.poll(() => exports.length).toBe(1)
    await new Promise(resolve => setTimeout(resolve, 100))
    expect(downloads).toEqual([])
  })
})
