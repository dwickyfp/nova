import { afterEach, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import { api } from '@/lib/api-client'
import { ProvidersTab } from './providers-tab'
import '@/styles/index.css'

afterEach(() => vi.restoreAllMocks())

it.each([1280, 320])('registers and edits a decision provider at %ipx', async (width) => {
  await page.viewport(width, 900)
  const endpoint = 'https://api.example.com/custom/decide/?version=2'
  let providers: Record<string, unknown>[] = []
  vi.spyOn(api, 'get').mockImplementation(async (path) =>
    path === '/ai/providers'
      ? { providers, count: providers.length }
      : { models: [], count: 0 }
  )
  const post = vi.spyOn(api, 'post').mockImplementation(async (_path, body) => {
    providers = [
      { id: 'p1', is_active: true, has_api_key: false, ...(body as object) },
    ]
    return providers[0]
  })
  const put = vi.spyOn(api, 'put').mockImplementation(async (_path, body) => {
    providers = [{ ...providers[0], ...(body as object) }]
    return providers[0]
  })

  const screen = await render(<ProvidersTab />)
  await screen.getByRole('button', { name: 'Add Provider' }).click()
  await screen.getByLabelText('Name', { exact: true }).fill('Decision service')
  await screen.getByRole('combobox', { name: 'Type', exact: true }).click()
  await screen.getByRole('option', { name: 'Decision', exact: true }).click()
  await screen.getByLabelText('Inference Endpoint').fill(endpoint)
  const dialog = document.querySelector('[role="dialog"]')!
  expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth)
  await page.screenshot()
  await expect
    .element(screen.getByRole('button', { name: 'Test Connection' }))
    .not.toBeInTheDocument()
  await screen.getByRole('button', { name: 'Create Provider' }).click()
  expect(post).toHaveBeenCalledWith(
    '/ai/providers',
    expect.objectContaining({
      type: 'decision',
      endpoint,
    })
  )

  const row = screen.getByRole('row').filter({ hasText: /decision service/i })
  await row.getByRole('button').click()
  await screen.getByRole('menuitem', { name: 'Edit Provider' }).click()
  await expect
    .element(screen.getByLabelText('Inference Endpoint'))
    .toHaveValue(endpoint)
  await screen
    .getByLabelText('Name', { exact: true })
    .fill('Renamed decision service')
  await screen.getByRole('button', { name: 'Save Changes' }).click()
  expect(put).toHaveBeenCalledWith(
    '/ai/providers/p1',
    expect.objectContaining({
      type: 'decision',
      endpoint,
      name: 'Renamed decision service',
    })
  )

  await row.getByRole('button').click()
  await screen.getByRole('menuitem', { name: 'Add Model' }).click()
  await expect
    .element(screen.getByRole('combobox', { name: 'Type', exact: true }))
    .toHaveTextContent('Decision')
  await expect.element(screen.getByLabelText('Max Tokens')).not.toBeInTheDocument()
  await screen.getByRole('button', { name: 'Cancel', exact: true }).click()
})
