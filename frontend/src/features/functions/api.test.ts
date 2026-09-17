import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createUDF, deleteUDF, fetchBuiltinFunctions, fetchUDFs } from './api'

const fetchMock = vi.fn()

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
  }
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValue(jsonResponse({}))
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

// The client owns the /api/v1 prefix. These assertions pin the URLs the
// functions page actually sends, so a caller that repeats the prefix or keeps
// the removed /functions/builtin path fails here instead of 404ing at runtime.
describe('functions API calls', () => {
  it('lists built-in functions from the module root', async () => {
    await fetchBuiltinFunctions()

    expect(requestedUrl()).toBe('/api/v1/functions')
  })

  it('does not call the removed /functions/builtin path', async () => {
    await fetchBuiltinFunctions()

    expect(requestedUrl()).not.toContain('/builtin')
  })

  it('lists user-defined functions', async () => {
    await fetchUDFs()

    expect(requestedUrl()).toBe('/api/v1/functions/udf')
  })

  it('creates a user-defined function', async () => {
    await createUDF({
      name: 'my_func',
      database: 'db',
      function_type: 'sql',
      args: 'x INTEGER',
      return_type: 'VARCHAR',
      body: 'SELECT x',
    })

    expect(requestedUrl()).toBe('/api/v1/functions/udf')
    expect(requestedMethod()).toBe('POST')
  })

  it('deletes a user-defined function by database and name', async () => {
    await deleteUDF({ database: 'my_db', name: 'my_func' })

    expect(requestedUrl()).toBe('/api/v1/functions/udf/my_db/my_func')
    expect(requestedMethod()).toBe('DELETE')
  })

  it('encodes path segments in the delete URL', async () => {
    await deleteUDF({ database: 'db name', name: 'fn/x' })

    expect(requestedUrl()).toBe('/api/v1/functions/udf/db%20name/fn%2Fx')
  })
})

describe('functions response shapes', () => {
  it('keeps the category count the backend sends', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        functions: [
          {
            name: 'ABS',
            category: 'Math',
            signature: 'ABS(x)',
            return_type: 'DOUBLE',
            description: 'absolute value',
          },
        ],
        categories: [{ name: 'Math', count: 12 }],
        count: 1,
      })
    )

    const res = await fetchBuiltinFunctions()

    expect(res.categories).toEqual([{ name: 'Math', count: 12 }])
    expect(res.functions[0].category).toBe('Math')
  })

  it('reads the databases field the backend returns for the UDF filter', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        functions: [
          {
            name: 'f',
            database: 'analytics',
            function_type: 'SQL',
            scope: 'database',
            args: 'x INTEGER',
            return_type: 'VARCHAR',
            body: null,
          },
        ],
        databases: ['analytics'],
        count: 1,
      })
    )

    const res = await fetchUDFs()

    expect(res.databases).toEqual(['analytics'])
  })

  it('degrades to an empty database list when the field is absent', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ functions: [], count: 0 })
    )

    const res = await fetchUDFs()

    expect(res.databases).toEqual([])
  })
})
