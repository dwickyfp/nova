import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  alterExternalCatalog,
  createExternalCatalog,
  deleteExternalCatalog,
  fetchCatalogTables,
  fetchExternalCatalogs,
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

function requestedUrl(index = 0) {
  return fetchMock.mock.calls[index][0] as string
}

function requestedMethod(index = 0) {
  return fetchMock.mock.calls[index][1]?.method ?? 'GET'
}

function requestedBody(index = 0) {
  const body = fetchMock.mock.calls[index][1]?.body
  return body ? JSON.parse(body as string) : undefined
}

describe('external catalogs API calls', () => {
  it('lists catalogs', async () => {
    await fetchExternalCatalogs()

    expect(requestedUrl()).toBe('/api/v1/external-catalogs')
    expect(requestedMethod()).toBe('GET')
  })

  it('creates a catalog', async () => {
    await createExternalCatalog({
      name: 'lake',
      type: 'iceberg',
      metastore_type: 'hms',
      metastore_uri: 'thrift://hms:9083',
      storage_connection: 'production',
    })

    expect(requestedUrl()).toBe('/api/v1/external-catalogs')
    expect(requestedMethod()).toBe('POST')
    expect(requestedBody().name).toBe('lake')
    // No credential field may ever be sent from the client.
    expect(requestedBody()).not.toHaveProperty('access_key')
    expect(requestedBody()).not.toHaveProperty('secret_key')
  })

  it('alters a catalog', async () => {
    await alterExternalCatalog('lake', { comment: 'updated' })

    expect(requestedUrl()).toBe('/api/v1/external-catalogs/lake')
    expect(requestedMethod()).toBe('PATCH')
  })

  it('drops a catalog', async () => {
    await deleteExternalCatalog('lake')

    expect(requestedUrl()).toBe('/api/v1/external-catalogs/lake')
    expect(requestedMethod()).toBe('DELETE')
  })

  it('lists tables in a catalog database', async () => {
    await fetchCatalogTables('lake', 'sales')

    expect(requestedUrl()).toBe(
      '/api/v1/external-catalogs/lake/databases/sales/tables'
    )
  })
})
