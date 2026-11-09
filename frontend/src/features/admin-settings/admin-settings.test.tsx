import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import { page } from 'vitest/browser'
import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@/styles/index.css'
import type { SettingsTab } from './api'
import { AdminSettingsPage } from './index'

vi.mock('@/components/layout/header', () => ({
  Header: ({ children }: { children?: React.ReactNode }) => <header>{children}</header>,
}))
vi.mock('@/components/search', () => ({ Search: () => null }))

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  api: mocks,
}))

const variables = (scope: string) =>
  Array.from({ length: 30 }, (_, index) => ({
    name: `${scope}_variable_${String(index).padStart(2, '0')}`,
    value: index === 3 ? '9' : '1',
    default: '1',
    scope,
  }))

function Page() {
  const [tab, setTab] = useState<SettingsTab>('session')
  return <AdminSettingsPage tab={tab} onTabChange={setTab} />
}

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Page />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  mocks.get.mockImplementation(async (path: string) => {
    const scope = new URLSearchParams(path.split('?')[1]).get('scope') ?? 'session'
    const all = variables(scope)
    return { variables: all, count: all.length, total: all.length, limit: 1000, offset: 0, scope }
  })
})

afterEach(async () => {
  await cleanup()
  vi.clearAllMocks()
})

describe('Admin Settings', () => {
  it('shows each scope as a plain tab and loads every list once', async () => {
    const screen = await mount()
    await expect.element(screen.getByRole('heading', { name: 'Admin Settings' })).toBeVisible()
    const tabs = ['Session variables', 'Global variables']
    for (const name of tabs) {
      await expect.element(screen.getByRole('tab', { name })).toBeVisible()
    }
    // The engine offers no password policy, so there is no tab for one.
    expect(screen.getByRole('tab').elements().map((tab) => tab.textContent)).toEqual(tabs)
    await expect.element(screen.getByText('session_variable_00')).toBeVisible()
    await expect.element(screen.getByText('30 variables')).toBeVisible()
    await expect.element(screen.getByText('1 changed')).toBeVisible()

    await screen.getByRole('tab', { name: 'Global variables' }).click()
    await expect.element(screen.getByText('global_variable_00')).toBeVisible()
    await screen.getByRole('tab', { name: 'Session variables' }).click()
    await expect.element(screen.getByText('session_variable_00')).toBeVisible()

    // One request per list, whatever the tab switching: no reload on return.
    const listCalls = mocks.get.mock.calls.filter(([path]) => String(path).startsWith('/variables?'))
    expect(listCalls.map(([path]) => new URLSearchParams(String(path).split('?')[1]).get('scope')).sort())
      .toEqual(['global', 'session'])
  })

  it('searches and pages without asking the server again', async () => {
    const screen = await mount()
    await expect.element(screen.getByText('session_variable_00')).toBeVisible()
    const before = mocks.get.mock.calls.length
    await screen.getByRole('textbox', { name: 'Search variables' }).fill('variable_2')
    await expect.element(screen.getByText('10 variables')).toBeVisible()
    await expect.element(screen.getByText('session_variable_29')).toBeVisible()
    expect(screen.getByText('session_variable_00').query()).toBeNull()
    expect(mocks.get.mock.calls.length).toBe(before)
  })

  it('fits a phone screen without sideways scrolling', async () => {
    await page.viewport(390, 844)
    const screen = await mount()
    await expect.element(screen.getByText('session_variable_00')).toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390)
    await page.viewport(1280, 720)
  })
})
