import { clearCookies } from '@/test-utils/cookies'
import { beforeEach, describe, expect, it, vi } from 'vitest'

async function importAuthStore() {
  const { useAuthStore } = await import('./auth-store')
  return useAuthStore
}

const sampleUser = {
  username: 'analyst',
  roles: ['analyst'],
}

describe('useAuthStore', () => {
  beforeEach(() => {
    clearCookies()
    vi.resetModules()
  })

  it('starts with an empty access token when nothing is persisted', async () => {
    const useAuthStore = await importAuthStore()

    expect(useAuthStore.getState().auth.accessToken).toBe('')
    expect(useAuthStore.getState().auth.user).toBeNull()
  })

  it('persists access token so a new store instance reads it back', async () => {
    const useAuthStore = await importAuthStore()
    useAuthStore.getState().auth.setAccessToken('session-token')

    vi.resetModules()
    const useAuthStoreAfterReload = await importAuthStore()

    expect(useAuthStoreAfterReload.getState().auth.accessToken).toBe(
      'session-token'
    )
  })

  it('clears persisted access token when resetAccessToken is used', async () => {
    const useAuthStore = await importAuthStore()
    useAuthStore.getState().auth.setAccessToken('to-clear')
    useAuthStore.getState().auth.resetAccessToken()

    vi.resetModules()
    const useAuthStoreAfterReload = await importAuthStore()

    expect(useAuthStoreAfterReload.getState().auth.accessToken).toBe('')
  })

  it('updates the signed-in user via setUser', async () => {
    const useAuthStore = await importAuthStore()

    useAuthStore.getState().auth.setUser({ ...sampleUser })

    expect(useAuthStore.getState().auth.user).toEqual(sampleUser)
  })

  it('changes the security epoch only when the identity changes', async () => {
    const useAuthStore = await importAuthStore()
    const epoch = () => useAuthStore.getState().securityEpoch

    useAuthStore.getState().auth.reset()
    const before = epoch()
    useAuthStore.getState().auth.setUser({ ...sampleUser, activeRole: 'analyst' })
    const signedIn = epoch()
    expect(signedIn).toBe(before + 1)
    useAuthStore.getState().auth.setUser({ ...sampleUser, activeRole: 'analyst' })
    expect(epoch()).toBe(signedIn)

    useAuthStore.getState().auth.setUser({ ...sampleUser, activeRole: 'admin' })
    expect(epoch()).toBe(signedIn + 1)
    useAuthStore.getState().auth.setUser({ ...sampleUser, activeRole: 'admin', securityContextVersion: 2 })
    expect(epoch()).toBe(signedIn + 2)

    useAuthStore.getState().auth.setAccessToken('token-a')
    const withToken = epoch()
    useAuthStore.getState().auth.setAccessToken('token-a')
    expect(epoch()).toBe(withToken)
    useAuthStore.getState().auth.setAccessToken('token-b')
    expect(epoch()).toBe(withToken + 1)

    useAuthStore.getState().auth.setUser(null)
    expect(epoch()).toBe(withToken + 2)
  })

  it('reset clears user and access token and drops persistence', async () => {
    const useAuthStore = await importAuthStore()
    useAuthStore.getState().auth.setAccessToken('will-be-cleared')
    useAuthStore.getState().auth.setUser({ ...sampleUser })

    useAuthStore.getState().auth.reset()

    expect(useAuthStore.getState().auth.user).toBeNull()
    expect(useAuthStore.getState().auth.accessToken).toBe('')

    vi.resetModules()
    const useAuthStoreAfterReload = await importAuthStore()

    expect(useAuthStoreAfterReload.getState().auth.user).toBeNull()
    expect(useAuthStoreAfterReload.getState().auth.accessToken).toBe('')
  })
})
