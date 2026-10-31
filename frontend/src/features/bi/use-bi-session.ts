import { useEffect, useState } from 'react'
import { api, ApiError } from '@/lib/api-client'

export type BiSessionStatus = 'opening' | 'ready' | 'error'

/** The theme key Superset reads; same origin, so Nova can set it for a frame. */
const SUPERSET_THEME_KEY = 'superset-theme-mode'

/** Make a framed Superset follow Nova's light or dark mode. */
export function syncBiTheme(theme: 'light' | 'dark') {
  try {
    localStorage.setItem(SUPERSET_THEME_KEY, theme === 'dark' ? 'dark' : 'default')
  } catch {
    // Storage can be unavailable; Superset then follows the system setting.
  }
}

/**
 * Open the HttpOnly `/bi` session before a Superset frame loads, so the frame
 * is signed in as the current Nova session and active role.
 */
export function useBiSession() {
  const [status, setStatus] = useState<BiSessionStatus>('opening')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setStatus('opening')
    api
      .post('/superset/session', undefined, controller.signal)
      .then(() => setStatus('ready'))
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(cause instanceof ApiError ? cause.message : 'BI could not be opened')
        setStatus('error')
      })
    return () => controller.abort()
  }, [attempt])

  return { status, error, retry: () => setAttempt((value) => value + 1) }
}
