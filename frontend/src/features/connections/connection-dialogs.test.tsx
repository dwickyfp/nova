import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page } from 'vitest/browser'
import type { ConnectionSummary } from './api'
import {
  AddConnectionDialog,
  CreateForeignCatalogDialog,
  DropConnectionDialog,
} from './connection-dialogs'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const createConnection = vi.fn()
vi.mock('./api', async (original) => ({
  ...(await original<typeof import('./api')>()),
  createConnection: (...args: unknown[]) => createConnection(...args),
}))

const connection = (overrides: Partial<ConnectionSummary> = {}): ConnectionSummary => ({
  name: 'sales_pg',
  connector_type: 'postgres',
  credential_source: 'managed',
  has_credentials: true,
  owner: 'nova_admin',
  comment: null,
  catalogs: [],
  created_at: null,
  updated_at: null,
  ...overrides,
})

describe('connection dialogs', () => {
  it('masks the password and submits it only with the new connection', async () => {
    createConnection.mockResolvedValue(connection())
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    await page.getByLabelText('Name', { exact: true }).fill('sales_pg')
    await page.getByLabelText('Address').fill('db:5432/sales')
    await page.getByLabelText('Username', { exact: true }).fill('reader')
    const password = page.getByLabelText('Password', { exact: true })
    await expect.element(password).toHaveAttribute('type', 'password')
    await password.fill('pw')
    await page.getByRole('button', { name: 'Add connection' }).click()
    expect(createConnection).toHaveBeenCalledWith({
      name: 'sales_pg',
      connector_type: 'postgres',
      endpoint: 'db:5432/sales',
      comment: undefined,
      credentials: { username: 'reader', password: 'pw' },
    })
  })

  it('keeps Add disabled until the name is a valid identifier', async () => {
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    await page.getByLabelText('Name', { exact: true }).fill('1-bad')
    await expect
      .element(page.getByText('Use letters, digits, and underscores, starting with a letter.'))
      .toBeVisible()
    await expect.element(page.getByRole('button', { name: 'Add connection' })).toBeDisabled()
  })

  it('offers only connections that have credentials', async () => {
    await render(
      <CreateForeignCatalogDialog
        open
        onOpenChange={() => {}}
        connections={[connection({ name: 'missing', has_credentials: false })]}
      />
    )
    await expect
      .element(page.getByText('No connection with credentials yet.', { exact: false }))
      .toBeVisible()
    await expect.element(page.getByRole('button', { name: 'Create catalog' })).toBeDisabled()
  })

  it('will not remove a connection that catalogs still use', async () => {
    await render(
      <DropConnectionDialog
        connection={connection({ catalogs: ['sales'] })}
        onOpenChange={() => {}}
      />
    )
    await expect.element(page.getByText('Catalogs sales use it. Drop them first.')).toBeVisible()
    await expect.element(page.getByRole('button', { name: 'Remove' })).toBeDisabled()
  })
})
