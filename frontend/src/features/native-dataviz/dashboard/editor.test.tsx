import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/auth-store'
import type { DashboardDefinitionV2 } from '../contracts'
import { DashboardEditor } from './editor'
import { NATIVE_DASHBOARD_CHANGED } from './events'
import { upcastDashboard } from './layout'
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
const ASSET = '99999999-0000-4000-8000-000000000001'
const uploads: { body: unknown; type: string }[] = []
let picture: Blob = new Blob()

function dashboard() {
  return { resource_id: DASHBOARD, revision, metadata_revision: revision, grants_revision: 1, publication_id: null, definition: upcastDashboard(definition as unknown as DashboardDefinitionV2) }
}

async function route(url: string, options: RequestInit = {}) {
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/permissions`)) return json(permissionList)
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/exports`)) {
    exports.push(JSON.parse(String(options.body)))
    return permissionList.includes('export') ? json({ allowed: true }) : json({ detail: 'Not allowed' }, 403)
  }
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/images`)) {
    uploads.push({ body: options.body, type: (options.headers as Record<string, string>)['Content-Type'] })
    return json({ asset_id: ASSET, media_type: 'image/png', size_bytes: 8, width: 800, height: 400, created_at: '2026-11-24T00:00:00Z' }, 201)
  }
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/images/${ASSET}`)) return new Response(picture, { headers: { 'Content-Type': 'image/png' } })
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
  uploads.length = 0
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
    expect(document.querySelector('.nova-dataviz-canvas b')).toBeNull()
    const query = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/query'))!
    expect(JSON.parse(query[1].body)).toMatchObject({ revision: 1, draft: false, measures: [{ field_id: 'units', aggregation: 'sum' }] })
  })

  it('commits a removal with the read revision and undoes it as a new revision', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await expect.element(screen.getByRole('toolbar', { name: 'Add to dashboard' })).toBeVisible()
    // An untitled item keeps its actions in a corner, not a header row, so its words start at the top.
    const words = screen.getByText('<b>Plain</b> notes').element()
    const top = words.getBoundingClientRect().top - words.closest('[data-element-body]')!.getBoundingClientRect().top
    expect(top).toBeLessThan(20)
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

  it('moves and resizes the selected item from the keyboard in page pixels', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await screen.getByText('<b>Plain</b> notes').click()
    await userEvent.keyboard('{Alt>}{ArrowDown}{/Alt}')
    await expect.poll(() => operations.length).toBe(1)
    // One baseline row down; only the moved item is sent.
    expect(operations[0]).toMatchObject({ changes: [{ op: 'elements.arrange', page_id: PAGE, placements: [
      { element_id: TEXT_WIDGET, geometry: { x: 306, y: 14, w: 888, h: 132, rotation: 0 } },
    ] }] })
    await expect.element(screen.getByText('Moved to x 306, y 14, 888 by 132 pixels')).toBeInTheDocument()
    // A free canvas lets items overlap: widening the KPI to the next column end is sent as asked.
    await screen.getByTestId('kpi-value').click()
    await userEvent.keyboard('{Alt>}{Shift>}{ArrowRight}{/Shift}{/Alt}')
    await expect.poll(() => operations.length).toBe(2)
    expect(operations[1]).toMatchObject({ changes: [{ op: 'elements.arrange', placements: [
      { element_id: KPI_WIDGET, geometry: { x: 6, y: 6, w: 388, h: 132, rotation: 0 } },
    ] }] })
    await expect.element(screen.getByText('Resized to x 6, y 6, 388 by 132 pixels')).toBeInTheDocument()
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

  it('scales the saved page to a narrower window and never saves it', async () => {
    const screen = await mount(760)
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    const width = (id: string) => document.querySelector(`[data-element-id="${id}"]`)!.getBoundingClientRect().width
    const scale = () => document.querySelector('[data-canvas-frame]')!.getBoundingClientRect().width / 1200
    await expect.poll(scale).toBeLessThan(0.7)
    expect(width(TEXT_WIDGET) / scale()).toBeCloseTo(888, 0)
    expect(width(KPI_WIDGET) / scale()).toBeCloseTo(288, 0)
    // At a wider window the same saved pixels are drawn larger, still in proportion.
    ;(screen.getByTestId('frame').element() as HTMLElement).style.width = '1280px'
    await expect.poll(scale).toBeGreaterThan(0.85)
    expect(width(TEXT_WIDGET) / width(KPI_WIDGET)).toBeCloseTo(888 / 288, 2)
    expect(operations).toHaveLength(0)
  })

  it('keeps a saved gap: a widget saved lower down reloads exactly where it was saved', async () => {
    const page0 = (definition.pages as { widgets: { widget_id: string; layout: Record<string, number> }[] }[])[0]
    page0.widgets[1].layout = { x: 3, y: 3, w: 9, h: 2 }
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    const frame = () => document.querySelector('[data-canvas-frame]')!.getBoundingClientRect()
    const item = () => document.querySelector(`[data-element-id="${TEXT_WIDGET}"]`)!.getBoundingClientRect()
    const scale = () => frame().width / 1200
    await expect.poll(() => Math.round((item().left - frame().left) / scale())).toBe(306)
    expect(Math.round((item().top - frame().top) / scale())).toBe(222)
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
    const item = document.querySelector<HTMLElement>(`[data-element-id="${TEXT_WIDGET}"]`)!
    expect(item.tabIndex).toBe(0)
    expect(item.getAttribute('aria-label')).toBe('<b>Plain</b> notes, press Alt and an arrow key to move it')
    item.focus()
    await userEvent.keyboard('{Alt>}{ArrowDown}{/Alt}')
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ changes: [{ op: 'elements.arrange', placements: [
      { element_id: TEXT_WIDGET, geometry: { x: 306, y: 14, w: 888, h: 132, rotation: 0 } }] }] })
  })

  it('shows the column grid while editing, lined up with the widgets, and none in view', async () => {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    expect(document.querySelector('[data-testid="canvas-grid"]')).toBeNull()
    await screen.getByRole('button', { name: 'Edit' }).click()
    const columns = () => [...document.querySelectorAll<HTMLElement>('[data-testid="canvas-grid"] > span')].map(item => item.getBoundingClientRect())
    await expect.poll(() => columns().length).toBe(12)
    for (const id of [KPI_WIDGET, TEXT_WIDGET]) {
      const box = document.querySelector(`[data-element-id="${id}"]`)!.getBoundingClientRect()
      // A widget starts on a column's left edge and ends on a column's right edge.
      expect(columns().some(column => Math.abs(column.left - box.left) < 1)).toBe(true)
      expect(columns().some(column => Math.abs(column.right - box.right) < 1)).toBe(true)
    }
    // Room is kept below the page to drag or resize into.
    const frame = document.querySelector('[data-canvas-frame]')!.getBoundingClientRect()
    expect(frame.height / (frame.width / 1200)).toBeGreaterThanOrEqual(720 + 216 - 1)
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
    await expect.element(screen.getByText('Changed by the assistant')).toBeVisible()
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

  it('adds an image from the toolbar and shows the viewer\'s copy on the page', async () => {
    const canvas = document.createElement('canvas')
    canvas.width = 2
    canvas.height = 1
    picture = await new Promise<Blob>(resolve => canvas.toBlob(blob => resolve(blob!), 'image/png'))
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await screen.getByRole('toolbar', { name: 'Add to dashboard' }).getByRole('button', { name: 'Image' }).click()
    const file = new File([picture], 'warehouse.png', { type: 'image/png' })
    await userEvent.upload(page.elementLocator(document.querySelector<HTMLInputElement>('#image-file')!), file)
    await page.getByLabelText('Description for screen readers').fill('Jakarta warehouse at night')
    await page.getByRole('button', { name: 'Add image' }).click()
    await expect.poll(() => operations.length).toBe(1)
    expect(uploads).toEqual([{ body: file, type: 'image/png' }])
    // The item keeps the picture's proportions (800 × 400) and its description.
    expect(operations[0]).toMatchObject({ changes: [{ op: 'elements.add', page_id: PAGE, elements: [{
      kind: 'image', asset_id: ASSET, alt: 'Jakarta warehouse at night', fit: 'contain', geometry: { w: 320, h: 160 } }] }] })
    await expect.element(page.getByRole('dialog')).not.toBeInTheDocument()
    const shown = screen.getByRole('img', { name: 'Jakarta warehouse at night' })
    await expect.element(screen.getByTestId('dashboard-image')).toBeVisible()
    expect((shown.element() as HTMLImageElement).src).toMatch(/^blob:/)
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

describe('canvas authoring in the editor', () => {
  /**
   * Keyboard only: press Tab (or Shift+Tab) until the focused control's
   * accessible name matches, as someone without a pointer would.
   */
  async function tabTo(name: string | RegExp, { back = false, limit = 120 } = {}) {
    const matches = () => {
      const node = document.activeElement as HTMLElement | null
      if (!node || node === document.body) return false
      const label = (node.getAttribute('aria-label') ?? node.textContent ?? '').trim()
      return typeof name === 'string' ? label === name : name.test(label)
    }
    for (let step = 0; step < limit && !matches(); step++) await userEvent.keyboard(back ? '{Shift>}{Tab}{/Shift}' : '{Tab}')
    expect(matches(), `focus reached ${String(name)}`).toBe(true)
  }
  const lastOp = () => operations[operations.length - 1] as { changes: Record<string, unknown>[] }

  it('builds and arranges with the keyboard alone: add, move, format, connect, reorder, undo', { timeout: 60_000 }, async () => {
    await mount()
    await expect.element(page.getByTestId('kpi-value')).toBeVisible()
    await tabTo('Edit')
    await userEvent.keyboard('{Enter}')
    await expect.element(page.getByRole('toolbar', { name: 'Add to dashboard' })).toBeVisible()

    // Insert a rectangle from the Shape menu: Enter opens it on the first item.
    await tabTo('Shape')
    await userEvent.keyboard('{Enter}')
    await expect.element(page.getByRole('menuitem', { name: 'Rectangle' })).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    await expect.poll(() => operations.length).toBe(1)
    const added = (lastOp().changes[0] as { op: string; elements: { element_id: string; kind: string }[] })
    expect(added).toMatchObject({ op: 'elements.add', elements: [{ kind: 'shape', shape: 'rect', name: 'Rectangle 1' }] })
    const rect = added.elements[0].element_id
    await expect.poll(announced).toMatch(/^Added a rectangle at x \d+, y \d+$/)

    // The new shape is selected: Alt+Right moves it one column.
    await expect.element(page.getByRole('menu')).not.toBeInTheDocument()
    await userEvent.keyboard('{Alt>}{ArrowRight}{/Alt}')
    await expect.poll(() => operations.length).toBe(2)
    expect(lastOp().changes[0]).toMatchObject({ op: 'elements.arrange', placements: [{ element_id: rect }] })

    // Format it: open the panel, then the Fill list, and arrow down to Brand.
    // (Not typeahead: its buffer resets after a second, so a slow run picks another colour.)
    await tabTo('Format and layers')
    await userEvent.keyboard('{Enter}')
    await expect.element(page.getByRole('complementary', { name: 'Format and layers' })).toBeVisible()
    await tabTo('Card')
    await userEvent.keyboard('{Enter}')
    await expect.element(page.getByRole('option', { name: 'Brand' })).toBeVisible()
    for (let step = 0; step < 20 && document.activeElement?.textContent !== 'Brand'; step++) await userEvent.keyboard('{ArrowDown}')
    await expect.element(page.getByRole('option', { name: 'Brand' })).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    await expect.poll(() => operations.length).toBe(3)
    expect(lastOp().changes[0]).toMatchObject({ op: 'elements.update', updates: [{ element_id: rect, set: { style: { fill: { token: 'brand' } } } }] })
    // Only what changed is sent; the box and flags are not cleared.
    expect(Object.keys((lastOp().changes[0] as { updates: { set: object }[] }).updates[0].set)).toEqual(['style'])

    // Layers: arrow to the tab, then add the text to the selection with Shift+Enter.
    await tabTo('Format')
    await userEvent.keyboard('{ArrowRight}')
    await expect.element(page.getByRole('tab', { name: 'Layers' })).toHaveAttribute('aria-selected', 'true')
    await tabTo(/^Text /)
    await userEvent.keyboard('{Shift>}{Enter}{/Shift}')
    await expect.poll(() => document.querySelectorAll('[data-selected]').length).toBe(2)

    // Connect the two from the Arrange menu, typing to reach the item.
    await tabTo('Arrange', { back: true })
    await userEvent.keyboard('{Enter}')
    await expect.element(page.getByRole('menu')).toBeVisible()
    // Tabbing back past the canvas items kept both selected.
    expect(document.querySelectorAll('[data-selected]').length).toBe(2)
    await userEvent.keyboard('Connect')
    await expect.element(page.getByRole('menuitem', { name: 'Connect with a line' })).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    await expect.poll(() => operations.length).toBe(4)
    expect(lastOp().changes[0]).toMatchObject({ op: 'elements.add', elements: [{ kind: 'connector',
      start: { kind: 'element', element_id: rect }, end: { kind: 'element', element_id: TEXT_WIDGET } }] })

    // Send the rectangle back with Alt+Down on its name in Layers, then undo that.
    await expect.element(page.getByRole('menu')).not.toBeInTheDocument()
    await tabTo('Shape Rectangle 1')
    await userEvent.keyboard('{Alt>}{ArrowDown}{/Alt}')
    await expect.poll(() => operations.length).toBe(5)
    expect(lastOp().changes[0]).toMatchObject({ op: 'elements.reorder' })
    await expect.poll(announced).toMatch(/^Rectangle 1 is now 3 of 4 from the front$/)
    await userEvent.keyboard('{Control>}z{/Control}')
    await expect.poll(() => operations.length).toBe(6)
    // Back to the order before the move: back to front, the KPI, the text, the rectangle, the new line.
    const line = (operations[3] as { changes: { elements: { element_id: string }[] }[] }).changes[0].elements[0].element_id
    expect(lastOp().changes[0]).toMatchObject({ op: 'elements.reorder', element_ids: [KPI_WIDGET, TEXT_WIDGET, rect, line] })
    expect((operations[4] as { changes: { element_ids: string[] }[] }).changes[0].element_ids).toEqual([KPI_WIDGET, rect, TEXT_WIDGET, line])
  })

  const announced = () => document.querySelector('p[role="status"].sr-only')?.textContent
  async function editing() {
    const screen = await mount()
    await expect.element(screen.getByTestId('kpi-value')).toBeVisible()
    await screen.getByRole('button', { name: 'Edit' }).click()
    await expect.element(screen.getByRole('toolbar', { name: 'Add to dashboard' })).toBeVisible()
    return screen
  }

  it('selects everything, groups it with a shortcut, and undoes and redoes the group', async () => {
    await editing()
    await userEvent.keyboard('{Control>}a{/Control}')
    await expect.poll(() => document.querySelectorAll('[data-selected]').length).toBe(2)
    await expect.poll(announced).toBe('2 items selected')

    await userEvent.keyboard('{Control>}g{/Control}')
    await expect.poll(() => operations.length).toBe(1)
    const group = (operations[0].changes as { op: string; group_id: string; element_ids: string[] }[])[0]
    expect(group).toMatchObject({ op: 'elements.group', page_id: PAGE, element_ids: [KPI_WIDGET, TEXT_WIDGET], name: 'Group 1' })
    await expect.poll(announced).toBe('Grouped 2 items')
    await expect.poll(() => document.querySelector(`[data-element-id="${group.group_id}"][data-kind="group"]`)).not.toBeNull()

    await userEvent.keyboard('{Control>}z{/Control}')
    await expect.poll(() => operations.length).toBe(2)
    expect((operations[1].changes as { op: string }[]).map(change => change.op)).toEqual(['elements.ungroup', 'elements.reorder', 'page.mobile.set'])
    await expect.poll(() => document.querySelector('[data-kind="group"]')).toBeNull()

    await userEvent.keyboard('{Control>}{Shift>}z{/Shift}{/Control}')
    await expect.poll(() => operations.length).toBe(3)
    expect(operations[2].changes).toEqual(operations[0].changes)
  })

  it('aligns, locks and restacks the selection from the Arrange menu', async () => {
    const screen = await editing()
    const text = document.querySelector<HTMLElement>(`[data-element-id="${TEXT_WIDGET}"]`)!
    text.focus()
    await userEvent.keyboard('{Escape}')
    await userEvent.click(text, { position: { x: 400, y: 60 } })
    await expect.poll(() => text.hasAttribute('data-selected')).toBe(true)

    await screen.getByRole('button', { name: 'Arrange' }).click()
    await page.getByRole('menuitem', { name: 'Align right' }).click()
    await expect.poll(() => operations.length).toBe(1)
    // One item aligns with the page: its right edge on the page's right edge.
    expect(operations[0]).toMatchObject({ changes: [{ op: 'elements.arrange', placements: [{ element_id: TEXT_WIDGET, geometry: { x: 312, y: 6, w: 888 } }] }] })
    await expect.poll(announced).toBe('Aligned to the page')

    await screen.getByRole('button', { name: 'Arrange' }).click()
    await page.getByRole('menuitem', { name: 'Send to back' }).click()
    await expect.poll(() => operations.length).toBe(2)
    expect(operations[1]).toMatchObject({ changes: [{ op: 'elements.reorder', element_ids: [TEXT_WIDGET, KPI_WIDGET] }] })

    await screen.getByRole('button', { name: 'Arrange' }).click()
    await page.getByRole('menuitem', { name: 'Lock' }).click()
    await expect.poll(() => operations.length).toBe(3)
    expect(operations[2]).toMatchObject({ changes: [{ op: 'elements.update', updates: [{ element_id: TEXT_WIDGET, set: { locked: true } }] }] })
    // Locked items do not move from the keyboard.
    await expect.element(page.getByRole('menu')).not.toBeInTheDocument()
    await userEvent.keyboard('{Alt>}{ArrowLeft}{/Alt}')
    await expect.poll(announced).toBe('This item is locked')
    expect(operations).toHaveLength(3)
    await screen.getByRole('button', { name: 'Arrange' }).click()
    await expect.element(page.getByRole('menuitem', { name: 'Unlock' })).toBeVisible()
    await userEvent.keyboard('{Escape}')
  })

  it('hides the grid and turns snapping off for the page, and checks page settings before saving', async () => {
    const screen = await editing()
    await expect.poll(() => document.querySelector('[data-testid="canvas-grid"]')).not.toBeNull()
    const show = screen.getByRole('button', { name: 'Show grid' })
    await expect.element(show).toHaveAttribute('aria-pressed', 'true')
    await show.click()
    await expect.poll(() => operations.length).toBe(1)
    expect(operations[0]).toMatchObject({ changes: [{ op: 'page.canvas.set', page_id: PAGE, canvas: { width: 1200, grid: { show: false, snap: true, columns: 12 } } }] })
    await expect.poll(() => document.querySelector('[data-testid="canvas-grid"]')).toBeNull()
    await expect.element(show).toHaveAttribute('aria-pressed', 'false')

    await screen.getByRole('button', { name: 'Snap to grid' }).click()
    await expect.poll(() => operations.length).toBe(2)
    expect(operations[1]).toMatchObject({ changes: [{ op: 'page.canvas.set', canvas: { grid: { show: false, snap: false } } }] })

    await screen.getByRole('button', { name: 'Page and grid settings' }).click()
    const dialog = page.getByRole('dialog', { name: 'Page and grid settings' })
    await expect.element(dialog).toBeVisible()
    const columns = dialog.getByLabelText('Columns')
    await columns.fill('30')
    await expect.element(dialog.getByRole('alert')).toHaveTextContent('Enter a whole number for columns (1–24).')
    await expect.element(dialog.getByRole('button', { name: 'Save' })).toBeDisabled()
    await columns.fill('24')
    await dialog.getByLabelText('Gutter (px)').fill('64')
    await expect.element(dialog.getByRole('alert')).toHaveTextContent('The columns do not fit the page width.')
    await dialog.getByLabelText('Gutter (px)').fill('16')
    await columns.fill('6')
    await dialog.getByRole('button', { name: 'Save' }).click()
    await expect.poll(() => operations.length).toBe(3)
    expect(operations[2]).toMatchObject({ changes: [{ op: 'page.canvas.set', canvas: { size: 'custom', width: 1200, grid: { columns: 6, gutter: 16, snap: false } } }] })
    await expect.poll(announced).toBe('Page settings saved')
    await screen.getByRole('button', { name: 'Undo' }).click()
    await expect.poll(() => operations.length).toBe(4)
    expect(operations[3]).toMatchObject({ changes: [{ op: 'page.canvas.set', canvas: { grid: { columns: 12, gutter: 12 } } }] })
  })
})
