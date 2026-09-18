import { useAuthStore } from '@/stores/auth-store'

const API_BASE = '/api/v1'

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
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, options: RequestInit & { signal?: AbortSignal } = {}): Promise<T> {
  const token = useAuthStore.getState().auth.accessToken
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    signal: options.signal,
  })
  if (res.status === 401) {
    useAuthStore.getState().auth.reset()
    window.location.href = '/sign-in'
    throw new Error('Session expired')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, err.detail || 'Request failed')
  }

  if (res.status === 204) {
    return undefined as T
  }

  const contentType = res.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    const text = await res.text()
    return (text ? text : undefined) as T
  }

  return res.json()
}

export const api = {
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
        useAuthStore.getState().auth.reset()
        window.location.href = '/sign-in'
        throw new Error('Session expired')
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail || 'Upload failed')
      }
      return res.json() as Promise<T>
    })
  },
}
