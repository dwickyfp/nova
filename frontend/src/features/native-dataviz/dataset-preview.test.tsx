import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { DatasetPreviewPane } from './dataset-preview'
import '@/styles/index.css'

const datasetId = 'aaaaaaaa-0000-4000-8000-000000000001'
const fieldId = 'aaaaaaaa-0000-4000-8000-000000000002'
const fetchMock = vi.fn()
const clients: QueryClient[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
function result(offset = 0, count = 1, more = false) {
  return {
    dataset_id: datasetId, revision: 2, offset, has_more: more,
    next_offset: more ? offset + count : null,
    fields: [{ field_id: fieldId, source_column: 'amount', name: 'Amount', logical_type: 'decimal',
      physical_type: 'DECIMAL(38,12)', semantic_role: 'measure', nullable: false }],
    rows: Array.from({ length: count }, (_, index) => [{ type: 'decimal', value: `${offset + index + 1}.000000000001` }]),
  }
}
async function mount() {
  const client = new QueryClient()
  clients.push(client)
  return render(<QueryClientProvider client={client}>
    <div style={{ height: 400, width: '100%', display: 'flex', minWidth: 0 }}>
      <DatasetPreviewPane datasetId={datasetId} revision={2} draft title='Sales' />
    </div>
  </QueryClientProvider>)
}

beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('preview-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValue(json(result()))
})
afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
  await page.viewport(1280, 720)
})

describe('dataset preview workflow', () => {
  it('waits for an explicit preview and renders the typed result without fabricating a total', async () => {
    const screen = await mount()
    expect(fetchMock).not.toHaveBeenCalled()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell', { name: '1.000000000001' })).toBeVisible()
    await expect.element(screen.getByText('Rows 1–1', { exact: true })).toBeVisible()
    await expect.element(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    await expect.element(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ revision: 2, draft: true, offset: 0, limit: 100, parameters: [] })
    expect(fetchMock.mock.calls[0][1].cache).toBe('no-store')
    expect(screen.container.textContent).not.toContain('total')
  })

  it('uses only the server next offset and resets to the first page when the limit changes', async () => {
    fetchMock.mockImplementation((_url, options) => {
      const { offset, limit } = JSON.parse(options.body)
      return Promise.resolve(json(result(offset, offset === 0 ? limit : 1, offset === 0)))
    })
    const screen = await mount()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByText('Rows 1–100', { exact: true })).toBeVisible()
    await screen.getByRole('button', { name: 'Next' }).click()
    await expect.element(screen.getByText('Rows 101–101', { exact: true })).toBeVisible()
    await screen.getByRole('combobox', { name: 'Row limit' }).click()
    await screen.getByRole('option', { name: '25', exact: true }).click()
    await expect.element(screen.getByText('Rows 1–25', { exact: true })).toBeVisible()
    expect(fetchMock.mock.calls.map(([, options]) => JSON.parse(options.body).offset)).toEqual([0, 100, 0])
    await screen.getByRole('button', { name: 'Next' }).click()
    await expect.element(screen.getByText('Rows 26–26', { exact: true })).toBeVisible()
    await screen.getByRole('button', { name: 'Previous' }).click()
    await expect.element(screen.getByText('Rows 1–25', { exact: true })).toBeVisible()
  })

  it('aborts in-flight preview and discards a late response after a role change', async () => {
    let settle!: (value: Response) => void
    fetchMock.mockImplementation(() => new Promise<Response>(resolve => { settle = resolve }))
    const screen = await mount()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByText('Loading preview…')).toBeVisible()
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    await expect.element(screen.getByRole('button', { name: 'Run preview' })).toBeVisible()
    expect(signal.aborted).toBe(true)
    settle(json(result()))
    await expect.poll(() => clients[0].isFetching()).toBe(0)
    expect(screen.container.querySelector('[role=grid]')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('aborts a preview when its panel is unmounted', async () => {
    fetchMock.mockImplementation((_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    const screen = await mount()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal
    await screen.unmount()
    expect(signal.aborted).toBe(true)
  })

  it.each([403, 409, 422, 429, 504])('shows a safe refusal for HTTP %s without an automatic retry', async status => {
    fetchMock.mockResolvedValue(json({ detail: 'private source expression' }, status))
    const screen = await mount()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('alert')).toBeVisible()
    expect(screen.container.textContent).not.toContain('private source expression')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(screen.container.querySelector('[role=grid]')).toBeNull()
  })

  it('shows an empty result with no invented rows and fits a narrow panel', async () => {
    await page.viewport(320, 640)
    fetchMock.mockResolvedValue(json(result(0, 0)))
    const screen = await mount()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByText('No rows returned.', { exact: true })).toBeVisible()
    await expect.element(screen.getByText('0 rows', { exact: true })).toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320)
    expect(screen.container.querySelector('[role=gridcell]')).toBeNull()
  })
})
