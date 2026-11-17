import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import type { DashboardDefinition } from '../contracts'
import { FilterBar, FilterBuilder } from './filters'
import type { FilterValue } from './filter-model'
import '@/styles/index.css'

// Release QA for the filter bar controls (docs/reports/native-dataviz/QA_MATRIX.csv).

const DATASET = 'aaaaaaaa-0000-4000-8000-0000000000c1'
const definition = {
  dashboard_id: 'aaaaaaaa-0000-4000-8000-0000000000c2', title: 'Orders', pages: [],
  filters: [{ filter_id: 'aaaaaaaa-0000-4000-8000-0000000000c3', title: 'Paid', kind: 'boolean_switch',
    field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'paid' }, scope: { kind: 'global' }, mappings: [], defaults: [],
    allow_all: true, include_null: false, searchable: true, depends_on: [] }],
} as unknown as DashboardDefinition

// Nothing here may reach the network: a 401 would send the test page to sign-in.
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })))
})
afterEach(async () => { await cleanup(); vi.unstubAllGlobals() })

describe('NDV-FILTER-008 true/false control', () => {
  it('offers All, True, False and No value, and choosing No value asks for rows with no value', async () => {
    const changes: (FilterValue | undefined)[] = []
    const client = new QueryClient()
    const screen = await render(<QueryClientProvider client={client}>
      <FilterBar definition={definition} values={{}} onChange={(_, value) => changes.push(value)} crossFilters={[]}
        onClearCross={() => undefined} editing={false} onEdit={() => undefined} draft={false} />
    </QueryClientProvider>)
    await screen.getByRole('combobox', { name: 'Paid filter' }).click()
    const options = page.getByRole('option')
    await expect.element(options.first()).toBeVisible()
    expect(options.elements().map(item => item.textContent)).toEqual(['All', 'True', 'False', 'No value'])
    await page.getByRole('option', { name: 'No value' }).click()
    expect(changes).toEqual([{ kind: 'boolean', value: null }])
  })
})

