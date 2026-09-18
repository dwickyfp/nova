import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { decideToolCall, toConsentPayload } from './stream-client'

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: {
    getState: () => ({ auth: { accessToken: 'test-token', reset: vi.fn() } }),
  },
}))

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockClear()
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ tool_call_id: 'call-1', status: 'approved' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  )
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('toConsentPayload', () => {
  it('maps allow to allow_once, allow + always to allow_session, and deny to deny', () => {
    expect(toConsentPayload('approve', false)).toBe('allow_once')
    expect(toConsentPayload('approve', true)).toBe('allow_session')
    expect(toConsentPayload('deny', false)).toBe('deny')
  })
})

describe('decideToolCall', () => {
  it('posts the tool-call-scoped path with the frozen decision enum', async () => {
    await decideToolCall('call-1', 'approve', false)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/assistant/tool-calls/call-1/decision')
    expect(url).not.toContain('/threads/')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ decision: 'allow_once' })
  })

  it('sends allow_session when the user checked always-allow', async () => {
    await decideToolCall('call-2', 'approve', true)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/assistant/tool-calls/call-2/decision')
    expect(JSON.parse(init.body as string)).toEqual({ decision: 'allow_session' })
  })

  it('sends deny without an always-allow field', async () => {
    await decideToolCall('call-3', 'deny', true)

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(init.body as string)
    expect(body).toEqual({ decision: 'deny' })
    expect(body).not.toHaveProperty('always_allow')
  })

  it('url-encodes the tool call id', async () => {
    await decideToolCall('call/with space', 'approve')

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/assistant/tool-calls/call%2Fwith%20space/decision')
  })

  it('returns the response grant_active flag so the panel can track the grant', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ tool_call_id: 'call-1', status: 'approved', grant_active: true }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )

    const result = await decideToolCall('call-1', 'approve', true)

    expect(result.grant_active).toBe(true)
  })
})
