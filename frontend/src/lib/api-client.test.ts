import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/auth-store'
import { ApiError, api, signInNavigation, signInUrl, tokenSecondsLeft } from './api-client'

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
  fetchMock.mockResolvedValue(jsonResponse({ ok: true }))
})

afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
})

function requestedUrl() {
  return fetchMock.mock.calls[0][0] as string
}

describe('api.patch', () => {
  it('prefixes the path with the API base exactly once', async () => {
    await api.patch('/pipes/my_pipe/suspend')

    expect(requestedUrl()).toBe('/api/v1/pipes/my_pipe/suspend')
  })

  it('issues a PATCH request', async () => {
    await api.patch('/pipes/my_pipe/suspend')

    expect(fetchMock.mock.calls[0][1].method).toBe('PATCH')
  })

  it('serialises a JSON body when one is supplied', async () => {
    await api.patch('/example', { state: 'ACTIVE' })

    expect(fetchMock.mock.calls[0][1].body).toBe(
      JSON.stringify({ state: 'ACTIVE' })
    )
  })

  it('sends no body when none is supplied', async () => {
    await api.patch('/example')

    expect(fetchMock.mock.calls[0][1].body).toBeUndefined()
  })

  it('returns the unwrapped response body', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: 'SUSPENDED' }))

    await expect(api.patch('/example')).resolves.toEqual({
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

    await expect(api.patch('/example')).rejects.toThrow(
      'Pipe is already suspended'
    )
  })

  it('exposes the HTTP status on the thrown error', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ detail: 'Thread not found' }),
    })

    const error = await api.delete('/example').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })
})

// Every caller passes a path relative to API_BASE. A caller repeating the
// /api/v1 prefix produces /api/v1/api/v1/... which the backend answers with
// 404, so assert the single-prefix shape for all five verbs.
describe('API base prefixing', () => {
  it.each([
    ['get', () => api.get('/pipes')],
    ['post', () => api.post('/pipes', {})],
    ['put', () => api.put('/pipes', {})],
    ['patch', () => api.patch('/pipes')],
    ['delete', () => api.delete('/pipes')],
  ])('builds the %s URL from a single base prefix', async (_method, call) => {
    await call()

    expect(requestedUrl()).toBe('/api/v1/pipes')
  })

  it('does not double the prefix for a nested path', async () => {
    await api.patch('/tasks/my_task/resume')

    expect(requestedUrl()).toBe('/api/v1/tasks/my_task/resume')
    expect(requestedUrl()).not.toContain('/api/v1/api/v1')
  })
})

describe('errorDetail', () => {
  it('reads a validation error list instead of printing objects', async () => {
    const { errorDetail } = await import('./api-client')

    expect(
      errorDetail([
        { loc: ['body', 'title'], msg: 'String should have at most 256 characters', type: 'string_too_long' },
      ])
    ).toBe('String should have at most 256 characters')
    expect(errorDetail('Record unavailable')).toBe('Record unavailable')
    expect(errorDetail({ message: 'Denied' })).toBe('Denied')
    expect(errorDetail(undefined)).toBe('Request failed')
    expect(errorDetail([{}], 'Upload failed')).toBe('Upload failed')
  })
})

describe('signInUrl', () => {
  it('returns to the page the session expired on', () => {
    expect(
      signInUrl({ pathname: '/studio', search: '?agent=a&thread=t', hash: '' }),
    ).toBe('/sign-in?redirect=%2Fstudio%3Fagent%3Da%26thread%3Dt')
    expect(signInUrl({ pathname: '/', search: '', hash: '' })).toBe('/sign-in')
    expect(signInUrl({ pathname: '/sign-in', search: '?redirect=x', hash: '' })).toBeNull()
  })
})

function jwt(exp: number) {
  const body = btoa(JSON.stringify({ sub: 'alice', sid: 's1', exp })).replace(/=+$/, '')
  return `h.${body}.sig`
}

describe('token renewal', () => {
  afterEach(() => {
    useAuthStore.getState().auth.reset()
  })

  it('reads the seconds a token has left', () => {
    expect(tokenSecondsLeft(jwt(1_000), 400_000)).toBe(600)
    expect(tokenSecondsLeft('not-a-token')).toBeNull()
  })

  it('renews an expired token once and retries the request', async () => {
    useAuthStore.getState().auth.setAccessToken('old')
    fetchMock
      .mockResolvedValueOnce({ ok: false, status: 401, headers: new Headers(), json: async () => ({}) })
      .mockResolvedValueOnce(jsonResponse({ access_token: 'fresh' }))
      .mockResolvedValueOnce(jsonResponse({ value: 1 }))

    await expect(api.get('/thing')).resolves.toEqual({ value: 1 })

    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/auth/renew')
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer old')
    expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe('Bearer fresh')
    expect(useAuthStore.getState().auth.accessToken).toBe('fresh')
  })

  it('signs in again when the session has ended', async () => {
    const open = vi.spyOn(signInNavigation, 'open').mockImplementation(() => {})
    useAuthStore.getState().auth.setAccessToken('old')
    const unauthorized = { ok: false, status: 401, headers: new Headers(), json: async () => ({}) }
    fetchMock.mockResolvedValueOnce(unauthorized).mockResolvedValueOnce(unauthorized)

    await expect(api.get('/thing')).rejects.toThrow('Session expired')

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(useAuthStore.getState().auth.accessToken).toBe('')
    expect(open).toHaveBeenCalledOnce()
    expect(open.mock.calls[0][1]).toMatch(/^\/sign-in/)
    open.mockRestore()
  })
})
