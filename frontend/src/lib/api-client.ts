import { useAuthStore } from '@/stores/auth-store'

const API_BASE = '/api/v1'

/** Where an expired session goes: sign-in, then back to the page it was on. */
export function signInUrl(location: Pick<Location, 'pathname' | 'search' | 'hash'>): string | null {
  if (location.pathname === '/sign-in') return null
  const back = `${location.pathname}${location.search}${location.hash}`
  return back === '/' ? '/sign-in' : `/sign-in?redirect=${encodeURIComponent(back)}`
}

/** How the sign-in page is opened; replaced in tests, which must not navigate. */
export const signInNavigation = {
  open(target: Window, url: string): void {
    target.location.href = url
  },
}

function signInAgain(): void {
  useAuthStore.getState().auth.reset()
  const target = window
  const url = signInUrl(target.location)
  if (url) signInNavigation.open(target, url)
}

/** Seconds until the token's expiry, or null when it carries none. */
export function tokenSecondsLeft(token: string, now = Date.now()): number | null {
  try {
    const body = token.split('.')[1]
    if (!body) return null
    const { exp } = JSON.parse(atob(body.replace(/-/g, '+').replace(/_/g, '/'))) as { exp?: number }
    return typeof exp === 'number' ? exp - now / 1000 : null
  } catch {
    return null
  }
}

let renewing: Promise<boolean> | null = null

/**
 * Exchange the current token for a fresh one while the Nova session is still
 * alive (activity in Nova or in Dataviz keeps it alive). Renewal itself is not
 * activity: an idle session still ends, and then this resolves false.
 */
export function renewAccessToken(): Promise<boolean> {
  const initial = useAuthStore.getState()
  const token = initial.auth.accessToken
  if (!token) return Promise.resolve(false)
  renewing ??= fetch(`${API_BASE}/auth/renew`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  })
    .then(async (res) => {
      if (!res.ok) return false
      const body = (await res.json()) as { access_token?: string }
      if (!body.access_token) return false
      const current = useAuthStore.getState()
      if (current.securityEpoch !== initial.securityEpoch || current.auth.accessToken !== token) return false
      useAuthStore.getState().auth.setAccessToken(body.access_token)
      return true
    })
    .catch(() => false)
    .finally(() => {
      renewing = null
    })
  return renewing
}

const RENEW_BEFORE_SECONDS = 5 * 60
let renewTimer: ReturnType<typeof setTimeout> | undefined

/** Renew a few minutes before expiry, so streams and BI launches never see it lapse. */
function scheduleRenewal(token: string): void {
  clearTimeout(renewTimer)
  const left = token ? tokenSecondsLeft(token) : null
  if (left === null || typeof window === 'undefined') return
  const wait = Math.max(0, left - RENEW_BEFORE_SECONDS) * 1000
  renewTimer = setTimeout(() => void renewAccessToken(), Math.min(wait, 2 ** 31 - 1))
}

let renewalStarted = false

/** Keep the access token fresh while the app runs; called once from the app entry. */
export function startTokenRenewal(): void {
  if (renewalStarted || typeof window === 'undefined') return
  renewalStarted = true
  scheduleRenewal(useAuthStore.getState().auth.accessToken)
  useAuthStore.subscribe((state, previous) => {
    if (state.auth.accessToken !== previous.auth.accessToken) scheduleRenewal(state.auth.accessToken)
  })
}

export function apiBase() {
  return API_BASE
}

export function authHeaders(extra?: Record<string, string>) {
  const token = useAuthStore.getState().auth.accessToken
  return {
    ...(extra ?? {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
}

/**
 * A non-2xx API response. Callers that need to treat a specific status
 * differently (for example, an idempotent delete whose 404 means "already
 * gone") branch on `status`; everything else reads `message`.
 */
/**
 * A readable message from an error body. Validation errors arrive as a list of
 * objects, which would otherwise print as "[object Object]".
 */
export function errorDetail(detail: unknown, fallback = 'Request failed'): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        typeof item === 'string'
          ? item
          : item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string'
            ? (item as { msg: string }).msg
            : ''
      )
      .filter(Boolean)
    if (messages.length) return messages.join('. ')
  }
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown; msg?: unknown }).message ?? (detail as { msg?: unknown }).msg
    if (typeof message === 'string' && message.trim()) return message
  }
  return fallback
}

export class ApiError extends Error {
  readonly status: number
  /** The parsed response body, for callers whose errors carry structured results. */
  readonly body: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

type RequestOptions = Omit<RequestInit, 'headers'> & {
  headers?: Record<string, string>
  securityEpoch?: number
}

async function request<T>(
  path: string,
  options: RequestOptions = {},
  renewed = false,
): Promise<T> {
  const { securityEpoch, ...fetchOptions } = options
  const assertCurrent = () => {
    if (securityEpoch !== undefined && securityEpoch !== useAuthStore.getState().securityEpoch) {
      throw new DOMException('Security context changed', 'AbortError')
    }
    options.signal?.throwIfAborted()
  }
  assertCurrent()
  const token = useAuthStore.getState().auth.accessToken
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...fetchOptions,
    headers,
    signal: options.signal,
  })
  assertCurrent()
  if (res.status === 401) {
    if (useAuthStore.getState().auth.accessToken !== token) {
      throw new DOMException('Security context changed', 'AbortError')
    }
    if (token && !renewed) {
      const renewedToken = await renewAccessToken()
      assertCurrent()
      if (renewedToken) return request<T>(path, options, true)
      if (useAuthStore.getState().auth.accessToken !== token) {
        throw new DOMException('Security context changed', 'AbortError')
      }
    }
    signInAgain()
    throw new Error('Session expired')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    assertCurrent()
    throw new ApiError(res.status, errorDetail(err?.detail), err)
  }

  if (res.status === 204) {
    return undefined as T
  }

  const contentType = res.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    const text = await res.text()
    assertCurrent()
    return (text ? text : undefined) as T
  }

  const body = await res.json()
  assertCurrent()
  return body
}

export const api = {
  request: <T>(path: string, options: RequestOptions) => request<T>(path, options),
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
      signal,
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PUT',
      body: body ? JSON.stringify(body) : undefined,
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PATCH',
      body: body ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'DELETE',
      body: body ? JSON.stringify(body) : undefined,
    }),
  upload: <T>(path: string, formData: FormData) => {
    const token = useAuthStore.getState().auth.accessToken
    const headers: Record<string, string> = {}
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    // Don't set Content-Type — let browser set multipart boundary
    return fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers,
      body: formData,
    }).then(async (res) => {
      if (res.status === 401) {
        signInAgain()
        throw new Error('Session expired')
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(errorDetail(err.detail, 'Upload failed'))
      }
      return res.json() as Promise<T>
    })
  },
}
