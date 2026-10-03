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

export const useAuthStore = create<AuthState>()((set) => {
  const stored = getCookie(TOKEN_KEY)
  const initToken = stored || ''
  return {
    securityEpoch: 0,
    auth: {
      user: null,
      setUser: (user) =>
        set((state) => ({ ...state, securityEpoch: state.securityEpoch + 1, auth: { ...state.auth, user } })),
      accessToken: initToken,
      setAccessToken: (accessToken) =>
        set((state) => {
          setCookie(TOKEN_KEY, accessToken)
          return { ...state, securityEpoch: state.securityEpoch + 1, auth: { ...state.auth, accessToken } }
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
