import { create } from 'zustand'
import { getCookie, setCookie, removeCookie } from '@/lib/cookies'

const TOKEN_KEY = 'nova_access_token'

export interface AuthUser {
  username: string
  roles: string[]
  activeRole?: string | null
  securityContextVersion?: number
}

interface AuthState {
  securityEpoch: number
  auth: {
    user: AuthUser | null
    setUser: (user: AuthUser | null) => void
    accessToken: string
    setAccessToken: (accessToken: string) => void
    resetAccessToken: () => void
    reset: () => void
  }
}

/** Whether two users carry the same principal, roles, active role and context version. */
function sameIdentity(a: AuthUser | null, b: AuthUser | null): boolean {
  if (!a || !b) return false
  return a.username === b.username
    && (a.activeRole ?? null) === (b.activeRole ?? null)
    && (a.securityContextVersion ?? null) === (b.securityContextVersion ?? null)
    && a.roles.length === b.roles.length
    && a.roles.every((role, index) => role === b.roles[index])
}

export const useAuthStore = create<AuthState>()((set) => {
  const stored = getCookie(TOKEN_KEY)
  const initToken = stored || ''
  return {
    securityEpoch: 0,
    auth: {
      user: null,
      setUser: (user) =>
        set((state) => ({
          ...state,
          // A repeat of the same identity (a second /auth/me answer) keeps the
          // epoch, so epoch-keyed queries are not fetched again for nothing.
          securityEpoch: sameIdentity(state.auth.user, user) ? state.securityEpoch : state.securityEpoch + 1,
          auth: { ...state.auth, user },
        })),
      accessToken: initToken,
      setAccessToken: (accessToken) =>
        set((state) => {
          setCookie(TOKEN_KEY, accessToken)
          const epoch = accessToken === state.auth.accessToken ? state.securityEpoch : state.securityEpoch + 1
          return { ...state, securityEpoch: epoch, auth: { ...state.auth, accessToken } }
        }),
      resetAccessToken: () =>
        set((state) => {
          removeCookie(TOKEN_KEY)
          return { ...state, securityEpoch: state.securityEpoch + 1, auth: { ...state.auth, accessToken: '' } }
        }),
      reset: () =>
        set((state) => {
          removeCookie(TOKEN_KEY)
          return {
            ...state,
            securityEpoch: state.securityEpoch + 1,
            auth: { ...state.auth, user: null, accessToken: '' },
          }
        }),
    },
  }
})
