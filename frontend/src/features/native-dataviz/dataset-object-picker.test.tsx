import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { DatasetObjectPicker } from './dataset-object-picker'
import '@/styles/index.css'

const fetchMock = vi.fn()
const selected = vi.fn()
const clients: QueryClient[] = []
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
})
const catalog = { catalogs: [
  { name: 'default_catalog', databases: ['Sales', 'Other', 'NOVA_SYSTEM', 'information_schema'] },
  { name: 'external', databases: ['External records'] },
] }

async function mount() {
  const client = new QueryClient()
  clients.push(client)
  return render(<QueryClientProvider client={client}>
    <DatasetObjectPicker onSelect={selected} />
  </QueryClientProvider>)
}
beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('picker-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  fetchMock.mockReset()
  selected.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockImplementation((url: string) => Promise.resolve(json(url.endsWith('/catalogs')
    ? catalog : { database: 'Sales', tables: [{ name: 'Orders' }], views: [{ name: 'Revenue' }] })))
})
afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
  document.documentElement.classList.remove('dark')
  await page.viewport(1280, 720)
})

describe('native source catalog picker', () => {
  it('uses the governed explorer and preserves object kind without inventing a physical ID', async () => {
    const screen = await mount()
    await expect.element(screen.getByRole('button', { name: 'Dataset database' })).toBeEnabled()
    await screen.getByRole('button', { name: 'Dataset database' }).click()
    expect(document.body.textContent).not.toContain('NOVA_SYSTEM')
    expect(document.body.textContent).not.toContain('External records')
    await page.getByRole('button', { name: 'Sales', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'Dataset table or view' })).toBeEnabled()
    await screen.getByRole('button', { name: 'Dataset table or view' }).click()
    await page.getByRole('button', { name: 'Revenue', exact: true }).click()
    expect(selected).toHaveBeenLastCalledWith({ kind: 'view', catalog: 'default_catalog',
      database: 'Sales', schema_name: 'default', object_name: 'Revenue' })
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/explorer/catalogs', '/api/v1/explorer/databases/Sales',
    ])
    await screen.getByRole('button', { name: 'Dataset database' }).click()
    await page.getByRole('button', { name: 'Other', exact: true }).click()
    expect(selected).toHaveBeenLastCalledWith(null)
    await expect.element(screen.getByRole('button', { name: 'Dataset table or view' }))
      .toHaveTextContent('Choose a table or view')
  })

  it('refuses dependency errors without exposing private error details', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'private-source-marker' }, 403))
    const screen = await mount()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('Sources could not be loaded')
    expect(screen.container.textContent).not.toContain('private-source-marker')
    expect(selected).not.toHaveBeenCalled()
    fetchMock.mockResolvedValue(json(catalog))
    await screen.getByRole('button', { name: 'Retry loading sources' }).click()
    await expect.element(screen.getByRole('button', { name: 'Dataset database' })).toBeEnabled()
  })

  it('aborts old-role metadata and hides prior selections when the role changes', async () => {
    let finish!: (response: Response) => void
    fetchMock.mockImplementation((url: string) => url.endsWith('/catalogs')
      ? Promise.resolve(json(catalog)) : new Promise<Response>(resolve => { finish = resolve }))
    const screen = await mount()
    await expect.element(screen.getByRole('button', { name: 'Dataset database' })).toBeEnabled()
    await screen.getByRole('button', { name: 'Dataset database' }).click()
    await page.getByRole('button', { name: 'Sales', exact: true }).click()
    await expect.poll(() => fetchMock.mock.calls.length).toBe(2)
    const signal = fetchMock.mock.calls[1][1].signal as AbortSignal
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    await expect.element(screen.getByRole('button', { name: 'Dataset database' }))
      .toHaveTextContent('Choose a database')
    expect(signal.aborted).toBe(true)
    finish(json({ database: 'Sales', tables: [{ name: 'Private late table' }], views: [] }))
    await expect.poll(() => clients[0].isFetching()).toBe(0)
    expect(screen.container.textContent).not.toContain('Private late table')
    await expect.element(screen.getByRole('button', { name: 'Dataset table or view' })).toBeDisabled()
  })

  it.each(['light', 'dark'])('keeps the source controls within a narrow %s viewport', async theme => {
    await page.viewport(320, 720)
    document.documentElement.classList.toggle('dark', theme === 'dark')
    fetchMock.mockResolvedValue(json({ catalogs: [] }))
    const screen = await mount()
    await expect.element(screen.getByRole('status')).toHaveTextContent('No sources are available')
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320)
    await expect.element(screen.getByRole('button', { name: 'Dataset database' })).toBeVisible()
    await expect.element(screen.getByRole('button', { name: 'Dataset table or view' })).toBeVisible()
  })
})
