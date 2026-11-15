import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { ThemeProvider } from '@/context/theme-provider'
import { useAuthStore } from '@/stores/auth-store'
import { DatasetCreate } from './dataset-create'
import '@/styles/index.css'

const dashboardId = 'aaaaaaaa-0000-4000-8000-000000000001'
const semanticId = 'aaaaaaaa-0000-4000-8000-000000000002'
const fetchMock = vi.fn()
const saved = vi.fn()
const cancelled = vi.fn()
const clients: QueryClient[] = []
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
})
const field = { field_id: 'amount', name: 'Revenue', source_column: 'amount', logical_type: 'decimal',
  physical_type: 'DECIMAL(38,12)', semantic_role: 'measure', nullable: true }
const physical = { kind: 'table', catalog: 'default_catalog', database: 'Sales', schema_name: 'default',
  object_name: 'Orders', physical_id: '1061' }
const view = { id: semanticId, name: 'Business revenue', database_name: 'Sales',
  active_version: 3, status: 'ACTIVE' }
const version = { version: 3, status: 'ACTIVE', fingerprint: 'c'.repeat(64), definition: {
  datasets: [{ name: 'orders', fields: [{ name: 'region', kind: 'dimension' }] }],
  metrics: [{ name: 'revenue' }], named_filters: [{ name: 'paid' }],
} }

