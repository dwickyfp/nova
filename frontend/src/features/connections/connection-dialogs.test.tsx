import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page } from 'vitest/browser'
import type { ConnectionSummary } from './api'
import {
  AddCatalogDialog,
  AddConnectionDialog,
  ConnectionRolesDialog,
  DropConnectionDialog,
} from './connection-dialogs'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const createConnection = vi.fn()
const createForeignCatalog = vi.fn()
const setConnectionRoles = vi.fn()
vi.mock('./api', async (original) => ({
  ...(await original<typeof import('./api')>()),
  createConnection: (...args: unknown[]) => createConnection(...args),
  createForeignCatalog: (...args: unknown[]) => createForeignCatalog(...args),
  setConnectionRoles: (...args: unknown[]) => setConnectionRoles(...args),
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

beforeEach(() => {
  createConnection.mockReset()
  createForeignCatalog.mockReset()
  setConnectionRoles.mockReset()
})

describe('AddConnectionDialog', () => {
  it('builds the endpoint from the address fields and masks the password', async () => {
    createConnection.mockResolvedValue(connection())
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    await page.getByLabelText('Name', { exact: true }).fill('sales_pg')
    await page.getByLabelText('Host', { exact: true }).fill('db')
    await page.getByLabelText('Port (optional)').fill('5433')
    await page.getByLabelText('Database', { exact: true }).fill('sales')
    await page.getByLabelText('Username', { exact: true }).fill('reader')
    const password = page.getByLabelText('Password', { exact: true })
    await expect.element(password).toHaveAttribute('type', 'password')
    await password.fill('pw')
    await page.getByRole('button', { name: 'Add connection' }).click()
    expect(createConnection).toHaveBeenCalledWith({
      name: 'sales_pg',
      connector_type: 'postgres',
      endpoint: 'db:5433/sales',
      comment: undefined,
      credentials: { username: 'reader', password: 'pw' },
    })
  })

  it('asks Snowflake for its account, database, warehouse, and an access token', async () => {
    createConnection.mockResolvedValue(connection({ connector_type: 'snowflake' }))
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    await page.getByRole('radio', { name: 'Snowflake' }).click()
    await page.getByLabelText('Name', { exact: true }).fill('sales_sf')
    await page.getByLabelText('Account', { exact: true }).fill('ORG-ACCT')
    await page.getByLabelText('Database', { exact: true }).fill('ANALYTICS')
    await page.getByLabelText('Warehouse', { exact: true }).fill('WH')
    await page.getByLabelText('Role (optional)').fill('READER')
    await page.getByLabelText('User', { exact: true }).fill('NOVA_SVC')
    await page.getByLabelText('Programmatic access token').fill('pat')
    await page.getByRole('button', { name: 'Add connection' }).click()
    expect(createConnection).toHaveBeenCalledWith(expect.objectContaining({
      connector_type: 'snowflake',
      endpoint: 'ORG-ACCT/ANALYTICS?warehouse=WH&role=READER',
      credentials: { username: 'NOVA_SVC', password: 'pat' },
    }))
  })

  it('offers exactly the supported sources', async () => {
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    for (const label of ['StarRocks', 'PostgreSQL', 'Snowflake', 'Iceberg', 'Kafka']) {
      await expect.element(page.getByRole('radio', { name: label })).toBeVisible()
    }
    await expect.element(page.getByRole('radio', { name: 'MySQL' })).not.toBeInTheDocument()
  })

  it('asks Iceberg for the catalog URI, storage settings and a storage key', async () => {
    createConnection.mockResolvedValue(connection({ connector_type: 'iceberg' }))
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    await page.getByRole('radio', { name: 'Iceberg' }).click()
    await page.getByLabelText('Name', { exact: true }).fill('lake_rest')
    await page.getByLabelText('Catalog URI').fill('http://iceberg-rest:8181')
    await page.getByLabelText('Storage endpoint (optional)').fill('http://object-store:9000')
    await page.getByLabelText('Storage region (optional)').fill('us-east-1')
    await page.getByLabelText('Access key ID').fill('AKIA')
    const secret = page.getByLabelText('Secret access key')
    await expect.element(secret).toHaveAttribute('type', 'password')
    await secret.fill('secret')
    await page.getByRole('button', { name: 'Add connection' }).click()
    expect(createConnection).toHaveBeenCalledWith({
      name: 'lake_rest',
      connector_type: 'iceberg',
      endpoint:
        'http://iceberg-rest:8181?s3_endpoint=http%3A%2F%2Fobject-store%3A9000&s3_region=us-east-1',
      comment: undefined,
      credentials: { username: 'AKIA', password: 'secret' },
    })
  })

  it('adds a Kafka cluster without credentials unless it signs in with SASL', async () => {
    createConnection.mockResolvedValue(connection({ connector_type: 'kafka' }))
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    await page.getByRole('radio', { name: 'Kafka' }).click()
    await page.getByLabelText('Name', { exact: true }).fill('events_kafka')
    await page.getByLabelText('Brokers').fill('kafka-1:9092, kafka-2:9092')
    await expect.element(page.getByLabelText('SASL username')).not.toBeInTheDocument()
    await expect
      .element(page.getByLabelText('SASL mechanism'))
      .not.toBeInTheDocument()
    await page.getByRole('button', { name: 'Add connection' }).click()
    expect(createConnection).toHaveBeenCalledWith({
      name: 'events_kafka',
      connector_type: 'kafka',
      endpoint: 'kafka-1:9092,kafka-2:9092',
      comment: undefined,
    })
  })

  it('needs the SASL username and password for a SASL Kafka cluster', async () => {
    createConnection.mockResolvedValue(connection({ connector_type: 'kafka' }))
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    await page.getByRole('radio', { name: 'Kafka' }).click()
    await page.getByLabelText('Name', { exact: true }).fill('secure_kafka')
    await page.getByLabelText('Brokers').fill('b1:9093')
    await page.getByLabelText('Security protocol').click()
    await page.getByRole('option', { name: 'SASL_SSL' }).click()
    await expect.element(page.getByRole('button', { name: 'Add connection' })).toBeDisabled()
    await page.getByLabelText('SASL username').fill('loader')
    await page.getByLabelText('SASL password').fill('pw')
    await page.getByRole('button', { name: 'Add connection' }).click()
    expect(createConnection).toHaveBeenCalledWith(expect.objectContaining({
      endpoint: 'b1:9093?security_protocol=SASL_SSL&sasl_mechanism=PLAIN',
      credentials: { username: 'loader', password: 'pw' },
    }))
  })

  it('keeps Add disabled until the name is a valid identifier', async () => {
    await render(<AddConnectionDialog open onOpenChange={() => {}} />)
    await page.getByLabelText('Name', { exact: true }).fill('1-bad')
    await expect
      .element(page.getByText('Use letters, digits, and underscores, starting with a letter.'))
      .toBeVisible()
    await expect.element(page.getByRole('button', { name: 'Add connection' })).toBeDisabled()
  })
})

describe('AddCatalogDialog', () => {
  it('registers a new connection, then the catalog over it', async () => {
    createConnection.mockResolvedValue(connection({ name: 'shop_sr_conn', connector_type: 'starrocks' }))
    createForeignCatalog.mockResolvedValue({ name: 'shop_sr' })
    const onCreated = vi.fn()
    await render(
      <AddCatalogDialog open onOpenChange={() => {}} connections={[]} onCreated={onCreated} />
    )
    await page.getByRole('radio', { name: 'StarRocks' }).click()
    await page.getByLabelText('Catalog name').fill('shop_sr')
    await page.getByLabelText('FE host').fill('sr-fe')
    await page.getByLabelText('Username', { exact: true }).fill('reader')
    await page.getByLabelText('Password', { exact: true }).fill('pw')
    await page.getByRole('button', { name: 'Add catalog' }).click()

    expect(createConnection).toHaveBeenCalledWith({
      name: 'shop_sr_conn',
      connector_type: 'starrocks',
      endpoint: 'sr-fe',
      credentials: { username: 'reader', password: 'pw' },
    })
    expect(createForeignCatalog).toHaveBeenCalledWith({
      name: 'shop_sr', connection: 'shop_sr_conn', comment: undefined,
    })
    expect(onCreated).toHaveBeenCalledWith('shop_sr')
  })

  it('reuses an existing connection of the chosen type without new credentials', async () => {
    createForeignCatalog.mockResolvedValue({ name: 'pg_shop' })
    await render(
      <AddCatalogDialog
        open
        onOpenChange={() => {}}
        connections={[connection({ name: 'shop_pg' })]}
        initialConnection='shop_pg'
      />
    )
    await page.getByLabelText('Catalog name').fill('pg_shop')
    await expect.element(page.getByLabelText('Host', { exact: true })).not.toBeInTheDocument()
    await page.getByRole('button', { name: 'Add catalog' }).click()
    expect(createConnection).not.toHaveBeenCalled()
    expect(createForeignCatalog).toHaveBeenCalledWith({
      name: 'pg_shop', connection: 'shop_pg', comment: undefined,
    })
  })

  it('never offers a connection without credentials', async () => {
    await render(
      <AddCatalogDialog
        open
        onOpenChange={() => {}}
        connections={[connection({ name: 'missing', has_credentials: false })]}
        initialConnection='missing'
      />
    )
    // Falls back to a new connection: the address fields are shown.
    await expect.element(page.getByLabelText('Host', { exact: true })).toBeVisible()
    await expect.element(page.getByRole('button', { name: 'Add catalog' })).toBeDisabled()
  })
})

describe('Kafka connections', () => {
  it('are never offered as a catalog source', async () => {
    await render(
      <AddCatalogDialog
        open
        onOpenChange={() => {}}
        connections={[connection({ name: 'events_kafka', connector_type: 'kafka' })]}
      />
    )
    await expect.element(page.getByRole('radio', { name: 'Iceberg' })).toBeVisible()
    await expect.element(page.getByRole('radio', { name: 'Kafka' })).not.toBeInTheDocument()
  })

  it('save the roles that may load from them', async () => {
    setConnectionRoles.mockResolvedValue(connection({ connector_type: 'kafka', roles: ['marketing'] }))
    const onSaved = vi.fn()
    await render(
      <ConnectionRolesDialog
        connection={connection({ name: 'events_kafka', connector_type: 'kafka', roles: ['finance'] })}
        onOpenChange={() => {}}
        onSaved={onSaved}
      />
    )
    const roles = page.getByLabelText('Roles', { exact: true })
    await expect.element(roles).toHaveValue('finance')
    await roles.fill('marketing, finance ,')
    await page.getByRole('button', { name: 'Save roles' }).click()
    expect(setConnectionRoles).toHaveBeenCalledWith('events_kafka', ['marketing', 'finance'])
    expect(onSaved).toHaveBeenCalled()
  })
})

describe('DropConnectionDialog', () => {
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
