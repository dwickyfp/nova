import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import type { ReactNode } from 'react'
import type { SearchResponse, SecurableDetail } from './catalog-metadata-api'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const api = vi.hoisted(() => ({
  resolveSecurable: vi.fn(),
  getLineage: vi.fn(),
  putTags: vi.fn(),
  deleteTag: vi.fn(),
  annotateSecurable: vi.fn(),
  searchCatalog: vi.fn(),
}))

vi.mock('./catalog-metadata-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./catalog-metadata-api')>()),
  ...api,
}))

const { parseCatalogQuery, parseTagInput } = await import('./catalog-metadata-api')
const { CatalogMetadataSection } = await import('./catalog-metadata-section')
const { CatalogSearch } = await import('./catalog-search')

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const column = {
  id: 'c'.repeat(64),
  securable_type: 'column',
  full_name: 'default_catalog.sales.orders.email',
  catalog_name: 'default_catalog',
  database_name: 'sales',
  object_name: 'orders',
  column_name: 'email',
  data_type: 'varchar(64)',
  status: 'live' as const,
  dropped_at: null,
  comment: null,
  engine_comment: 'customer email',
  owner_name: null,
  owner_type: null,
  classification: 'CONFIDENTIAL',
  tags: [{ key: 'pii', value: 'email' }],
}

function detail(overrides: Partial<SecurableDetail> = {}): SecurableDetail {
  return {
    ...column,
    id: 'r'.repeat(64),
    securable_type: 'table',
    full_name: 'default_catalog.sales.orders',
    column_name: null,
    data_type: null,
    engine_comment: null,
    classification: null,
    comment: 'Customer orders',
    owner_name: 'sales_ops',
    owner_type: 'role',
    tags: [{ key: 'tier', value: 'gold' }],
    first_seen_at: null,
    parent_id: null,
    can_edit: false,
    can_set_owner: false,
    columns: [column],
    used_by: [{ owner_type: 'semantic_view', owner_id: 'v1', label: 'default_catalog.sales.public.revenue' }],
    ...overrides,
  }
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset()
  api.getLineage.mockResolvedValue({
    id: 'r'.repeat(64),
    full_name: 'default_catalog.sales.orders',
    upstream: [{ id: 'u', full_name: 'default_catalog.raw.orders', status: 'dropped', statement_kind: 'insert', last_seen_at: null }],
    downstream: [],
  })
})

describe('catalog query parsing', () => {
  it('splits a tag filter out of the search text', () => {
    expect(parseCatalogQuery('orders tag:pii=email ')).toEqual({ q: 'orders', tag: 'pii=email' })
    expect(parseCatalogQuery('tag:')).toEqual({ q: 'tag:', tag: '' })
    expect(parseCatalogQuery('  customer   email ')).toEqual({ q: 'customer email', tag: '' })
  })

  it('accepts key or key=value tags and rejects bad keys', () => {
    expect(parseTagInput('pii')).toEqual({ key: 'pii', value: null })
    expect(parseTagInput(' pii = a=b ')).toEqual({ key: 'pii', value: 'a=b' })
    expect(parseTagInput('9pii')).toBeNull()
    expect(parseTagInput('has space')).toBeNull()
  })
})

describe('CatalogMetadataSection', () => {
  it('shows description, owner, tags, lineage, dependents, and column metadata read-only', async () => {
    api.resolveSecurable.mockResolvedValue(detail())
    const screen = await render(wrap(
      <CatalogMetadataSection catalog='default_catalog' database='sales' object='orders' />,
    ))
    await expect.element(screen.getByText('Customer orders')).toBeVisible()
    await expect.element(screen.getByText('sales_ops')).toBeVisible()
    await expect.element(screen.getByText('tier')).toBeVisible()
    await expect.element(screen.getByText('default_catalog.raw.orders')).toBeVisible()
    await expect.element(screen.getByText('Dropped')).toBeVisible()
    await expect.element(screen.getByText('default_catalog.sales.public.revenue')).toBeVisible()
    await expect.element(screen.getByText('CONFIDENTIAL')).toBeVisible()
    await expect.element(screen.getByText('customer email')).toBeVisible()
    expect(api.resolveSecurable).toHaveBeenCalledWith('default_catalog', 'sales', 'orders')
    expect(screen.container.textContent).not.toContain('Add tag')
    expect(screen.container.querySelector('[aria-label^="Edit metadata"]')).toBeNull()
  })

  it('lets a role that may edit add and remove tags', async () => {
    api.resolveSecurable.mockResolvedValue(detail({ can_edit: true }))
    api.putTags.mockResolvedValue(detail({ can_edit: true, tags: [{ key: 'tier', value: 'gold' }, { key: 'pii', value: null }] }))
    api.deleteTag.mockResolvedValue(detail({ can_edit: true, tags: [] }))
    const screen = await render(wrap(
      <CatalogMetadataSection catalog='default_catalog' database='sales' object='orders' />,
    ))
    const input = screen.getByPlaceholder('key or key=value')
    await userEvent.fill(input, '9bad')
    await expect.element(screen.getByText(/A tag key starts with a letter/)).toBeVisible()
    await userEvent.fill(input, 'pii')
    await userEvent.keyboard('{Enter}')
    await expect.poll(() => api.putTags.mock.calls.length).toBe(1)
    expect(api.putTags).toHaveBeenCalledWith('r'.repeat(64), [{ key: 'pii', value: null }])
    await screen.getByRole('button', { name: 'Remove tag tier' }).click()
    await expect.poll(() => api.deleteTag.mock.calls.length).toBe(1)
    await expect.element(screen.getByText('No tags')).toBeVisible()
  })

  it('explains an object the catalog has not synced', async () => {
    const { ApiError } = await import('@/lib/api-client')
    api.resolveSecurable.mockRejectedValue(new ApiError(404, 'Object not found'))
    const screen = await render(wrap(
      <CatalogMetadataSection catalog='default_catalog' database='sales' object='fresh' />,
    ))
    await expect.element(screen.getByText(/not in the Nova Catalog yet/)).toBeVisible()
  })
})

describe('CatalogSearch', () => {
  it('searches as the user types and opens the chosen result', async () => {
    const response: SearchResponse = {
      query: null,
      tag: 'pii',
      count: 1,
      truncated: false,
      results: [{ ...column, matched: ['tag'] }],
    }
    api.searchCatalog.mockResolvedValue(response)
    const onSelect = vi.fn()
    const screen = await render(wrap(<CatalogSearch onSelect={onSelect} />))
    await userEvent.fill(screen.getByRole('combobox', { name: 'Search the catalog' }), 'tag:pii')
    await expect.element(screen.getByText('email', { exact: true })).toBeVisible()
    expect(api.searchCatalog.mock.calls[0][0]).toEqual({ q: '', tag: 'pii', limit: 20 })
    await userEvent.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: column.id }))
  })

  it('says when nothing the role can see matches', async () => {
    api.searchCatalog.mockResolvedValue({ query: 'zz', tag: null, count: 0, truncated: false, results: [] })
    const screen = await render(wrap(<CatalogSearch onSelect={() => {}} />))
    await userEvent.fill(screen.getByRole('combobox', { name: 'Search the catalog' }), 'zz')
    await expect.element(screen.getByText(/Nothing your role can see matches/)).toBeVisible()
  })
})