function inspected(body: { dataset_id: string; source: Record<string, unknown>; parameters?: unknown[] }) {
  const source = body.source.kind === 'sql'
    ? { ...body.source, catalog: 'default_catalog', schema_name: 'default', ast_digest: 'b'.repeat(64), dependencies: [physical] }
    : body.source.kind === 'semantic_model'
      ? { ...body.source, catalog: 'default_catalog', database: 'Sales', schema_name: 'default', dependencies: [physical] }
      : { ...physical, kind: body.source.kind, database: body.source.database,
        object_name: body.source.object_name }
  return { dataset_id: body.dataset_id, source, fields: [field], schema_fingerprint: 'a'.repeat(64),
    ...(body.parameters ? { parameters: body.parameters } : {}) }
}
function receipt(options: RequestInit) {
  const headers = options.headers as Record<string, string>
  const base = Number(headers['If-Match'].replace(/"/g, ''))
  return { operation_id: headers['Idempotency-Key'], resource_id: dashboardId,
    base_revision: base, new_revision: base + 1, event_sequence: 1, committed_at: '2026-11-14T00:00:00Z' }
}
async function route(url: string, options: RequestInit) {
  if (url.endsWith('/catalogs')) return json({ catalogs: [{ name: 'default_catalog', databases: ['Sales'] }] })
  if (url.endsWith('/explorer/databases/Sales')) return json({ tables: [{ name: 'Orders' }, { name: 'Other' }], views: [] })
  if (url.endsWith('/semantic-views')) return json([view,
    { ...view, id: 'draft', name: 'Private draft', active_version: null, status: 'DRAFT' }])
  if (url.endsWith(`/semantic-views/${semanticId}`)) return json({ ...view, versions: [version] })
  if (url.endsWith('/inspect')) return json(inspected(JSON.parse(String(options.body))))
  if (url.endsWith(`/dashboards/${dashboardId}/datasets`)) return json(receipt(options), 201)
  throw new Error('Unexpected test request')
}
function Harness() {
  const [revision, setRevision] = useState(1)
  return <DatasetCreate dashboardId={dashboardId} dashboardRevision={revision}
    onSaved={saved} onCancel={cancelled} onReloadDashboard={() => setRevision(2)} />
}
async function mount(theme: 'light' | 'dark' = 'light') {
  const client = new QueryClient()
  clients.push(client)
  return render(<ThemeProvider defaultTheme={theme} storageKey='native-dataset-test-theme'>
    <QueryClientProvider client={client}><Harness /></QueryClientProvider>
  </ThemeProvider>)
}
async function chooseObject() {
  await page.getByRole('textbox', { name: 'Dataset name' }).fill('Sales data')
  await expect.element(page.getByRole('button', { name: 'Dataset database' })).toBeEnabled()
  await page.getByRole('button', { name: 'Dataset database' }).click()
  await page.getByRole('button', { name: 'Sales', exact: true }).click()
  await expect.element(page.getByRole('button', { name: 'Dataset table or view' })).toBeEnabled()
  await page.getByRole('button', { name: 'Dataset table or view' }).click()
  await page.getByRole('button', { name: 'Orders', exact: true }).click()
}
async function inspectObject() {
  await chooseObject()
  await page.getByRole('button', { name: 'Inspect columns', exact: true }).click()
  await expect.element(page.getByRole('table', { name: 'Inspected dataset fields' })).toBeVisible()
}
const saves = () => fetchMock.mock.calls.filter(([url]) => url.endsWith(`/dashboards/${dashboardId}/datasets`))

beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('dataset-authoring-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  fetchMock.mockReset().mockImplementation(route)
  saved.mockReset()
  cancelled.mockReset()
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

describe('dashboard dataset authoring', () => {
  it('inspects authoritative columns and atomically saves the source without fetching rows', async () => {
    const screen = await mount()
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeDisabled()
    await inspectObject()
    await expect.element(screen.getByRole('cell', { name: 'DECIMAL(38,12)' })).toBeVisible()
    await screen.getByRole('tab', { name: 'Lineage' }).click()
    await expect.element(screen.getByText('Sales.Orders', { exact: true })).toBeVisible()
    await screen.getByRole('button', { name: 'Save dataset' }).click()
    await expect.element(screen.getByRole('status')).toHaveTextContent('Dataset saved to this dashboard.')
    expect(saved).toHaveBeenCalledTimes(1)
    const [url, options] = saves()[0]
    const body = JSON.parse(options.body)
    expect(url).toContain(`/dashboards/${dashboardId}/datasets`)
    expect(body.dataset).toMatchObject({ title: 'Sales data', expected_physical_id: '1061',
      expected_schema_fingerprint: 'a'.repeat(64), source: { kind: 'table', object_name: 'Orders' } })
    expect(body.dataset).not.toHaveProperty('fields')
    expect(body.dataset).not.toHaveProperty('workspace_id')
    expect(options.headers['If-Match']).toBe('"1"')
    expect(saved.mock.calls[0][1]).toBe(body.dataset.dataset_id)
    expect(fetchMock.mock.calls.some(([path]) => path.endsWith('/preview'))).toBe(false)
    await expect.element(screen.getByRole('button', { name: 'Run preview' })).toBeVisible()
  })

  it('invalidates inspected columns when a different object is selected', async () => {
    const screen = await mount()
    await inspectObject()
    await screen.getByRole('button', { name: 'Dataset table or view' }).click()
    await page.getByRole('button', { name: 'Other', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeDisabled()
    await expect.element(screen.getByRole('table')).not.toBeInTheDocument()
    expect(saves()).toHaveLength(0)
  })

  it('retries the identical command after a lost response without inspecting a changed source', async () => {
    let attempt = 0
    fetchMock.mockImplementation((url, options) => {
      if (url.endsWith(`/dashboards/${dashboardId}/datasets`) && ++attempt === 1) return Promise.reject(new TypeError('connection lost'))
      return route(url, options)
    })
    const screen = await mount()
    await inspectObject()
    await screen.getByRole('button', { name: 'Save dataset' }).click()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('not confirmed')
    expect(saved).not.toHaveBeenCalled()
    await expect.element(screen.getByRole('textbox', { name: 'Dataset name' })).toBeDisabled()
    await expect.element(screen.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled()
    await screen.getByRole('button', { name: 'Retry save' }).click()
    await expect.poll(() => saved.mock.calls.length).toBe(1)
    expect(saves()).toHaveLength(2)
    expect(saves()[1][1].body).toBe(saves()[0][1].body)
    expect(saves()[1][1].headers).toEqual(saves()[0][1].headers)
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/inspect'))).toHaveLength(1)
  })

  it('retains a conflict draft and uses a fresh command only after reload and reinspection', async () => {
    let attempt = 0
    fetchMock.mockImplementation((url, options) => {
      if (url.endsWith(`/dashboards/${dashboardId}/datasets`) && ++attempt === 1) return Promise.resolve(json({ detail: 'private-error-marker' }, 409))
      return route(url, options)
    })
    const screen = await mount()
    await inspectObject()
    await screen.getByRole('button', { name: 'Save dataset' }).click()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('Your selections are retained')
    expect(screen.container.textContent).not.toContain('private-error-marker')
    await expect.element(screen.getByRole('textbox', { name: 'Dataset name' })).toHaveValue('Sales data')
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeDisabled()
    await screen.getByRole('button', { name: 'Reload dashboard' }).click()
    await screen.getByRole('button', { name: 'Inspect columns', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeEnabled()
    await screen.getByRole('button', { name: 'Save dataset' }).click()
    await expect.poll(() => saved.mock.calls.length).toBe(1)
    expect(saves()[1][1].headers['If-Match']).toBe('"2"')
    expect(saves()[1][1].headers['Idempotency-Key']).not.toBe(saves()[0][1].headers['Idempotency-Key'])
    expect(JSON.parse(saves()[1][1].body).dataset.dataset_id).toBe(JSON.parse(saves()[0][1].body).dataset.dataset_id)
  })

  it('discards an in-flight inspection and the whole draft on a role change', async () => {
    let finish!: (value: Response) => void
    fetchMock.mockImplementation((url, options) => url.endsWith('/inspect')
      ? new Promise<Response>(resolve => { finish = resolve }) : route(url, options))
    const screen = await mount()
    await chooseObject()
    await screen.getByRole('button', { name: 'Inspect columns', exact: true }).click()
    await expect.element(screen.getByText('Inspecting source columns…')).toBeVisible()
    const call = fetchMock.mock.calls.find(([url]) => url.endsWith('/inspect'))!
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    await expect.element(screen.getByRole('textbox', { name: 'Dataset name' })).toHaveValue('')
    expect(call[1].signal.aborted).toBe(true)
    finish(json(inspected(JSON.parse(call[1].body))))
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeDisabled()
    await expect.element(screen.getByRole('table')).not.toBeInTheDocument()
    expect(saved).not.toHaveBeenCalled()
  })

  it('does not deliver an old-role save receipt to the next role', async () => {
    let finish!: (value: Response) => void
    fetchMock.mockImplementation((url, options) => url.endsWith(`/dashboards/${dashboardId}/datasets`)
      ? new Promise<Response>(resolve => { finish = resolve }) : route(url, options))
    const screen = await mount()
    await inspectObject()
    await screen.getByRole('button', { name: 'Save dataset' }).click()
    await expect.element(screen.getByText('Saving dataset…')).toBeVisible()
    const options = saves()[0][1]
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    await expect.element(screen.getByRole('textbox', { name: 'Dataset name' })).toHaveValue('')
    expect(options.signal.aborted).toBe(true)
    finish(json(receipt(options), 201))
    await expect.poll(() => clients[0].isFetching()).toBe(0)
    expect(saved).not.toHaveBeenCalled()
    await expect.element(screen.getByText('Dataset saved to this dashboard.')).not.toBeInTheDocument()
  })

  it.each(['SELECT amount FROM Orders', 'SELECT SUM(amount) AS total FROM Orders',
    "SELECT amount FROM Orders WHERE region = 'West'"])(
  'preserves rapid input through the real Monaco SQL editor: %s', async sql => {
    const screen = await mount()
    await screen.getByRole('textbox', { name: 'Dataset name' }).fill('SQL revenue')
    await screen.getByRole('combobox', { name: 'Dataset source type' }).click()
    await page.getByRole('option', { name: 'SQL query', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'SQL database' })).toBeEnabled()
    await screen.getByRole('button', { name: 'SQL database' }).click()
    await page.getByRole('button', { name: 'Sales', exact: true }).click()
    await expect.element(screen.getByRole('textbox', { name: 'Dataset SQL' })).toBeVisible()
    await userEvent.type(screen.getByRole('textbox', { name: 'Dataset SQL' }), sql)
    await screen.getByRole('button', { name: 'Inspect columns', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeEnabled()
    await screen.getByRole('button', { name: 'Save dataset' }).click()
    await expect.poll(() => saved.mock.calls.length).toBe(1)
    expect(JSON.parse(saves()[0][1].body).dataset.source).toMatchObject({
      kind: 'sql', database: 'Sales', sql,
    })
    expect(JSON.parse(saves()[0][1].body).dataset).not.toHaveProperty('expected_physical_id')
  })

  it('declares SQL parameters, saves the analyzer-accepted declarations and offers them in preview', async () => {
    const screen = await mount()
    await screen.getByRole('textbox', { name: 'Dataset name' }).fill('Regional revenue')
    await screen.getByRole('combobox', { name: 'Dataset source type' }).click()
    await page.getByRole('option', { name: 'SQL query', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'SQL database' })).toBeEnabled()
    await screen.getByRole('button', { name: 'SQL database' }).click()
    await page.getByRole('button', { name: 'Sales', exact: true }).click()
    await userEvent.type(screen.getByRole('textbox', { name: 'Dataset SQL' }),
      'SELECT amount FROM Orders WHERE region IN (:regions) AND amount >= :floor')
    await screen.getByRole('button', { name: 'Add parameter' }).click()
    await screen.getByRole('textbox', { name: 'Parameter name' }).fill('1regions')
    await screen.getByRole('button', { name: 'Inspect columns', exact: true }).click()
    await expect.element(screen.getByText(/^Name 1regions with a letter/)).toBeVisible()
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/inspect'))).toBe(false)

    await screen.getByRole('textbox', { name: 'Parameter name' }).fill('regions')
    await screen.getByRole('checkbox', { name: 'Allow several values, for use with IN (…)' }).click()
    await screen.getByRole('button', { name: 'Add parameter' }).click()
    await screen.getByRole('textbox', { name: 'Parameter name' }).nth(1).fill('floor')
    await screen.getByRole('combobox', { name: 'Type', exact: true }).nth(1).click()
    await page.getByRole('option', { name: 'Decimal', exact: true }).click()
    await screen.getByRole('textbox', { name: 'Scale' }).fill('4')
    await screen.getByRole('combobox', { name: 'When no value is supplied' }).nth(1).click()
    await page.getByRole('option', { name: 'Use a default value', exact: true }).click()
    await screen.getByRole('textbox', { name: 'Default value' }).fill('0')
    await screen.getByRole('button', { name: 'Inspect columns', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeEnabled()
    const declared = [
      { parameter_id: 'regions', type: 'string', multiple: true, required: true, nullable: false, defaults: [] },
      { parameter_id: 'floor', type: 'decimal', multiple: false, required: false, nullable: false,
        defaults: [{ type: 'decimal', value: '0' }], precision: 18, scale: 4 },
    ]
    const inspect = fetchMock.mock.calls.find(([url]) => url.endsWith('/inspect'))!
    expect(JSON.parse(inspect[1].body).parameters).toMatchObject(declared)

    // A changed declaration discards the inspected schema until it is inspected again.
    await screen.getByRole('textbox', { name: 'Default value' }).fill('1')
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeDisabled()
    await screen.getByRole('textbox', { name: 'Default value' }).fill('0')
    await screen.getByRole('button', { name: 'Inspect columns', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeEnabled()
    await screen.getByRole('button', { name: 'Save dataset' }).click()
    await expect.poll(() => saved.mock.calls.length).toBe(1)
    expect(JSON.parse(saves()[0][1].body).dataset.parameters).toMatchObject(declared)
    await expect.element(screen.getByRole('group', { name: 'regions' })).toBeVisible()
    await expect.element(screen.getByRole('group', { name: 'floor' })).toBeVisible()
  })

  it('keeps the parameter editor within a phone-width viewport', async () => {
    await page.viewport(360, 720)
    const screen = await mount('dark')
    await screen.getByRole('combobox', { name: 'Dataset source type' }).click()
    await page.getByRole('option', { name: 'SQL query', exact: true }).click()
    await screen.getByRole('button', { name: 'Add parameter' }).click()
    await screen.getByRole('combobox', { name: 'Type', exact: true }).click()
    await page.getByRole('option', { name: 'Decimal', exact: true }).click()
    await screen.getByRole('combobox', { name: 'When no value is supplied' }).click()
    await page.getByRole('option', { name: 'Use a default value', exact: true }).click()
    const section = screen.container.querySelector('section[aria-label="Add dataset"]')!
    expect(section.scrollWidth).toBeLessThanOrEqual(section.clientWidth)
    await expect.element(screen.getByRole('button', { name: 'Remove Parameter 1' })).toBeVisible()
    await page.screenshot({ path: '__screenshots__/dataset-parameter-declarations-phone-dark.png' })
  })

  it('pins a published semantic version, metrics, dimensions and named filters', async () => {
    const screen = await mount()
    await screen.getByRole('textbox', { name: 'Dataset name' }).fill('Semantic revenue')
    await screen.getByRole('combobox', { name: 'Dataset source type' }).click()
    await page.getByRole('option', { name: 'Published Semantic View', exact: true }).click()
    await expect.element(screen.getByRole('combobox', { name: 'Published Semantic View' })).toBeEnabled()
    await screen.getByRole('combobox', { name: 'Published Semantic View' }).click()
    expect(document.body.textContent).not.toContain('Private draft')
    await page.getByRole('option', { name: 'Business revenue · Sales' }).click()
    await expect.element(screen.getByRole('checkbox', { name: 'Metrics: revenue' })).toBeVisible()
    await expect.element(screen.getByRole('button', { name: 'Inspect columns', exact: true })).toBeDisabled()
    await screen.getByRole('checkbox', { name: 'Metrics: revenue' }).click()
    await screen.getByRole('checkbox', { name: 'Dimensions: orders.region' }).click()
    await screen.getByRole('checkbox', { name: 'Named filters: paid' }).click()
    await screen.getByRole('button', { name: 'Inspect columns', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'Save dataset' })).toBeEnabled()
    await screen.getByRole('button', { name: 'Save dataset' }).click()
    await expect.poll(() => saved.mock.calls.length).toBe(1)
    expect(JSON.parse(saves()[0][1].body).dataset.source).toEqual({
      kind: 'semantic_model', model_id: semanticId, model_revision: 3, model_fingerprint: 'c'.repeat(64),
      metrics: ['revenue'], dimensions: ['orders.region'], named_filters: ['paid'],
    })
  })

  it.each(['light', 'dark'] as const)('keeps populated authoring within a narrow %s viewport', async theme => {
    await page.viewport(390, 844)
    const screen = await mount(theme)
    await inspectObject()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390)
    for (const label of ['Inspect again', 'Save dataset', 'Cancel']) {
      const button = screen.getByRole('button', { name: label, exact: true })
      expect(button.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44)
    }
  })
})
