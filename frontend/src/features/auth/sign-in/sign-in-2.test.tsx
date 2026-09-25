import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page } from 'vitest/browser'
import '@/styles/index.css'
import { SignIn2 } from './sign-in-2'

vi.mock('@tanstack/react-router', () => ({ useSearch: () => ({ redirect: undefined }) }))
vi.mock('./components/user-auth-form', () => ({
  UserAuthForm: () => <div>{Array.from({ length: 30 }, (_, index) => <p key={index}>Form row {index + 1}</p>)}</div>,
}))
vi.mock('./components/sign-in-visual', () => ({ SignInVisual: () => null }))

describe('Sign in layout', () => {
  it('keeps a long form inside the viewport', async () => {
    await page.viewport(320, 480)
    try {
      const screen = await render(<SignIn2 />)
      const main = screen.getByRole('main').element()
      const frame = main.parentElement!
      await expect.element(screen.getByRole('heading', { name: 'Sign in to Nova' })).toBeVisible()
      expect(main.scrollHeight).toBeGreaterThan(main.clientHeight)
      expect(frame.scrollHeight).toBeLessThanOrEqual(frame.clientHeight)
    } finally {
      await page.viewport(1280, 720)
    }
  })
})
