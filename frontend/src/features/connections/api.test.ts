import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createConnection,
  createForeignCatalog,
  deleteConnection,
  fetchConnections,
  setConnectionCredentials,
} from './api'

const fetchMock = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({}),
  })
})

afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
})

function call(index = 0) {
  const [url, init] = fetchMock.mock.calls[index]
  return {
    url: url as string,
    method: init?.method ?? 'GET',
    body: init?.body ? JSON.parse(init.body as string) : undefined,
  }
}

describe('connections api', () => {
  it('lists and removes connections by name', async () => {
    await fetchConnections()
    await deleteConnection('sales pg')
    expect(call(0)).toMatchObject({ method: 'GET' })
    expect(call(0).url).toMatch(/\/catalog\/connections$/)
    expect(call(1).method).toBe('DELETE')
    expect(call(1).url).toMatch(/\/catalog\/connections\/sales%20pg$/)
  })

  it('sends entered credentials once, to the write-only endpoints', async () => {
    await createConnection({
      name: 'sales_pg',
      connector_type: 'postgres',
      endpoint: 'db:5432/sales',
      credentials: { username: 'reader', password: 'pw' },
    })
    await setConnectionCredentials('sales_pg', { username: 'reader', password: 'pw2' })
    expect(call(0)).toMatchObject({
      method: 'POST',
      body: { name: 'sales_pg', credentials: { username: 'reader', password: 'pw' } },
    })
    expect(call(1).method).toBe('PUT')
    expect(call(1).url).toMatch(/\/catalog\/connections\/sales_pg\/credentials$/)
  })

  it('creates a catalog from a connection name only', async () => {
    await createForeignCatalog({ name: 'sales', connection: 'sales_pg' })
    expect(call(0).url).toMatch(/\/catalog\/foreign-catalogs$/)
    expect(call(0).body).toEqual({ name: 'sales', connection: 'sales_pg' })
  })
})
