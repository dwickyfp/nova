import { describe, expect, it } from 'vitest'
import type { ChartDefinition, DashboardDefinition } from '../contracts'
import { chartContext, type FilterDefinition, type FilterValue } from './filter-model'

// Release QA for dashboard filters (docs/reports/native-dataviz/QA_MATRIX.csv).

const ORDERS = 'aaaaaaaa-0000-4000-8000-0000000000b1'
const field = (field_id: string) => ({ dataset_id: ORDERS, dataset_revision: 2, field_id })
const target = { dataset_id: ORDERS, dataset_revision: 2 } as ChartDefinition
const filter = (overrides: Partial<FilterDefinition>): FilterDefinition => ({
  filter_id: 'f1', title: 'Customer', kind: 'single_value', field: field('customer'), scope: { kind: 'global' },
  mappings: [], defaults: [], allow_all: true, include_null: false, searchable: true, depends_on: [], ...overrides,
} as FilterDefinition)
const dashboard = (filters: FilterDefinition[]) =>
  ({ dashboard_id: 'd', title: 'D', pages: [], filters } as unknown as DashboardDefinition)
const context = (filters: FilterDefinition[], values: Record<string, FilterValue>, widgetId = 'w1', pageId = 'p1') =>
  chartContext({ definition: dashboard(filters), chart: target, widgetId, pageId, values, crossFilters: [] })

describe('NDV-FILTER-001 single-value filter', () => {
  it('applies one eq predicate on the bound field only to the widgets in its scope', () => {
    const single = filter({ scope: { kind: 'widget_subset', widget_ids: ['w1', 'w2'] } })
    const values = { f1: { kind: 'members' as const, values: [{ type: 'string' as const, value: 'PT Maju Jaya' }] } }
    for (const widget of ['w1', 'w2']) {
      expect(context([single], values, widget)?.expression).toEqual({
        type: 'predicate', field: field('customer'), op: 'eq', values: [{ type: 'string', value: 'PT Maju Jaya' }],
        source: 'global', stage: 'row',
      })
    }
    expect(context([single], values, 'w3')).toBeUndefined()
    // A chart on another dataset without a mapping is never filtered.
    expect(chartContext({ definition: dashboard([single]), chart: { dataset_id: 'other', dataset_revision: 1 } as ChartDefinition,
      widgetId: 'w1', pageId: 'p1', values, crossFilters: [] })).toBeUndefined()
  })
})

describe('NDV-FILTER-003 date picker on a timestamp column', () => {
  it('sends one chosen calendar day as a half-open day of date bounds, across a leap day', () => {
    const picker = filter({ title: 'Ordered at', kind: 'date_picker', field: field('ordered_at') })
    const day = { type: 'date' as const, value: '2024-02-29' }
    const tree = context([picker], { f1: { kind: 'range', from: day, to: day } })
    // Date-typed bounds are anchored at midnight in the field's own time zone by the server
    // (backend test_date_bounds_on_timestamps_use_the_fields_own_midnight).
    expect(tree?.expression).toEqual({ type: 'and', children: [
      { type: 'predicate', field: field('ordered_at'), op: 'gte', values: [{ type: 'date', value: '2024-02-29' }], source: 'global', stage: 'row' },
      { type: 'predicate', field: field('ordered_at'), op: 'lt', values: [{ type: 'date', value: '2024-03-01' }], source: 'global', stage: 'row' },
    ] })
    const yearEnd = context([picker], { f1: { kind: 'range', from: { type: 'date', value: '2025-12-31' }, to: { type: 'date', value: '2025-12-31' } } })
    expect(yearEnd?.expression).toMatchObject({ children: [{ op: 'gte', values: [{ value: '2025-12-31' }] }, { op: 'lt', values: [{ value: '2026-01-01' }] }] })
  })
})

describe('NDV-FILTER-010 widget-subset scope', () => {
  it('leaves an excluded widget query unchanged while the chosen widget is filtered', () => {
    const scoped = filter({ kind: 'multi_value', title: 'Region', field: field('region'), scope: { kind: 'widget_subset', widget_ids: ['w2'] } })
    const values = { f1: { kind: 'members' as const, values: [{ type: 'string' as const, value: 'Jakarta' }, { type: 'string' as const, value: 'Medan' }] } }
    expect(context([scoped], values, 'w1')).toBeUndefined()
    expect(context([scoped], values, 'w2')?.expression).toMatchObject({ op: 'in', values: [{ value: 'Jakarta' }, { value: 'Medan' }] })
    // An unscoped filter still applies to the excluded widget, and only that one predicate.
    const global = filter({ filter_id: 'f2', kind: 'text_entry', title: 'Name', field: field('name') })
    const both = { ...values, f2: { kind: 'text' as const, value: 'maju' } }
    expect(context([scoped, global], both, 'w1')?.expression).toMatchObject({ op: 'like_literal', values: [{ value: 'maju' }] })
    expect(context([scoped, global], both, 'w2')?.expression).toMatchObject({ type: 'and', children: [{ op: 'in' }, { op: 'like_literal' }] })
  })
})
