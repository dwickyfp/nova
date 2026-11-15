import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { ThemeProvider } from '@/context/theme-provider'
import { useAuthStore } from '@/stores/auth-store'
import { DashboardDataTab } from './dashboard-data-tab'
import '@/styles/index.css'

const id = (value: number) => `aaaaaaaa-0000-4000-8000-${String(value).padStart(12, '0')}`
const fetchMock = vi.fn()
const committed = vi.fn()
const reloaded = vi.fn()
const clients: QueryClient[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const field = { field_id: 'amount', name: 'Revenue', source_column: 'amount', logical_type: 'decimal',
  physical_type: 'DECIMAL(38,12)', semantic_role: 'measure', nullable: true }
const definition = { dataset_id: id(2), title: 'Pinned revenue',
  source: { kind: 'table', catalog: 'default_catalog', database: 'Sales', object_name: 'Orders', physical_id: '1001' },
  fields: [field], schema_fingerprint: 'a'.repeat(64) }
const snapshot = { resource_id: id(2), revision: 2, metadata_revision: 4, grants_revision: 1,
  publication_id: null, definition }
const index = { dashboard_id: id(1), revision: 3,
  datasets: [{ dataset_id: id(2), revision: 2, title: 'Pinned revenue' }] }
const preview = { dataset_id: id(2), revision: 2, fields: [field], offset: 0, has_more: false, next_offset: null,
  rows: [[{ type: 'decimal', value: '100000000000000000.123456789012' }]] }
function route(url: string) {
  if (url.includes(`/dashboards/${id(1)}/datasets?`)) return Promise.resolve(json(index))
  if (url.endsWith('/preview')) return Promise.resolve(json(preview))
  if (url.includes(`/datasets/${id(2)}?`)) return Promise.resolve(json(snapshot))
  if (url.endsWith('/catalogs')) return Promise.resolve(json({ catalogs: [] }))
  throw new Error('Unexpected test request')
}
async function mount(width = 'calc(100vw - 32px)', theme: 'light' | 'dark' = 'light') {
  const client = new QueryClient()
  clients.push(client)
  return render(<ThemeProvider defaultTheme={theme} storageKey='native-data-tab-test-theme'>
    <QueryClientProvider client={client}>
      <div style={{ width, height: 'calc(100dvh - 32px)', display: 'flex', minHeight: 0, minWidth: 0 }}>
        <DashboardDataTab dashboardId={id(1)} workspaceId='personal:alice' revision={3} onCommitted={committed} onReload={reloaded} />
      </div>
    </QueryClientProvider>
  </ThemeProvider>)
}
beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('data-tab-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  fetchMock.mockReset().mockImplementation(route)
  committed.mockReset()
  reloaded.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
  document.documentElement.classList.remove('dark')
  await page.viewport(1280, 720)
})

