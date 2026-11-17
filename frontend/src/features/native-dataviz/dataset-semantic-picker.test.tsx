import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { DatasetSemanticPicker } from './dataset-semantic-picker'
import '@/styles/index.css'

const fetchMock = vi.fn()
const selected = vi.fn()
const clients: QueryClient[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const view = { id: 'semantic-sales', name: 'Sales model', database_name: 'Sales', status: 'ACTIVE', active_version: 2 }
const version = { version: 2, status: 'ACTIVE', fingerprint: 'a'.repeat(64), definition: {
  metrics: [{ name: 'revenue' }], datasets: [{ name: 'sales', fields: [{ name: 'region', kind: 'dimension' }] }],
} }
async function mount() {
  const client = new QueryClient()
  clients.push(client)
  return render(<QueryClientProvider client={client}><DatasetSemanticPicker onSelect={selected} /></QueryClientProvider>)
}
async function choose() {
  await expect.element(page.getByRole('combobox', { name: 'Published Semantic View' })).toBeEnabled()
  await page.getByRole('combobox', { name: 'Published Semantic View' }).click()
  await page.getByRole('option', { name: 'Sales model · Sales' }).click()
}
beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('semantic-picker-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  selected.mockReset()
  fetchMock.mockReset().mockImplementation((url: string) => Promise.resolve(json(
    url.endsWith('/semantic-views') ? [view] : { ...view, versions: [version] },
  )))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
})

describe('published semantic source selection', () => {
  it('does not substitute a draft or another version when the published pin is unavailable', async () => {
    fetchMock.mockImplementation((url: string) => Promise.resolve(json(url.endsWith('/semantic-views') ? [view]
      : { ...view, versions: [{ ...version, version: 3, status: 'DRAFT',
        definition: { metrics: [{ name: 'Private draft metric' }] } }] })))
    const screen = await mount()
    await choose()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('no longer has an available published version')
    expect(screen.container.textContent).not.toContain('Private draft metric')
    expect(selected.mock.calls.every(([value]) => value === null)).toBe(true)
  })

  it('clears a selection when the last metric is removed and keeps qualified dimensions', async () => {
    const screen = await mount()
    await choose()
    await expect.element(screen.getByRole('checkbox', { name: 'Metrics: revenue' })).toBeVisible()
    await screen.getByRole('checkbox', { name: 'Dimensions: sales.region' }).click()
    expect(selected).toHaveBeenLastCalledWith(null)
    await screen.getByRole('checkbox', { name: 'Metrics: revenue' }).click()
    expect(selected).toHaveBeenLastCalledWith({ kind: 'semantic_model', model_id: view.id,
      model_revision: 2, model_fingerprint: version.fingerprint,
      metrics: ['revenue'], dimensions: ['sales.region'], named_filters: [] })
    await screen.getByRole('checkbox', { name: 'Metrics: revenue' }).click()
    expect(selected).toHaveBeenLastCalledWith(null)
  })

  it('drops old-role semantic definitions arriving after the security context changes', async () => {
    let finish!: (value: Response) => void
    fetchMock.mockImplementation((url: string) => url.endsWith('/semantic-views')
      ? Promise.resolve(json([view])) : new Promise<Response>(resolve => { finish = resolve }))
    const screen = await mount()
    await choose()
    await expect.poll(() => fetchMock.mock.calls.length).toBe(2)
    const signal = fetchMock.mock.calls[1][1].signal as AbortSignal
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    await expect.element(screen.getByRole('combobox', { name: 'Published Semantic View' }))
      .toHaveTextContent('Choose a published Semantic View')
    expect(signal.aborted).toBe(true)
    finish(json({ ...view, versions: [version] }))
    await expect.poll(() => clients[0].isFetching()).toBe(0)
    await expect.element(screen.getByRole('checkbox', { name: 'Metrics: revenue' })).not.toBeInTheDocument()
  })

  it('shows unavailable and empty states without displaying dependency diagnostics', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'private-semantic-marker' }, 403))
    const screen = await mount()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('could not be loaded with your current access')
    expect(screen.container.textContent).not.toContain('private-semantic-marker')
    fetchMock.mockResolvedValue(json([]))
    await screen.getByRole('button', { name: 'Retry loading Semantic Views' }).click()
    await expect.element(screen.getByRole('status')).toHaveTextContent('No published Semantic Views are available')
    expect(selected.mock.calls.every(([value]) => value === null)).toBe(true)
  })
})
