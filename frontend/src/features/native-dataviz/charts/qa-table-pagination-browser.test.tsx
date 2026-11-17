import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import type { ChartDefinition, DatasetQueryRequest, DatasetQueryResult } from '../contracts'
import { NativeChart } from './native-chart'
import '@/styles/index.css'

// Release QA NDV-CHART-016 (docs/reports/native-dataviz/QA_MATRIX.csv): a table
// over a large result reads one server page at a time in a stable order.

const DATASET = 'aaaaaaaa-0000-4000-8000-0000000000e1'
type Column = DatasetQueryResult['columns'][number]
const STORE = { key: 'd0', name: 'store', logical_type: 'string', role: 'dimension', field_id: 'store' } as Column
const REVENUE = { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' } as Column

// 250 stores; revenue repeats every ten stores, so the sort key ties often.
const ROWS = Array.from({ length: 250 }, (_, i) => ({ store: `Store ${String(i + 1).padStart(3, '0')}`, revenue: (i % 10) + 1 }))

function table(options: { pageSize?: number; rowLimit?: number; grandTotal?: boolean } = {}) {
  return {
    schema_version: 2, chart_id: 'cccccccc-0000-4000-8000-0000000000e1', title: 'Revenue by store',
    dataset_id: DATASET, dataset_revision: 1, visual: 'table',
    row_limit: options.rowLimit ?? 1000,
    encoding: {
      kind: 'table', dimensions: [{ field_id: 'store' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }],
      page_size: options.pageSize ?? 100, ...(options.grandTotal ? { grand_total: true } : {}),
    },
  } as ChartDefinition
}

/** A stand-in engine: the planner's total order (sort keys, then the store), then one page. */
function answer(request: DatasetQueryRequest): DatasetQueryResult {
  if (!request.dimensions?.length) {
    const total = ROWS.reduce((sum, row) => sum + row.revenue, 0)
    return { dataset_id: DATASET, revision: 1, columns: [REVENUE], rows: [[{ type: 'decimal', value: String(total) }]], truncated: false }
  }
  const sort = request.sort?.[0]
  const ordered = [...ROWS].sort((a, b) => {
    if (sort?.column === 'm0' && a.revenue !== b.revenue) return (a.revenue - b.revenue) * (sort.direction === 'desc' ? -1 : 1)
    const byStore = a.store.localeCompare(b.store)
    return sort?.column === 'd0' && sort.direction === 'desc' ? -byStore : byStore
  })
  const offset = request.offset ?? 0
  const limit = request.limit ?? 1000
  const page = ordered.slice(offset, offset + limit)
  const truncated = ordered.length > offset + limit
  return {
    dataset_id: DATASET, revision: 1, columns: [STORE, REVENUE], truncated, offset,
    next_offset: truncated ? offset + page.length : null,
    rows: page.map(row => [{ type: 'string', value: row.store }, { type: 'decimal', value: String(row.revenue) }]),
  }
}

const sent: DatasetQueryRequest[] = []
const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  if (!url.includes(`/viz/datasets/${DATASET}/query`)) throw new Error(`Unexpected request ${url}`)
  const request = JSON.parse(String(init?.body)) as DatasetQueryRequest
  sent.push(request)
  return new Response(JSON.stringify(answer(request)), { status: 200, headers: { 'Content-Type': 'application/json' } })
})

async function show(chart: ChartDefinition) {
  sent.length = 0
  fetchMock.mockClear()
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <div style={{ width: 640, height: 480, display: 'flex' }}><NativeChart chart={chart} draft={false} /></div>
  </QueryClientProvider>)
}

const firstStore = (screen: Awaited<ReturnType<typeof show>>) =>
  screen.container.querySelector('tbody tr td')?.textContent

afterEach(async () => {
  await cleanup()
  vi.unstubAllGlobals()
})