describe('shared dashboard Data tab', () => {
  it.each(['light', 'dark'] as const)('reads the pinned definition and previews only on request on a wide %s screen', async theme => {
    await page.viewport(1440, 900)
    const screen = await mount(undefined, theme)
    await expect.element(screen.getByRole('heading', { name: 'Pinned revenue' })).toBeVisible()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `/api/v1/viz/dashboards/${id(1)}/datasets?revision=3`,
      `/api/v1/viz/datasets/${id(2)}?draft=true&revision=2`,
    ])
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell', { name: '100000000000000000.123456789012' })).toBeVisible()
    expect(JSON.parse(fetchMock.mock.calls[2][1].body).revision).toBe(2)
    expect(committed).not.toHaveBeenCalled()
    await expect.element(screen.getByRole('complementary', { name: 'Dataset fields and lineage' })).toBeVisible()
    await expect.element(screen.getByRole('button', { name: 'Fields and lineage' })).not.toBeInTheDocument()
    expect(screen.container.firstElementChild!.getBoundingClientRect().width).toBeGreaterThan(1300)
    await page.screenshot({ element: screen.container.firstElementChild!,
      path: `__screenshots__/data-tab-desktop-${theme}-attempt-3.png` })
  })

  it('opens real dataset authoring from the empty state without loading rows', async () => {
    fetchMock.mockImplementation((url: string) => url.includes('/datasets?')
      ? Promise.resolve(json({ ...index, datasets: [] })) : route(url))
    const screen = await mount()
    await expect.element(screen.getByText('Add a source to this dashboard')).toBeVisible()
    await screen.getByRole('button', { name: 'Add dataset', exact: true }).last().click()
    await expect.element(screen.getByRole('textbox', { name: 'Dataset name' })).toBeVisible()
    await screen.getByRole('textbox', { name: 'Dataset name' }).fill('Unsaved source')
    await screen.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect.element(screen.getByText('Add a source to this dashboard')).toBeVisible()
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/preview'))).toBe(false)
    expect(committed).not.toHaveBeenCalled()
  })

  it('does not query or fabricate a title for an unavailable dataset', async () => {
    fetchMock.mockResolvedValue(json({ ...index, datasets: [{ ...index.datasets[0], title: null }] }))
    const screen = await mount()
    await expect.element(screen.getByRole('button', { name: 'Unavailable dataset 1' })).toBeDisabled()
    await expect.element(screen.getByText('Select an available dataset')).toBeVisible()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await screen.getByRole('button', { name: 'Reload dashboard' }).click()
    expect(reloaded).toHaveBeenCalledTimes(1)
  })

  it('opens the existing dataset library only when requested from the add menu', async () => {
    fetchMock.mockImplementation((url: string) => url.startsWith('/api/v1/viz/datasets?')
      ? Promise.resolve(json([])) : route(url))
    const screen = await mount()
    await expect.element(screen.getByRole('heading', { name: 'Pinned revenue' })).toBeVisible()
    expect(fetchMock.mock.calls.some(([url]) => url.startsWith('/api/v1/viz/datasets?'))).toBe(false)
    await screen.getByRole('button', { name: 'Add dataset', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Use existing dataset', exact: true }).click()
    await expect.element(screen.getByText('No editable datasets are available on this page.')).toBeVisible()
    expect(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0]).toContain('workspace_id=personal%3Aalice&draft=true')
    await screen.getByRole('button', { name: 'Cancel' }).click()
    await expect.element(screen.getByRole('heading', { name: 'Pinned revenue' })).toBeVisible()
    expect(committed).not.toHaveBeenCalled()
  })

  it('refuses a latest-draft response when the dashboard pins an earlier revision', async () => {
    fetchMock.mockImplementation((url: string) => url.includes(`/datasets/${id(2)}?`)
      ? Promise.resolve(json({ ...snapshot, revision: 4,
        definition: { ...definition, title: 'Wrong newer definition' } })) : route(url))
    const screen = await mount()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('This dataset is unavailable')
    expect(screen.container.textContent).not.toContain('Wrong newer definition')
    await expect.element(screen.getByRole('button', { name: 'Run preview' })).not.toBeInTheDocument()
  })

  it('clears data and pending authoring when the role changes', async () => {
    const screen = await mount()
    await expect.element(screen.getByRole('button', { name: 'Run preview' })).toBeVisible()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell')).toBeVisible()
    await screen.getByRole('button', { name: 'Add dataset', exact: true }).click()
    await page.getByRole('menuitem', { name: 'New dataset', exact: true }).click()
    await screen.getByRole('textbox', { name: 'Dataset name' }).fill('Prior-role draft')
    fetchMock.mockResolvedValue(json({ detail: 'private-denial-marker' }, 403))
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    await expect.element(screen.getByRole('alert')).toHaveTextContent('Datasets are unavailable')
    expect(screen.container.textContent).not.toContain('private-denial-marker')
    expect(screen.container.textContent).not.toContain('Pinned revenue')
    expect(screen.container.textContent).not.toContain('100000000000000000.123456789012')
    await expect.element(screen.getByRole('textbox', { name: 'Dataset name' })).not.toBeInTheDocument()
  })

  it.each(['light', 'dark'] as const)('collapses the inspector in a narrow artifact panel on a wide %s screen', async theme => {
    await page.viewport(1440, 900)
    const screen = await mount('520px', theme)
    await expect.element(screen.getByRole('heading', { name: 'Pinned revenue' })).toBeVisible()
    const fields = screen.getByRole('button', { name: 'Fields and lineage' })
    await expect.element(fields).toBeVisible()
    await fields.click()
    await expect.element(page.getByRole('dialog')).toBeVisible()
    await expect.element(page.getByRole('table', { name: 'Inspected dataset fields' })).toBeVisible()
    await userEvent.keyboard('{Escape}')
    await expect.element(fields).toHaveFocus()
    expect(screen.container.scrollWidth).toBeLessThanOrEqual(1440)
    await page.screenshot({ element: screen.container.firstElementChild!,
      path: `__screenshots__/data-tab-panel-${theme}.png` })
  })

  it('keeps a populated preview within a narrow viewport and preserves the dataset header', async () => {
    await page.viewport(390, 844)
    const screen = await mount()
    await expect.element(screen.getByRole('button', { name: 'Run preview' })).toBeVisible()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell')).toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390)
    expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(844)
    await expect.element(screen.getByRole('heading', { name: 'Datasets' })).toBeVisible()
    await expect.element(screen.getByRole('heading', { name: 'Pinned revenue' })).toBeVisible()
    await page.screenshot({ element: screen.container.firstElementChild!,
      path: '__screenshots__/data-tab-mobile.png' })
  })
})
