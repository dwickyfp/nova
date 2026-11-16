import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import type { DashboardDefinition } from '../contracts'
import { FilterBar } from './filters'
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
