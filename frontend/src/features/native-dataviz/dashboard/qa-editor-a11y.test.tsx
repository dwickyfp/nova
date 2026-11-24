import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cdp, page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/auth-store'
import type { DashboardDefinitionV2 } from '../contracts'
import { DashboardEditor } from './editor'
import { upcastDashboard } from './layout'
import '@/styles/index.css'

/* Release-QA accessibility and responsive scenarios for the dashboard editor (QA_MATRIX.csv). */

const DASHBOARD = 'dddddddd-0000-4000-8000-000000000001'
const DATASET = 'aaaaaaaa-0000-4000-8000-000000000001'
const CHART = 'cccccccc-0000-4000-8000-000000000001'
const PAGE = 'eeeeeeee-0000-4000-8000-000000000001'
const WIDGETS = ['ffffffff-0000-4000-8000-000000000001', 'ffffffff-0000-4000-8000-000000000002', 'ffffffff-0000-4000-8000-000000000003', 'ffffffff-0000-4000-8000-000000000004']
const TEXT_WIDGET = 'ffffffff-0000-4000-8000-000000000009'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const fetchMock = vi.fn()
const operations: Record<string, unknown>[] = []
const clients: QueryClient[] = []
let permissionList: string[] = []
let publicationId: string | null = null
let revision = 4
let definition: Record<string, unknown>

const kpi = {
  schema_version: 2, chart_id: CHART, title: 'Units sold', dataset_id: DATASET, dataset_revision: 1, visual: 'kpi',
  encoding: { kind: 'single_measure', measure: { field_id: 'units', aggregation: 'sum', format: { kind: 'number', decimals: 0, locale: 'en-US' }, axis: 'left', series: 'bar' } },
}

