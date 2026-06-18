import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
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

  it('renders StarRocks credential fields and submit button', async () => {
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
    })
    expect(navigate).toHaveBeenCalledWith({ to: '/', replace: true })
  })

  it('navigates to a safe internal redirect path', async () => {
    screen = await render(<UserAuthForm redirectTo='/settings?tab=account' />)
    usernameInput = screen.getByRole('textbox', { name: /^Username$/i })
    passwordInput = screen.getByLabelText(/^Password$/i)
    signInButton = screen.getByRole('button', { name: /^Sign in$/i })

    await userEvent.fill(usernameInput, 'analyst')
    await userEvent.fill(passwordInput, 'secret')
    await userEvent.click(signInButton)

    await vi.waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/settings?tab=account',
        replace: true,
      })
    )
  })
})
