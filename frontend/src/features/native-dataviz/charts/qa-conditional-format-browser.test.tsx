import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { NativeChart } from './native-chart'
import '@/styles/index.css'

// Release QA NDV-CHART-018 (docs/reports/native-dataviz/QA_MATRIX.csv): a value a
// highlight rule matched is marked by more than colour.

const DATASET = 'aaaaaaaa-0000-4000-8000-0000000000f1'
type Column = DatasetQueryResult['columns'][number]
const column = (key: string, field_id: string, role: 'dimension' | 'measure') =>
  ({ key, name: field_id, logical_type: role === 'measure' ? 'decimal' : 'string', role, field_id,
    ...(role === 'measure' ? { aggregation: 'sum' } : {}) }) as Column
const text = (value: string) => ({ type: 'string', value }) as const
const decimal = (value: string) => ({ type: 'decimal', value }) as const
const RULE = [{ field_id: 'revenue', kind: 'rule', operator: 'gt', value: decimal('1000'), tone: 'positive' }]

function chart(visual: 'table' | 'pivot'): ChartDefinition {
  return {
    schema_version: 2, chart_id: 'cccccccc-0000-4000-8000-0000000000f1', title: 'Revenue', dataset_id: DATASET,
    dataset_revision: 1, visual, row_limit: 1000, style: { conditional_formats: RULE },
    encoding: { kind: 'table', dimensions: [{ field_id: 'region' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }],
      ...(visual === 'pivot' ? { pivot_columns: [{ field_id: 'channel' }] } : { page_size: 100 }) },
  } as unknown as ChartDefinition
}

function result(visual: 'table' | 'pivot'): DatasetQueryResult {
  if (visual === 'table') return { dataset_id: DATASET, revision: 1, truncated: false,
    columns: [column('d0', 'region', 'dimension'), column('m0', 'revenue', 'measure')],
    rows: [[text('East'), decimal('1500')], [text('West'), decimal('800')]] }
  return { dataset_id: DATASET, revision: 1, truncated: false,
    columns: [column('d0', 'region', 'dimension'), column('d1', 'channel', 'dimension'), column('m0', 'revenue', 'measure')],
    rows: [[text('East'), text('Online'), decimal('1500')], [text('East'), text('Store'), decimal('200')]] }
}

async function show(visual: 'table' | 'pivot') {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(result(visual)),
    { status: 200, headers: { 'Content-Type': 'application/json' } })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <div style={{ width: 640, height: 360, display: 'flex' }}><NativeChart chart={chart(visual)} draft={false} /></div>
  </QueryClientProvider>)
}

afterEach(async () => {
  await cleanup()
  vi.unstubAllGlobals()
})

describe('NDV-CHART-018 a highlighted value has a non-colour cue', () => {
  it.each(['table', 'pivot'] as const)('in a %s: a marker, heavier text and words a screen reader reads', async visual => {
    const screen = await show(visual)
    const flagged = page.getByRole('cell', { name: /^1,500\s*, highlighted: above 1,000$/ })
    await expect.element(flagged).toBeVisible()
    const cell = flagged.element() as HTMLElement
    // A visible shape, not only a tint: the marker has size and an opaque fill.
    const marker = cell.querySelector<HTMLElement>('[data-highlighted] > [aria-hidden]')!
    const box = marker.getBoundingClientRect()
    expect(box.width).toBeGreaterThanOrEqual(5)
    expect(getComputedStyle(marker).backgroundColor).not.toMatch(/rgba\(.*, 0\)|transparent/)
    expect(Number(getComputedStyle(cell).fontWeight)).toBeGreaterThanOrEqual(600)
    expect(cell.querySelector('[data-highlighted]')?.getAttribute('title')).toBe('Highlighted: above 1,000')
    // A value the rule did not match has none of it.
    const plain = screen.container.querySelectorAll('td')
    const others = [...plain].filter(td => /^(800|200)$/.test(td.textContent ?? ''))
    expect(others).toHaveLength(1)
    expect(others[0].querySelector('[data-highlighted]')).toBeNull()
    expect(Number(getComputedStyle(others[0]).fontWeight)).toBeLessThan(600)
  })
})