describe('Multi-value filter list', () => {
  const regions = {
    ...definition,
    filters: [{ filter_id: 'aaaaaaaa-0000-4000-8000-0000000000c4', title: 'Region', kind: 'multi_value',
      field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'region' }, scope: { kind: 'global' }, mappings: [], defaults: [],
      allow_all: true, include_null: false, searchable: true, depends_on: [] }],
  } as unknown as DashboardDefinition
  const column = { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' }
  const asked: { filters?: unknown; dimensions?: unknown[] }[] = []
  function serve(names: string[], truncated = false) {
    asked.length = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
      // The filter reads the dataset definition for the field's type: text fields can be searched.
      if (!options.body && url.includes(`/viz/datasets/${DATASET}?`)) {
        return new Response(JSON.stringify({ resource_id: DATASET, revision: 1, metadata_revision: 1, grants_revision: 1, publication_id: null,
          definition: { dataset_id: DATASET, title: 'Orders', schema_fingerprint: 'a'.repeat(64),
            fields: [{ field_id: 'region', name: 'Region', source_column: 'region', logical_type: 'string', physical_type: 'VARCHAR(32)', semantic_role: 'dimension', nullable: true }],
            source: { kind: 'table', catalog: 'default_catalog', database: 'Sales', object_name: 'Orders', physical_id: '1001' } } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      const body = JSON.parse(String(options.body ?? '{}')) as { dimensions?: unknown[] }
      if (body.dimensions) asked.push(body)
      return new Response(JSON.stringify({ dataset_id: DATASET, revision: 1, truncated, columns: [column],
        rows: names.map(name => [{ type: 'string', value: name }]) }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
  }
  async function open(values: Record<string, FilterValue>, changes: (FilterValue | undefined)[] = []) {
    const screen = await render(<QueryClientProvider client={new QueryClient()}>
      <FilterBar definition={regions} values={values} onChange={(_, value) => changes.push(value)} crossFilters={[]}
        onClearCross={() => undefined} editing={false} onEdit={() => undefined} draft={false} />
    </QueryClientProvider>)
    await screen.getByRole('combobox', { name: 'Region filter' }).or(screen.getByRole('button', { name: 'Region filter' })).click()
    return screen
  }

  it('NDV-FILTER-016: a chosen value the data no longer has stays chosen, marked, and can be removed', async () => {
    serve(['East', 'West'])
    const changes: (FilterValue | undefined)[] = []
    const saved = { kind: 'members', values: [{ type: 'string', value: 'North' }, { type: 'string', value: 'East' }] } as FilterValue
    await open({ 'aaaaaaaa-0000-4000-8000-0000000000c4': saved }, changes)
    const north = page.getByRole('option', { name: /North/ })
    await expect.element(north).toHaveAttribute('aria-selected', 'true')
    await expect.element(north).toHaveTextContent('Not in the data now')
    await expect.element(page.getByRole('option', { name: 'East' })).toHaveAttribute('aria-selected', 'true')
    expect(page.getByRole('option').elements().map(item => item.textContent)).toEqual(['NorthNot in the data now', 'East', 'West'])
    // Nothing changed until the viewer acts; removing it keeps the other choice.
    expect(changes).toEqual([])
    await north.click()
    expect(changes).toEqual([{ kind: 'members', values: [{ type: 'string', value: 'East' }] }])
  })

  it('NDV-FILTER-018: a long list shows the first 200 values, says so, and searches on the server', async () => {
    serve(Array.from({ length: 200 }, (_, index) => `City ${String(index).padStart(3, '0')}`), true)
    await open({})
    await expect.element(page.getByText('Showing the first 200 values. Search to narrow the list.')).toBeVisible()
    expect(page.getByRole('option').elements()).toHaveLength(200)
    expect(asked[0]).toMatchObject({ limit: 200 })
    await page.getByRole('textbox', { name: 'Search Region' }).fill('City 19')
    await expect.poll(() => asked.length).toBeGreaterThan(1)
    expect(asked[asked.length - 1]).toMatchObject({ limit: 200,
      filters: { expression: { type: 'predicate', op: 'like_literal', values: [{ type: 'string', value: 'City 19' }] } } })
  })

  it('NDV-FILTER-013: a filter that follows another lists only values matching the other choice', async () => {
    const CITY = 'aaaaaaaa-0000-4000-8000-0000000000c5'
    const cascading = { ...regions, filters: [
      { ...regions.filters![0], filter_id: CITY, title: 'City', field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'city' } },
      { ...regions.filters![0], depends_on: [CITY] },
    ] } as unknown as DashboardDefinition
    serve(['East'])
    const jakarta = { kind: 'members', values: [{ type: 'string', value: 'Jakarta' }] } as FilterValue
    const screen = await render(<QueryClientProvider client={new QueryClient()}>
      <FilterBar definition={cascading} values={{ [CITY]: jakarta }} onChange={() => undefined} crossFilters={[]}
        onClearCross={() => undefined} editing={false} onEdit={() => undefined} draft={false} />
    </QueryClientProvider>)
    await screen.getByRole('button', { name: 'Region filter' }).click()
    await expect.element(page.getByRole('option', { name: 'East' })).toBeVisible()
    const parent = { type: 'predicate', field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'city' }, op: 'eq',
      values: [{ type: 'string', value: 'Jakarta' }] }
    expect(asked[0]).toMatchObject({ dimensions: [{ field_id: 'region' }], filters: { expression: parent } })
    // Searching keeps the parent choice and adds the search.
    await page.getByRole('textbox', { name: 'Search Region' }).fill('Ea')
    await expect.poll(() => asked.length).toBeGreaterThan(1)
    expect(asked[asked.length - 1]).toMatchObject({ filters: { expression: { type: 'and', children: [
      parent, { type: 'predicate', op: 'like_literal', values: [{ type: 'string', value: 'Ea' }] }] } } })
  })

  it('NDV-FILTER-013: an author makes a filter follow another, and removing the parent frees the child', async () => {
    serve([])
    const applied: unknown[][] = []
    const datasets = [{ dataset_id: DATASET, revision: 1, title: 'Orders' }] as never
    const screen = await render(<QueryClientProvider client={new QueryClient()}>
      <FilterBuilder open onOpenChange={() => undefined} definition={regions} datasets={datasets} saving={false}
        onApply={filters => applied.push(filters)} />
    </QueryClientProvider>)
    await screen.getByRole('combobox', { name: 'Filter field' }).click()
    await page.getByRole('option', { name: 'Region' }).click()
    await screen.getByRole('textbox', { name: 'Filter label' }).fill('Region again')
    await screen.getByRole('combobox', { name: 'Values follow' }).click()
    await page.getByRole('option', { name: 'Region' }).click()
    await screen.getByRole('button', { name: /^Add multiple values filter/ }).click()
    await expect.element(screen.getByText('· follows Region', { exact: false })).toBeVisible()
    await screen.getByRole('button', { name: 'Apply filters' }).click()
    expect((applied[0][1] as { depends_on: string[] }).depends_on).toEqual(['aaaaaaaa-0000-4000-8000-0000000000c4'])
    await screen.getByRole('button', { name: 'Remove Region', exact: true }).click()
    await screen.getByRole('button', { name: 'Apply filters' }).click()
    expect(applied[1]).toHaveLength(1)
    expect((applied[1][0] as { depends_on: string[] }).depends_on).toEqual([])
  })
})
