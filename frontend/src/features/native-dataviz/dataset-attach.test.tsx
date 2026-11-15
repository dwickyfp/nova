import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { DatasetAttach } from './dataset-attach'
import '@/styles/index.css'

const id = (value: number) => `aaaaaaaa-0000-4000-8000-${String(value).padStart(12, '0')}`
const fetchMock = vi.fn()
const saved = vi.fn()
const clients: QueryClient[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const summary = (value: number) => ({ resource_id: id(value), kind: 'dataset', title: `Dataset ${value}`,
  owner_name: 'alice', workspace_id: 'personal:alice', updated_at: '2026-11-14T00:00:00Z',
  metadata_revision: 4, grants_revision: 1, current_publication_id: null, revision: 3 })
const definition = { dataset_id: id(3), title: 'Dataset 3',
  source: { kind: 'table', catalog: 'default_catalog', database: 'Sales', object_name: 'Orders', physical_id: '1001' },
  fields: [{ field_id: 'amount', name: 'Revenue', source_column: 'amount', logical_type: 'decimal',
    physical_type: 'DECIMAL(38,12)', semantic_role: 'measure', nullable: true }], schema_fingerprint: 'a'.repeat(64) }
const snapshot = { resource_id: id(3), revision: 3, metadata_revision: 4, grants_revision: 1,
  publication_id: null, definition }
function receipt(options: RequestInit) {
  const body = JSON.parse(String(options.body))
  return { operation_id: body.operation_id, resource_id: id(1), base_revision: body.expected_revision,
    new_revision: body.expected_revision + 1, event_sequence: 1, committed_at: '2026-11-14T00:00:00Z' }
}
async function route(url: string, options: RequestInit) {
  if (url.includes('/datasets?')) return json([summary(2), summary(3)])
  if (url.includes(`/datasets/${id(3)}?`)) return json(snapshot)
  if (url.endsWith('/operations')) return json(receipt(options))
  throw new Error('Unexpected test request')
}
function Harness() {
  const [revision, setRevision] = useState(5)
  return <DatasetAttach dashboardId={id(1)} workspaceId='personal:alice' revision={revision}
    references={[{ dataset_id: id(2), revision: 1, title: 'Already attached' },
      ...(revision > 5 ? [{ dataset_id: id(4), revision: 2, title: 'Concurrent addition' }] : [])]}
    onSaved={saved} onReload={() => setRevision(6)} onCancel={() => {}} />
}
async function mount() {
  const client = new QueryClient()
  clients.push(client)
  return render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>)
}
const saves = () => fetchMock.mock.calls.filter(([url]) => url.endsWith('/operations'))
async function choose() {
  await page.getByRole('button', { name: 'Dataset 3 Revision 3', exact: true }).click()
  await expect.element(page.getByRole('table', { name: 'Inspected dataset fields' })).toBeVisible()
}
beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('dataset-attach-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  fetchMock.mockReset().mockImplementation(route)
  saved.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
})

