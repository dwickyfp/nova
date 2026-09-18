import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  PASSWORD_POLICY_VARIABLES,
  fetchPasswordPolicy,
  fetchVariables,
  isVariableChanged,
  parsePasswordPolicy,
  parsePolicyBoolean,
  parsePolicyNumber,
  setVariable,
  updatePasswordPolicy,
  type VariableItem,
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

describe('admin settings API calls match the merged variables router', () => {
  it('lists variables from GET /variables with scope and pagination', async () => {
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

  it('sets a variable through POST /variables/set, not PUT /variables/:name', async () => {
    await setVariable({ scope: 'session', name: 'query_timeout', value: '600' })

    expect(requestedUrl()).toBe('/api/v1/variables/set')
    expect(requestedMethod()).toBe('POST')
    expect(requestedBody()).toEqual({
      scope: 'session',
      name: 'query_timeout',
      value: '600',
    })
  })

  it('sends only reset:true when resetting, with no value', async () => {
    await setVariable({ scope: 'global', name: 'query_timeout', reset: true })

    expect(requestedBody()).toEqual({
      scope: 'global',
      name: 'query_timeout',
      reset: true,
    })
    expect(requestedBody().value).toBeUndefined()
  })

  it('reads the policy from GET /variables/password-policy', async () => {
    await fetchPasswordPolicy()

    expect(requestedUrl()).toBe('/api/v1/variables/password-policy')
    expect(requestedMethod()).toBe('GET')
  })

  it('writes the policy through POST /variables/password-policy', async () => {
    await updatePasswordPolicy({ password_lifetime: 90, validate_password: true })

    expect(requestedUrl()).toBe('/api/v1/variables/password-policy')
    expect(requestedMethod()).toBe('POST')
    expect(requestedBody()).toEqual({
      password_lifetime: 90,
      validate_password: true,
    })
  })
})

describe('password policy unwrapping', () => {
  it('unwraps the {policy: ...} envelope and fills every documented field', () => {
    const policy = parsePasswordPolicy({
      password_lifetime: '90',
      validate_password: 'false',
    })

    expect(policy.password_lifetime).toBe('90')
    expect(policy.validate_password).toBe('false')
    expect(policy.password_history).toBe('')
    for (const name of PASSWORD_POLICY_VARIABLES) {
      expect(policy[name]).toBeDefined()
    }
  })

  it('handles a missing envelope without throwing', () => {
    const policy = parsePasswordPolicy(undefined)

    expect(policy.password_lifetime).toBe('')
  })
})

describe('variable change detection', () => {
  const base: VariableItem = {
    name: 'query_timeout',
    value: '600',
    default: '300',
    scope: 'session',
  }

  it('marks a value that differs from the engine default', () => {
    expect(isVariableChanged(base)).toBe(true)
  })

  it('does not mark a value equal to the default', () => {
    expect(isVariableChanged({ ...base, value: '300' })).toBe(false)
  })

  it('does not treat an unknown default as changed', () => {
    expect(isVariableChanged({ ...base, default: null })).toBe(false)
  })
})

describe('policy value parsing', () => {
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

  it('parses the engine boolean strings', () => {
    expect(parsePolicyBoolean('true')).toBe(true)
    expect(parsePolicyBoolean('ON')).toBe(true)
    expect(parsePolicyBoolean('false')).toBe(false)
  })
})
