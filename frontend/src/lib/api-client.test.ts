import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api-client'

const fetchMock = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ ok: true }),
  })
})

afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
})

describe('api.patch', () => {
  it('issues a PATCH request to the API base path', async () => {
    await api.patch('/api/v1/pipes/my_pipe/suspend')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/api/v1/pipes/my_pipe/suspend')
    expect(init.method).toBe('PATCH')
  })

  it('serialises a JSON body when one is supplied', async () => {
    await api.patch('/api/v1/example', { state: 'ACTIVE' })

    const [, init] = fetchMock.mock.calls[0]
    expect(init.body).toBe(JSON.stringify({ state: 'ACTIVE' }))
  })

  it('sends no body when none is supplied', async () => {
    await api.patch('/api/v1/example')

    const [, init] = fetchMock.mock.calls[0]
    expect(init.body).toBeUndefined()
  })

  it('returns the unwrapped response body', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ status: 'SUSPENDED' }),
    })

    await expect(api.patch('/api/v1/example')).resolves.toEqual({
      status: 'SUSPENDED',
    })
  })

  it('surfaces the backend detail message on a failed request', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ detail: 'Pipe is already suspended' }),
    })

    await expect(api.patch('/api/v1/example')).rejects.toThrow(
      'Pipe is already suspended'
    )
  })
})