async function route(url: string, options: RequestInit = {}) {
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/permissions`)) return json(permissionList)
  if (url.includes(`/viz/dashboards/${DASHBOARD}/events`)) return json({ events: [], through: revision, next_sequence: revision })
  if (url.includes(`/viz/dashboards/${DASHBOARD}/datasets`)) return json({ dashboard_id: DASHBOARD, revision, datasets: [{ dataset_id: DATASET, revision: 1, title: 'Orders' }] })
  if (url.endsWith(`/viz/dashboards/${DASHBOARD}/operations`)) {
    operations.push(JSON.parse(String(options.body)))
    const key = (options.headers as Record<string, string>)['Idempotency-Key']
    revision += 1
    return json({ operation_id: key, resource_id: DASHBOARD, base_revision: revision - 1, new_revision: revision, event_sequence: revision, committed_at: '2026-11-16T00:00:00Z' })
  }
  if (url.includes(`/viz/dashboards/${DASHBOARD}?`)) {
    return json({ resource_id: DASHBOARD, revision, metadata_revision: revision, grants_revision: 1, publication_id: publicationId, definition: upcastDashboard(definition as unknown as DashboardDefinitionV2) })
  }
  if (url.includes(`/viz/charts/${CHART}?`)) return json({ resource_id: CHART, revision: 1, metadata_revision: 1, grants_revision: 1, publication_id: null, definition: kpi })
  if (url.includes(`/viz/datasets/${DATASET}?`)) {
    const field = (field_id: string, logical_type: string, semantic_role: string) =>
      ({ field_id, name: field_id, source_column: field_id, logical_type, physical_type: logical_type === 'string' ? 'VARCHAR(32)' : 'BIGINT', semantic_role, nullable: true })
    return json({ resource_id: DATASET, revision: 1, metadata_revision: 1, grants_revision: 1, publication_id: null, definition: {
      dataset_id: DATASET, title: 'Orders',
      source: { kind: 'table', catalog: 'default_catalog', database: 'Sales', object_name: 'Orders', physical_id: '1001' },
      fields: [field('region', 'string', 'dimension'), field('units', 'integer', 'measure')], schema_fingerprint: 'a'.repeat(64) } })
  }
  if (url.endsWith(`/viz/datasets/${DATASET}/query`)) {
    return json({ dataset_id: DATASET, revision: 1, columns: [{ key: 'm0', name: 'units', logical_type: 'decimal', role: 'measure', field_id: 'units', aggregation: 'sum' }],
      rows: [[{ type: 'decimal', value: '15257' }]], truncated: false })
  }
  return json({ detail: 'Not found' }, 404)
}

async function mount(width: number, height = 720) {
  const client = new QueryClient()
  clients.push(client)
  return render(<QueryClientProvider client={client}><TooltipProvider>
    <div data-testid='frame' style={{ height, width, display: 'flex' }}>
      <DashboardEditor dashboardId={DASHBOARD} workspaceId='personal' onBack={() => undefined} />
    </div>
  </TooltipProvider></QueryClientProvider>)
}

/** Tabs forward until `target` has focus; fails if it is not reachable from the keyboard. */
async function tabTo(target: HTMLElement, limit = 60) {
  for (let step = 0; step < limit && document.activeElement !== target; step += 1) await userEvent.keyboard('{Tab}')
  expect(document.activeElement).toBe(target)
}

/** Shift+Tabs backward until `target` has focus. */
async function tabBackTo(target: HTMLElement, limit = 60) {
  for (let step = 0; step < limit && document.activeElement !== target; step += 1) await userEvent.keyboard('{Shift>}{Tab}{/Shift}')
  expect(document.activeElement).toBe(target)
}

beforeEach(() => {
  permissionList = ['view', 'interact', 'comment', 'edit', 'publish', 'manage']
  publicationId = null
  revision = 4
  operations.length = 0
  definition = {
    schema_version: 2, dashboard_id: DASHBOARD, title: 'Sales overview', description: '', columns: 12,
    datasets: [{ dataset_id: DATASET, revision: 1 }], filters: [], actions: [],
    pages: [{ page_id: PAGE, title: 'Overview', widgets: [
      ...WIDGETS.map((widget_id, index) => ({ kind: 'chart', widget_id, title: '', locked: false,
        layout: { x: index * 3, y: 0, w: 3, h: 2 }, chart_ref: { chart_id: CHART, revision: 1 } })),
      { kind: 'text', widget_id: TEXT_WIDGET, title: '', locked: false, layout: { x: 0, y: 2, w: 12, h: 2 },
        text: 'Quarterly notes for the regional sales leads, kept short so a phone reader sees them whole.', style: 'body' },
    ] }],
  }
  useAuthStore.getState().auth.setAccessToken('qa-a11y-token')
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

describe('QA accessibility', { timeout: 30_000 }, () => {
  it('NDV-A11Y-006: a 390px phone shows a readable single-column viewer without horizontal overflow', async () => {
    permissionList = ['view', 'interact']
    publicationId = '99999999-0000-4000-8000-000000000001'
    await page.viewport(390, 844)
    const screen = await mount(390, 844)
    await expect.poll(() => screen.container.querySelectorAll('[data-testid="kpi-value"]').length).toBe(4)
    const grid = (screen.container.querySelector('.grid-stack') as unknown as { gridstack: { getColumn: () => number } }).gridstack
    await expect.poll(() => grid.getColumn()).toBe(1)
    const measure = () => [...WIDGETS, TEXT_WIDGET].map(id => screen.container.querySelector<HTMLElement>(`[gs-id="${id}"]`)!.getBoundingClientRect())
    // One column: every widget starts at the same left edge, is as wide as the others and sits below the previous one.
    await expect.poll(() => new Set(measure().map(box => Math.round(box.left))).size).toBe(1)
    await expect.poll(() => new Set(measure().map(box => Math.round(box.width))).size).toBe(1)
    await new Promise(resolve => setTimeout(resolve, 500))
    const boxes = measure()
    expect(new Set(boxes.map(box => Math.round(box.left))).size).toBe(1)
    const ordered = [...boxes].sort((a, b) => a.top - b.top)
    for (let index = 1; index < ordered.length; index += 1) expect(ordered[index].top).toBeGreaterThanOrEqual(ordered[index - 1].bottom - 1)
    for (const box of boxes) {
      expect(box.left).toBeGreaterThanOrEqual(0)
      expect(box.right).toBeLessThanOrEqual(390)
    }
    // Values stay readable: not clipped to nothing and not smaller than body text.
    for (const value of screen.container.querySelectorAll<HTMLElement>('[data-testid="kpi-value"]')) {
      expect(value.textContent).toBe('15,257')
      expect(value.scrollWidth).toBeLessThanOrEqual(value.clientWidth)
      expect(parseFloat(getComputedStyle(value).fontSize)).toBeGreaterThanOrEqual(16)
    }
    await expect.element(screen.getByRole('heading', { name: 'Sales overview' })).toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390)
    expect(document.body.scrollWidth).toBeLessThanOrEqual(390)
    const header = screen.container.querySelector('header')!
    expect(header.scrollWidth).toBeLessThanOrEqual(header.clientWidth)
    expect(operations).toHaveLength(0)
  })

  it('NDV-A11Y-007: at 768px tablet the inspector is a closable drawer and the actions stay reachable by keyboard', async () => {
    await page.viewport(768, 1024)
    const screen = await mount(768, 1024)
    await expect.poll(() => screen.container.querySelectorAll('[data-testid="kpi-value"]').length).toBe(4)
    const canvas = screen.container.querySelector<HTMLElement>('.grid-stack')!
    await tabTo(screen.getByRole('button', { name: 'Edit' }).element() as HTMLElement)
    await userEvent.keyboard('{Enter}')
    const toolbar = screen.getByRole('toolbar', { name: 'Add to dashboard' })
    await expect.element(toolbar).toBeVisible()
    const width = canvas.getBoundingClientRect().width
    await tabTo(toolbar.getByRole('button', { name: 'KPI' }).element() as HTMLElement)
    await userEvent.keyboard('{Enter}')

    const inspector = screen.getByRole('complementary', { name: 'Chart inspector' })
    await expect.element(inspector).toBeVisible()
    // A drawer over the canvas: the canvas keeps its width rather than being squeezed.
    const drawer = (inspector.element() as HTMLElement).parentElement!
    expect(getComputedStyle(drawer).position).toBe('absolute')
    expect(canvas.getBoundingClientRect().width).toBe(width)
    expect(drawer.getBoundingClientRect().right).toBeLessThanOrEqual(768)
    // Its own actions are reachable from the keyboard, including closing it.
    const close = inspector.getByRole('button', { name: 'Close inspector' }).element() as HTMLElement
    await tabTo(close)
    await tabTo(inspector.getByRole('textbox', { name: 'Chart name' }).element() as HTMLElement)
    await userEvent.keyboard('Units sold by tablet')
    await tabTo(inspector.getByRole('button', { name: 'Add to dashboard' }).element() as HTMLElement, 150)
    await tabBackTo(close, 150)
    await userEvent.keyboard('{Enter}')
    await expect.element(inspector).not.toBeInTheDocument()

    // The page actions are named buttons reachable from the keyboard at this width.
    for (const name of ['Chart', 'Text', 'Filters']) await tabTo(toolbar.getByRole('button', { name }).element() as HTMLElement)
    await tabBackTo(screen.getByRole('button', { name: 'Publish' }).element() as HTMLElement)
    await userEvent.keyboard('{Enter}')
    await expect.element(page.getByRole('dialog', { name: 'Publish dashboard' })).toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(768)
  })

  it('NDV-A11Y-009: every control reached by Tab is named and visibly focused, also in forced colours', async () => {
    const screen = await mount(1200)
    await expect.element(screen.getByRole('button', { name: 'Edit' })).toBeVisible()
    const nameOf = (element: Element) => (element.getAttribute('aria-label')
      ?? (element.getAttribute('aria-labelledby') ? document.getElementById(element.getAttribute('aria-labelledby')!)?.textContent : null)
      ?? element.textContent ?? '').trim()
    const indicator = (element: Element) => {
      const style = getComputedStyle(element)
      return { outline: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0, ring: style.boxShadow !== 'none' && style.boxShadow !== '' }
    }
    /** One full Tab cycle through the dashboard, from the top. */
    const walk = async () => {
      ;(document.activeElement as HTMLElement | null)?.blur()
      const seen: { name: string; outline: boolean; ring: boolean }[] = []
      for (let step = 0; step < 60; step += 1) {
        await userEvent.keyboard('{Tab}')
        const focused = document.activeElement
        if (!focused || focused === document.body || !screen.container.contains(focused)) continue
        if (seen.length && nameOf(focused) === seen[0].name) break
        seen.push({ name: nameOf(focused), ...indicator(focused) })
      }
      return seen
    }
    const check = (stops: Awaited<ReturnType<typeof walk>>, forced: boolean) => {
      for (const item of stops) {
        expect(item.name, 'a focusable control without a name').not.toBe('')
        // Forced colours drop shadows, so there the focus must be an outline.
        expect(forced ? item.outline : item.outline || item.ring, `no focus indicator on ${item.name}`).toBe(true)
      }
    }
    // The Playwright provider's CDP session; its typing is only declared by
    // that provider's package, which would also change global timer types.
    const session = cdp() as unknown as {
      send(method: string, params?: object): Promise<unknown>
    }
    const forcedColours = (value: 'active' | 'none') => session.send('Emulation.setEmulatedMedia', { features: [{ name: 'forced-colors', value }] })

    // Viewing: the header and page controls.
    const viewing = await walk()
    expect(viewing.map(item => item.name)).toEqual(expect.arrayContaining(['Back to dashboards', 'Refresh data', 'Edit', 'Overview']))
    check(viewing, false)
    // Editing: every widget is a stop, with its own actions.
    await screen.getByRole('button', { name: 'Edit' }).click()
    await expect.poll(() => screen.container.querySelectorAll('[aria-label$=" actions"]').length).toBeGreaterThan(0)
    const editing = await walk()
    expect(editing.some(item => item.name.endsWith('press Alt and an arrow key to move it'))).toBe(true)
    expect(editing.some(item => item.name.endsWith(' actions'))).toBe(true)
    check(editing, false)
    await forcedColours('active')
    try {
      expect(matchMedia('(forced-colors: active)').matches).toBe(true)
      check(await walk(), true)
    } finally {
      await forcedColours('none')
    }
  })

})
