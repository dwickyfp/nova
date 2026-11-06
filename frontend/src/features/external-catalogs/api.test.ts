import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  alterCatalog,
  deleteCatalog,
  fetchCatalogObjects,
  fetchCatalogs,
  fetchSyncRuns,
  refreshCatalogMetadata,
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

describe('Nova Catalog API calls', () => {
  it('lists catalogs', async () => {
    await fetchCatalogs()

    expect(requestedUrl()).toBe('/api/v1/catalog/catalogs')
    expect(requestedMethod()).toBe('GET')
  })

  it('alters a catalog', async () => {
    await alterCatalog('lake', { comment: 'updated' })

    expect(requestedUrl()).toBe('/api/v1/catalog/catalogs/lake')
    expect(requestedMethod()).toBe('PATCH')
  })

  it('drops a catalog', async () => {
    await deleteCatalog('lake')

    expect(requestedUrl()).toBe('/api/v1/catalog/catalogs/lake')
    expect(requestedMethod()).toBe('DELETE')
  })

  it('lists objects in a catalog database', async () => {
    await fetchCatalogObjects('lake', 'sales')

    expect(requestedUrl()).toBe(
      '/api/v1/catalog/catalogs/lake/databases/sales/objects'
    )
  })

  it('refreshes one catalog or every catalog', async () => {
    await refreshCatalogMetadata('lake')
    await refreshCatalogMetadata()

    expect(requestedUrl(0)).toBe('/api/v1/catalog/catalogs/lake/refresh')
    expect(requestedMethod(0)).toBe('POST')
    expect(requestedUrl(1)).toBe('/api/v1/catalog/catalogs/refresh')
  })

  it('lists sync runs', async () => {
    await fetchSyncRuns('lake')

    expect(requestedUrl()).toBe('/api/v1/catalog/catalogs/lake/sync-runs')
  })
})