describe('reuse a dataset in the shared Data tab', () => {
  it('checks the selected exact revision and adds its reference without copying or querying rows', async () => {
    const screen = await mount()
    await expect.element(screen.getByRole('button', { name: 'Dataset 2 Already added' })).toBeDisabled()
    await choose()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/viz/datasets?workspace_id=personal%3Aalice&draft=true&limit=50')
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/v1/viz/datasets/${id(3)}?draft=true&revision=3`)
    await screen.getByRole('button', { name: 'Add selected revision' }).click()
    await expect.element(screen.getByRole('status')).toHaveTextContent('Dataset added to this dashboard.')
    const command = JSON.parse(saves()[0][1].body)
    expect(command).toEqual({ operation_id: expect.any(String), expected_revision: 5,
      changes: [{ op: 'datasets.replace', datasets: [
        { dataset_id: id(2), revision: 1 }, { dataset_id: id(3), revision: 3 },
      ] }] })
    expect(saved).toHaveBeenCalledWith(expect.objectContaining({ new_revision: 6 }), id(3))
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('retains the exact intent on an unknown outcome and forbids cancelling or changing the selection', async () => {
    let attempts = 0
    fetchMock.mockImplementation((url: string, options: RequestInit) => url.endsWith('/operations') && attempts++ === 0
      ? Promise.reject(new TypeError('private-network-marker')) : route(url, options))
    const screen = await mount()
    await choose()
    await screen.getByRole('button', { name: 'Add selected revision' }).click()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The save result is not confirmed')
    expect(saved).not.toHaveBeenCalled()
    expect(screen.container.textContent).not.toContain('private-network-marker')
    await expect.element(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await expect.element(screen.getByRole('button', { name: 'Reload list' })).toBeDisabled()
    await screen.getByRole('button', { name: 'Retry save' }).click()
    await expect.element(screen.getByRole('status')).toHaveTextContent('Dataset added')
    expect(saves()[1][1].body).toBe(saves()[0][1].body)
    expect(saves()[1][1].headers).toEqual(saves()[0][1].headers)
  })

  it('requires reload after a conflict and preserves concurrent dataset additions in the fresh command', async () => {
    let attempts = 0
    fetchMock.mockImplementation((url: string, options: RequestInit) => url.endsWith('/operations') && attempts++ === 0
      ? Promise.resolve(json({ detail: 'private-conflict-marker' }, 409)) : route(url, options))
    const screen = await mount()
    await choose()
    await screen.getByRole('button', { name: 'Add selected revision' }).click()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The dashboard changed')
    await expect.element(screen.getByRole('button', { name: 'Add selected revision' })).toBeDisabled()
    await screen.getByRole('button', { name: 'Reload dashboard' }).click()
    await screen.getByRole('button', { name: 'Add selected revision' }).click()
    await expect.element(screen.getByRole('status')).toHaveTextContent('Dataset added')
    const first = JSON.parse(saves()[0][1].body)
    const next = JSON.parse(saves()[1][1].body)
    expect(next.operation_id).not.toBe(first.operation_id)
    expect(next.expected_revision).toBe(6)
    expect(next.changes[0].datasets).toEqual([
      { dataset_id: id(2), revision: 1 }, { dataset_id: id(4), revision: 2 }, { dataset_id: id(3), revision: 3 },
    ])
  })

  it('refuses an unavailable selected revision without displaying returned details or enabling a save', async () => {
    fetchMock.mockImplementation((url: string, options: RequestInit) => url.includes(`/datasets/${id(3)}?`)
      ? Promise.resolve(json({ detail: 'private-revision-marker' }, 403)) : route(url, options))
    const screen = await mount()
    await screen.getByRole('button', { name: 'Dataset 3 Revision 3', exact: true }).click()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('This dataset revision is unavailable')
    await expect.element(screen.getByRole('button', { name: 'Add selected revision' })).toBeDisabled()
    expect(screen.container.textContent).not.toContain('private-revision-marker')
    expect(saves()).toHaveLength(0)
  })

  it('uses the keyset cursor for a new page and clears the prior selection', async () => {
    fetchMock.mockImplementation((url: string, options: RequestInit) => url.includes('/datasets?')
      ? Promise.resolve(json(url.includes('before_time') ? [] : Array.from({ length: 50 }, (_, i) => summary(i + 3))))
      : route(url, options))
    const screen = await mount()
    await choose()
    await screen.getByRole('button', { name: 'Next page' }).click()
    await expect.element(screen.getByText('No editable datasets are available on this page.')).toBeVisible()
    await expect.element(screen.getByRole('table', { name: 'Inspected dataset fields' })).not.toBeInTheDocument()
    await expect.element(screen.getByRole('button', { name: 'Add selected revision' })).toBeDisabled()
    const request = new URL(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0], 'https://nova.invalid')
    expect(request.searchParams.get('before_id')).toBe(id(52))
    expect(request.searchParams.get('before_time')).toBe('2026-11-14T00:00:00Z')
    await screen.getByRole('button', { name: 'Reload list' }).click()
    await expect.element(screen.getByRole('button', { name: 'Dataset 3 Revision 3', exact: true })).toBeVisible()
  })

  it('aborts a pending save and discards its late receipt on role change', async () => {
    let settle: (value: Response) => void = () => {}
    let pending: RequestInit | undefined
    fetchMock.mockImplementation((url: string, options: RequestInit) => {
      if (!url.endsWith('/operations')) return route(url, options)
      pending = options
      return new Promise<Response>(resolve => { settle = resolve })
    })
    const screen = await mount()
    await choose()
    await screen.getByRole('button', { name: 'Add selected revision' }).click()
    await expect.element(screen.getByRole('status')).toHaveTextContent('Adding dataset')
    fetchMock.mockResolvedValue(json({ detail: 'denied' }, 403))
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    await expect.element(screen.getByRole('alert')).toHaveTextContent('Datasets could not be loaded')
    expect(pending!.signal!.aborted).toBe(true)
    settle(json(receipt(pending!)))
    await expect.poll(() => saved.mock.calls.length).toBe(0)
    expect(screen.container.textContent).not.toContain('Dataset 3')
    await expect.element(screen.getByRole('table', { name: 'Inspected dataset fields' })).not.toBeInTheDocument()
  })
})