describe('NDV-CHART-016 server-side table pages', () => {
  it('fetches each page once from the server and shows which rows are on screen', async () => {
    const screen = await show(table())
    await expect.element(screen.getByText('Rows 1–100', { exact: true })).toBeVisible()
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ limit: 100 })
    expect(sent[0].offset ?? 0).toBe(0)
    expect(screen.container.querySelectorAll('tbody tr')).toHaveLength(100)
    await expect.element(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()

    await screen.getByRole('button', { name: 'Next page' }).click()
    await expect.element(screen.getByText('Rows 101–200', { exact: true })).toBeVisible()
    expect(sent).toHaveLength(2)
    expect(sent[1]).toMatchObject({ limit: 100, offset: 100 })
    expect(firstStore(screen)).toBe('Store 101')

    await screen.getByRole('button', { name: 'Next page' }).click()
    await expect.element(screen.getByText('Rows 201–250', { exact: true })).toBeVisible()
    expect(sent).toHaveLength(3)
    expect(sent[2]).toMatchObject({ limit: 100, offset: 200 })
    expect(screen.container.querySelectorAll('tbody tr')).toHaveLength(50)
    await expect.element(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()

    // Going back reads the cached page; no new query is sent.
    await screen.getByRole('button', { name: 'Previous page' }).click()
    await expect.element(screen.getByText('Rows 101–200', { exact: true })).toBeVisible()
    expect(firstStore(screen)).toBe('Store 101')
    expect(sent).toHaveLength(3)
    // A table read in pages never claims it stopped early.
    await expect.element(screen.getByText(/Showing the first/)).not.toBeInTheDocument()
  })

  it('sorts a column on the server and starts again at the first page', async () => {
    const screen = await show(table())
    await expect.element(screen.getByText('Rows 1–100', { exact: true })).toBeVisible()
    await screen.getByRole('button', { name: 'Next page' }).click()
    await expect.element(screen.getByText('Rows 101–200', { exact: true })).toBeVisible()

    await screen.getByRole('button', { name: 'Sum of revenue' }).click()
    await expect.element(screen.getByText('Rows 1–100', { exact: true })).toBeVisible()
    expect(sent).toHaveLength(3)
    expect(sent[2]).toMatchObject({ limit: 100, sort: [{ column: 'm0', direction: 'asc' }] })
    expect(sent[2].offset ?? 0).toBe(0)
    await expect.element(screen.getByRole('columnheader', { name: 'Sum of revenue' })).toHaveAttribute('aria-sort', 'ascending')
    // Ties on revenue keep the store order, so the page is stable.
    await expect.poll(() => firstStore(screen)).toBe('Store 001')

    await screen.getByRole('button', { name: 'Next page' }).click()
    await expect.element(screen.getByText('Rows 101–200', { exact: true })).toBeVisible()
    expect(sent).toHaveLength(4)
    expect(sent[3]).toMatchObject({ offset: 100, sort: [{ column: 'm0', direction: 'asc' }] })
    // Revenue 1 has 25 stores, so rows 101–125 are the revenue 5 stores.
    expect(firstStore(screen)).toBe('Store 005')

    await screen.getByRole('button', { name: 'Sum of revenue' }).click()
    await expect.element(screen.getByRole('columnheader', { name: 'Sum of revenue' })).toHaveAttribute('aria-sort', 'descending')
    await expect.element(screen.getByText('Rows 1–100', { exact: true })).toBeVisible()
    expect(sent[sent.length - 1]).toMatchObject({ sort: [{ column: 'm0', direction: 'desc' }] })
    expect(sent[sent.length - 1].offset ?? 0).toBe(0)
  })

  it('shows no page controls when the result fits on one page', async () => {
    const screen = await show(table({ pageSize: 500 }))
    await expect.element(screen.getByRole('table')).toBeVisible()
    expect(screen.container.querySelectorAll('tbody tr')).toHaveLength(250)
    await expect.element(screen.getByRole('button', { name: 'Next page' })).not.toBeInTheDocument()
    await expect.element(screen.getByText(/^Rows /)).not.toBeInTheDocument()
    expect(sent).toHaveLength(1)
  })

  it('stops at the row limit and says the result was cut', async () => {
    const screen = await show(table({ rowLimit: 150 }))
    await expect.element(screen.getByText('Rows 1–100', { exact: true })).toBeVisible()
    await screen.getByRole('button', { name: 'Next page' }).click()
    await expect.element(screen.getByText('Rows 101–150', { exact: true })).toBeVisible()
    // The last page asks only for the rows the limit allows.
    expect(sent[1]).toMatchObject({ limit: 50, offset: 100 })
    await expect.element(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    await expect.element(screen.getByText('Showing the first 150 rows. Add a filter to see everything.')).toBeVisible()
  })

  it('totals every matching row with one query, not just the rows on screen', async () => {
    const screen = await show(table({ grandTotal: true }))
    await expect.element(screen.getByText('Rows 1–100', { exact: true })).toBeVisible()
    const total = ROWS.reduce((sum, row) => sum + row.revenue, 0)
    await expect.poll(() => screen.container.querySelector('tbody tr:last-child td:last-child')?.textContent)
      .toBe(total.toLocaleString())
    const totals = sent.filter(request => !request.dimensions?.length)
    expect(totals).toHaveLength(1)
    expect(totals[0]).toMatchObject({ measures: [{ field_id: 'revenue', aggregation: 'sum' }], limit: 1 })
    expect(totals[0].sort ?? []).toEqual([])
    expect(totals[0].dimensions ?? []).toEqual([])

    await screen.getByRole('button', { name: 'Next page' }).click()
    await expect.element(screen.getByText('Rows 101–200', { exact: true })).toBeVisible()
    // The totals query is shared by every page.
    expect(sent.filter(request => !request.dimensions?.length)).toHaveLength(1)
    expect(screen.container.querySelector('tbody tr:last-child td:last-child')?.textContent).toBe(total.toLocaleString())
  })
})

describe('NDV-CACHE-010 a filter change while a page is still loading', () => {
  it('starts again at the first page of the new query and never shows the late page', async () => {
    const FILTER = { expression: { type: 'predicate', field: { dataset_id: DATASET, dataset_revision: 1, field_id: 'store' },
      op: 'like_literal', values: [{ type: 'string', value: '7' }], source: 'global', stage: 'row' } } as never
    let release: () => void = () => undefined
    const held = new Promise<void>(resolve => { release = resolve })
    sent.length = 0
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const request = JSON.parse(String(init?.body)) as DatasetQueryRequest
      sent.push(request)
      // The unfiltered second page answers only after the filter has changed.
      if (!request.filters && request.offset === 100) await held
      const result = answer(request)
      const filtered = request.filters
        ? { ...result, rows: result.rows.filter(row => String(row[0]?.value).includes('7')).slice(0, 5), truncated: false }
        : result
      return new Response(JSON.stringify(filtered), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = (context?: never) => <QueryClientProvider client={client}>
      <div style={{ width: 640, height: 480, display: 'flex' }}><NativeChart chart={table()} draft={false} context={context} /></div>
    </QueryClientProvider>
    const screen = await render(view())
    await expect.element(screen.getByText('Rows 1–100', { exact: true })).toBeVisible()
    await screen.getByRole('button', { name: 'Next page' }).click()
    await expect.poll(() => sent.some(request => request.offset === 100)).toBe(true)

    // The viewer filters while page 2 is still on its way.
    await screen.rerender(view(FILTER))
    await expect.poll(() => sent.some(request => request.filters)).toBe(true)
    const first = sent.find(request => request.filters)!
    expect(first.offset ?? 0).toBe(0)
    await expect.poll(() => screen.container.querySelectorAll('tbody tr').length).toBe(5)
    expect([...screen.container.querySelectorAll('tbody tr td:first-child')].every(cell => cell.textContent?.includes('7'))).toBe(true)

    // The late unfiltered page arrives: nothing on screen changes.
    release()
    await new Promise(resolve => setTimeout(resolve, 300))
    const stores = [...screen.container.querySelectorAll('tbody tr td:first-child')].map(cell => cell.textContent)
    expect(stores).toHaveLength(5)
    expect(stores.every(store => store?.includes('7'))).toBe(true)
    expect(screen.container.textContent).not.toContain('Rows 101–200')
    await expect.element(screen.getByRole('button', { name: 'Next page' })).not.toBeInTheDocument()
  })
})

