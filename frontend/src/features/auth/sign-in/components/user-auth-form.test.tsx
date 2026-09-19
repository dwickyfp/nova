import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, type RenderResult } from 'vitest-browser-react'
import { type Locator, userEvent } from 'vitest/browser'
import { UserAuthForm } from './user-auth-form'

const navigate = vi.fn()
const setUserMock = vi.fn()
const setAccessTokenMock = vi.fn()
const fetchMock = vi.fn()

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: () => ({
    auth: {
      setUser: setUserMock,
      setAccessToken: setAccessTokenMock,
    },
  }),
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => navigate,
  }
})

function mockAuthenticatedResponse() {
  fetchMock.mockResolvedValue({
    status: 200,
    ok: true,
    json: async () => ({
      status: 'AUTHENTICATED',
      access_token: 'mock-access-token',
      user: 'analyst',
      roles: ['analyst'],
    }),
  } as Response)
}

describe('UserAuthForm', () => {
  let screen: RenderResult
  let usernameInput: Locator
  let passwordInput: Locator
  let signInButton: Locator

  beforeEach(async () => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
    mockAuthenticatedResponse()

    screen = await render(<UserAuthForm />)
    usernameInput = screen.getByRole('textbox', { name: /^Username$/i })
    passwordInput = screen.getByLabelText(/^Password$/i)
    signInButton = screen.getByRole('button', { name: /^Sign in$/i })
  })

  it('renders credential fields and submit button', async () => {
    await expect.element(usernameInput).toBeInTheDocument()
    await expect.element(passwordInput).toBeInTheDocument()
    await expect.element(signInButton).toBeInTheDocument()
  })

  it('shows validation messages when credentials are empty', async () => {
    await userEvent.click(signInButton)

    await expect
      .element(screen.getByText('Please enter your username.'))
      .toBeInTheDocument()
    await expect
      .element(screen.getByText('Please enter your password.'))
      .toBeInTheDocument()
  })

  it('authenticates and navigates to the default route', async () => {
    await userEvent.fill(usernameInput, 'analyst')
    await userEvent.fill(passwordInput, 'secret')
    await userEvent.click(signInButton)

    await vi.waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: 'analyst',
          password: 'secret',
        }),
      })
    )
    expect(setAccessTokenMock).toHaveBeenCalledWith('mock-access-token')
    expect(setUserMock).toHaveBeenCalledWith({
      username: 'analyst',
      roles: ['analyst'],
      // The login response omits active_role, so getAuthUser falls back to the first role.
      activeRole: 'analyst',
    })
    expect(navigate).toHaveBeenCalledWith({ to: '/', replace: true })
  })

  it('shows the forced change-password form when the admin requires it', async () => {
    fetchMock.mockReset()
    fetchMock.mockResolvedValueOnce({
      status: 200,
      ok: true,
      json: async () => ({
        status: 'PASSWORD_CHANGE_REQUIRED',
        access_token: 'forced-token',
        user: 'dwicky',
        roles: ['public'],
      }),
    } as Response)

    await userEvent.fill(usernameInput, 'dwicky')
    await userEvent.fill(passwordInput, 'generated-pass')
    await userEvent.click(signInButton)

    await expect
      .element(
        screen.getByText('Your administrator requires a new password before first use.')
      )
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: /Change Password & Continue/i }))
      .toBeInTheDocument()
  })

  it('changes the password and logs in with the new one', async () => {
    fetchMock.mockReset()
    // 1) login -> forced; 2) change-password; 3) login with new password.
    fetchMock
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({
          status: 'PASSWORD_CHANGE_REQUIRED',
          access_token: 'forced-token',
          user: 'dwicky',
          roles: ['public'],
        }),
      } as Response)
      .mockResolvedValueOnce({ status: 200, ok: true, json: async () => ({}) } as Response)
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({
          status: 'AUTHENTICATED',
          access_token: 'new-token',
          user: 'dwicky',
          roles: ['public'],
        }),
      } as Response)

    await userEvent.fill(usernameInput, 'dwicky')
    await userEvent.fill(passwordInput, 'generated-pass')
    await userEvent.click(signInButton)

    await userEvent.fill(screen.getByLabelText(/^New Password$/i), 'NewPass123!')
    await userEvent.fill(screen.getByLabelText(/^Confirm Password$/i), 'NewPass123!')
    await userEvent.click(screen.getByRole('button', { name: /Change Password & Continue/i }))

    await vi.waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/auth/change-password',
        expect.objectContaining({ method: 'POST' })
      )
    )
    await vi.waitFor(() => expect(setAccessTokenMock).toHaveBeenCalledWith('new-token'))
  })

  it('navigates to a safe internal redirect path', async () => {
    // Render into a fresh container: vitest only cleans up between tests, so the form
    // mounted by beforeEach would otherwise still be in the document and every locator
    // would resolve to two elements.
    await cleanup()

    screen = await render(<UserAuthForm redirectTo='/users?tab=account' />)
    usernameInput = screen.getByRole('textbox', { name: /^Username$/i })
    passwordInput = screen.getByLabelText(/^Password$/i)
    signInButton = screen.getByRole('button', { name: /^Sign in$/i })

    await userEvent.fill(usernameInput, 'analyst')
    await userEvent.fill(passwordInput, 'secret')
    await userEvent.click(signInButton)

    await vi.waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/users?tab=account',
        replace: true,
      })
    )
  })
})
