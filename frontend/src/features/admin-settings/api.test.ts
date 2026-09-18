import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  PASSWORD_POLICY_DEFAULTS,
  fetchPasswordPolicy,
  fetchVariables,
  isVariableChanged,
  parsePolicyNumber,
  updatePasswordPolicy,
  updateVariable,
  type Variable,
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

describe('admin settings API calls', () => {
  it('lists session variables with scope and pagination', async () => {
    await fetchVariables({ scope: 'session', limit: 25, offset: 25 })

    expect(requestedUrl()).toBe('/api/v1/variables?scope=session&limit=25&offset=25')
    expect(requestedUrl()).not.toContain('/api/v1/api/v1')
  })

  it('adds a search term when one is given', async () => {
    await fetchVariables({ scope: 'global', search: 'timeout' })

    expect(requestedUrl()).toBe(
      '/api/v1/variables?scope=global&limit=50&offset=0&search=timeout'
    )
  })

  it('sets a variable for the active scope', async () => {
    await updateVariable('query_timeout', 'session', '600')

    expect(requestedUrl()).toBe('/api/v1/variables/query_timeout')
    expect(requestedMethod()).toBe('PUT')
    expect(requestedBody()).toEqual({ scope: 'session', value: '600' })
  })

  it('encodes variable names in the path', async () => {
    await updateVariable('a b', 'global', '1')

    expect(requestedUrl()).toBe('/api/v1/variables/a%20b')
  })

  it('reads the password policy', async () => {
    await fetchPasswordPolicy()

    expect(requestedUrl()).toBe('/api/v1/variables/password-policy')
    expect(requestedMethod()).toBe('GET')
  })

  it('saves the password policy', async () => {
    await updatePasswordPolicy(PASSWORD_POLICY_DEFAULTS)

    expect(requestedUrl()).toBe('/api/v1/variables/password-policy')
    expect(requestedMethod()).toBe('PUT')
    expect(requestedBody().password_lifetime).toBe(90)
  })
})

describe('variable change detection', () => {
  const base: Variable = {
    name: 'query_timeout',
    value: '600',
    default_value: '300',
    scope: 'session',
    description: null,
  }

  it('prefers an explicit changed flag', () => {
    expect(isVariableChanged({ ...base, changed: false })).toBe(false)
    expect(isVariableChanged({ ...base, changed: true })).toBe(true)
  })

  it('falls back to comparing value with default', () => {
    expect(isVariableChanged(base)).toBe(true)
    expect(isVariableChanged({ ...base, value: '300' })).toBe(false)
  })

  it('does not treat an unknown default as changed', () => {
    expect(isVariableChanged({ ...base, default_value: null })).toBe(false)
  })
})

describe('policy number parsing', () => {
  it('parses a non-negative integer', () => {
    expect(parsePolicyNumber('30')).toBe(30)
  })

  it('treats a blank field as unset, not zero', () => {
    expect(parsePolicyNumber('   ')).toBeNull()
  })

  it('rejects negatives and non-numeric input', () => {
    expect(parsePolicyNumber('-1')).toBeNull()
    expect(parsePolicyNumber('abc')).toBeNull()
  })
})
