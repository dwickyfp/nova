import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createUDF, deleteUDF, fetchBuiltinFunctions, fetchCreatableDatabases, fetchUDFs, parseUDFArgs } from './api'

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
    // The service takes named, typed arguments, not the dialog's text.
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body.args).toEqual([{ name: 'x', type: 'INTEGER' }])
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

describe('databases for a new function', () => {
  it('offers every database the active role can use, not only those holding a function', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ roles: [], databases: ['sales', 'empty_db'], schemas: [] }))

    const databases = await fetchCreatableDatabases()

    expect(requestedUrl()).toBe('/api/v1/query/context')
    expect(databases).toEqual(['sales', 'empty_db'])
  })

  it('returns no choices when the context has none', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ roles: [] }))

    expect(await fetchCreatableDatabases()).toEqual([])
  })
})

describe('function arguments', () => {
  it('keeps commas inside a type with the type', () => {
    expect(parseUDFArgs('x INT, amount DECIMAL(10,2) , label VARCHAR')).toEqual([
      { name: 'x', type: 'INT' },
      { name: 'amount', type: 'DECIMAL(10,2)' },
      { name: 'label', type: 'VARCHAR' },
    ])
  })

  it('allows a function without arguments', () => {
    expect(parseUDFArgs('  ')).toEqual([])
  })

  it('refuses an argument without a type before anything is sent', async () => {
    expect(() => parseUDFArgs('x')).toThrow(/name and a type/)
    await expect(createUDF({
      name: 'f', database: 'db', function_type: 'sql', args: 'x', return_type: 'INT', body: 'x',
    })).rejects.toThrow(/name and a type/)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
