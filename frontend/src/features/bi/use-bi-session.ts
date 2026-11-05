import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'

export type BiSessionStatus = 'opening' | 'ready' | 'error'

/** Nova's theme setting; `system` follows the OS in both apps. */
export type BiThemeSetting = 'light' | 'dark' | 'system'

/** The theme key Superset reads; same origin, so Nova can set it for a frame. */
const SUPERSET_THEME_KEY = 'superset-theme-mode'

/**
 * A BI handle lives as long as Superset uses it. A frame mounted after this
 * long opens a fresh one, so a handle left idle while Nova stayed in use never
 * signs a new frame in.
 */
const BI_SESSION_REUSE_MS = 60_000

/** Make a framed Superset follow Nova's light, dark or system mode. */
export function syncBiTheme(theme: BiThemeSetting) {
  const mode = theme === 'dark' ? 'dark' : theme === 'system' ? 'system' : 'default'
  try {
    localStorage.setItem(SUPERSET_THEME_KEY, mode)
  } catch {
    // Storage can be unavailable; Superset then follows the system setting.
  }
}

/**
 * Open the HttpOnly `/bi` session before a Superset frame loads, so the frame
 * is signed in as the current Nova session and active role. Frames on one
 * page share the session; a new sign-in or role opens another.
 */
export function useBiSession() {
  const securityEpoch = useAuthStore((state) => state.securityEpoch)
  const session = useQuery({
    queryKey: ['bi', 'session', securityEpoch],
    queryFn: ({ signal }) => api.post('/superset/session', undefined, signal),
    staleTime: BI_SESSION_REUSE_MS,
    refetchOnWindowFocus: false,
    retry: false,
  })

  const status: BiSessionStatus = session.isSuccess
    ? 'ready'
    : session.isError
      ? 'error'
      : 'opening'
  const error =
    session.error instanceof ApiError
      ? session.error.message
      : session.error
        ? 'This view could not be opened'
        : ''
  return { status, error, retry: () => void session.refetch() }
}
