import { describe, expect, it } from 'vitest'
import type { ChartDefinition, DatasetField, DatasetQueryResult } from '../contracts'
import { drillContext, drilledChart, drillOptions } from './drill'
import { cellStyle, matchedRule, MAX_PIVOT_COLUMNS, pivotModel, ranges, ruleMeaning } from './pivot'

const pivot = (extra: Record<string, unknown> = {}) => ({
  schema_version: 2, chart_id: 'c', title: 'Units', dataset_id: 'd', dataset_revision: 3, visual: 'pivot',
  encoding: { kind: 'table', dimensions: [{ field_id: 'region' }, { field_id: 'city' }], pivot_columns: [{ field_id: 'channel' }],
    measures: [{ field_id: 'units', aggregation: 'sum' }], grand_total: true, ...extra },
}) as unknown as ChartDefinition

const columns = [
  { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
  { key: 'd1', name: 'city', logical_type: 'string', role: 'dimension', field_id: 'city' },
  { key: 'd2', name: 'channel', logical_type: 'string', role: 'dimension', field_id: 'channel' },
  { key: 'm0', name: 'units', logical_type: 'integer', role: 'measure', field_id: 'units', aggregation: 'sum' },
] as DatasetQueryResult['columns']
const s = (value: string) => ({ type: 'string' as const, value })
const n = (value: number) => ({ type: 'integer' as const, value: String(value) })
const result = (rows: DatasetQueryResult['rows']): DatasetQueryResult => ({ dataset_id: 'd', revision: 3, columns, rows, truncated: false })

describe('pivot', () => {
  const rows = [
    [s('West'), s('Bandung'), s('Retail'), n(5)],
    [s('East'), s('Surabaya'), s('Online'), n(7)],
    [s('West'), s('Bandung'), s('Online'), n(3)],
    [s('West'), s('Cirebon'), s('Retail'), n(2)],
    [s('East'), s('Malang'), s('Retail'), n(4)],
  ]

  it('cross-tabulates rows by column groups with totals', () => {
    const model = pivotModel(pivot(), result(rows))
    expect(model.rowHeaders).toEqual(['Region', 'City'])
    expect(model.columnKeys).toEqual([['Online'], ['Retail']])
    expect(model.rows.map(row => [...row.key, ...row.values.map(cells => cells[0])])).toEqual([
      ['East', 'Malang', null, 4], ['East', 'Surabaya', 7, null], ['West', 'Bandung', 3, 5], ['West', 'Cirebon', null, 2],
    ])
    expect(model.columnTotals).toEqual([[10], [11]])
    expect(model.rowTotals).toEqual([[4], [7], [8], [2]])
  })

  it('adds a subtotal row after each first-level group', () => {
    const model = pivotModel(pivot({ subtotals: true }), result(rows))
    const subtotals = model.rows.filter(row => row.subtotal)
    expect(subtotals.map(row => [row.key[0], ...row.values.map(cells => cells[0])])).toEqual([['East', 7, 4], ['West', 3, 7]])
    // Grand totals count each value once, not again through subtotals.
    expect(model.columnTotals).toEqual([[10], [11]])
  })

  it('never adds up averages', () => {
    const chart = pivot({ measures: [{ field_id: 'units', aggregation: 'avg' }] })
    const averaged = { ...result(rows), columns: columns.map(column => column.role === 'measure' ? { ...column, aggregation: 'avg' } : column) } as DatasetQueryResult
    const model = pivotModel(chart, averaged)
    expect(model.columnTotals).toEqual([[null], [null]])
  })

  it('caps very wide pivots and says so', () => {
    const wide = Array.from({ length: MAX_PIVOT_COLUMNS + 5 }, (_, i) => [s('West'), s('Bandung'), s(`C${String(i).padStart(3, '0')}`), n(i)])
    const model = pivotModel(pivot(), result(wide))
    expect(model.columnKeys).toHaveLength(MAX_PIVOT_COLUMNS)
    expect(model.truncatedColumns).toBe(true)
  })
})

describe('conditional formatting', () => {
  const formats = [
    { field_id: 'units', kind: 'scale' as const, tone: 'accent' as const },
    { field_id: 'units', kind: 'rule' as const, operator: 'lt' as const, value: { type: 'decimal' as const, value: '3' }, tone: 'negative' as const },
  ]

  it('shades by position in the range, and a matching rule wins', () => {
    const range = ranges([{ field_id: 'units', value: 2 }, { field_id: 'units', value: 12 }, { field_id: null, value: 99 }]).get('units')
    expect(range).toEqual({ min: 2, max: 12 })
    expect(cellStyle(formats, 'units', 12, range)?.backgroundColor).toBe('color-mix(in oklab, var(--chart-1) 40%, transparent)')
    expect(cellStyle(formats, 'units', 7, range)?.backgroundColor).toBe('color-mix(in oklab, var(--chart-1) 23%, transparent)')
    expect(cellStyle(formats, 'units', 2, range)).toEqual({ backgroundColor: 'color-mix(in oklab, var(--destructive) 22%, transparent)', fontWeight: 600 })
  })

  it('NDV-CHART-018: names the rule a highlighted value passed, in words', () => {
    const format = new Intl.NumberFormat('en-US').format
    const rule = matchedRule(formats, 'units', 2)
    expect(rule?.operator).toBe('lt')
    expect(ruleMeaning(rule!, format)).toBe('Highlighted: below 3')
    expect(matchedRule(formats, 'units', 7)).toBeUndefined()
    expect(matchedRule(formats, 'revenue', 2)).toBeUndefined()
    const words = (['gt', 'gte', 'lt', 'lte', 'eq'] as const).map(operator =>
      ruleMeaning({ field_id: 'units', kind: 'rule', operator, value: { type: 'decimal', value: '1200.5' } }, format))
    expect(words).toEqual(['Highlighted: above 1,200.5', 'Highlighted: at least 1,200.5', 'Highlighted: below 1,200.5',
      'Highlighted: at most 1,200.5', 'Highlighted: equal to 1,200.5'])
  })

  it('leaves other measures, empty cells and charts without formats alone', () => {
    expect(cellStyle(formats, 'revenue', 5, { min: 0, max: 10 })).toBeUndefined()
    expect(cellStyle(formats, 'units', null, { min: 0, max: 10 })).toBeUndefined()
    expect(cellStyle(undefined, 'units', 5)).toBeUndefined()
  })
})

describe('drill-down', () => {
  const bar = {
    schema_version: 2, chart_id: 'c', title: 'Revenue by region', dataset_id: 'd', dataset_revision: 3, visual: 'bar',
    encoding: { kind: 'cartesian', dimensions: [{ field_id: 'region' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }] },
    sort: [{ field_id: 'region', direction: 'asc' }],
  } as unknown as ChartDefinition
  const field = (field_id: string, logical_type: DatasetField['logical_type'], semantic_role: DatasetField['semantic_role'] = 'dimension') =>
    ({ field_id, name: field_id, source_column: field_id, logical_type, physical_type: logical_type, semantic_role, nullable: true }) as DatasetField
  const fields = [field('region', 'string'), field('channel', 'string'), field('order_date', 'date'), field('revenue', 'decimal', 'measure')]

  it('offers other groupings, never the current one or a measure', () => {
    expect(drillOptions(bar, fields).map(option => option.label)).toEqual(['Channel', 'Order date'])
  })

  it('offers a finer date level first for a date grouping', () => {
    const byMonth = { ...bar, encoding: { ...bar.encoding, dimensions: [{ field_id: 'order_date', date_grain: 'month' }] } } as ChartDefinition
    expect(drillOptions(byMonth, fields)[0]).toEqual({ field_id: 'order_date', date_grain: 'week', label: 'Order date by week' })
  })

  it('regroups the chart and narrows it to every clicked value, on top of dashboard filters', () => {
    const steps = [{ field_id: 'region', value: s('Jakarta'), label: 'Jakarta', by: { field_id: 'channel' } }]
    const drilled = drilledChart(bar, steps)
    expect(drilled.encoding).toMatchObject({ dimensions: [{ field_id: 'channel' }] })
    expect(drilled.sort).toEqual([])
    const base = { expression: { type: 'predicate', field: { dataset_id: 'd', dataset_revision: 3, field_id: 'channel' }, op: 'eq', values: [s('Online')] } }
    const context = drillContext(bar, steps, base as never) as { expression: { type: string; children: { op: string; values: unknown[] }[] } }
    expect(context.expression.type).toBe('and')
    expect(context.expression.children.map(child => child.op)).toEqual(['eq', 'eq'])
    expect(context.expression.children[1].values).toEqual([s('Jakarta')])
  })

  it('narrows a date mark to its whole period', () => {
    const steps = [{ field_id: 'order_date', value: { type: 'date' as const, value: '2026-08-01' }, label: 'Aug 2026',
      period: { from: '2026-08-01', to: '2026-09-01' }, by: { field_id: 'channel' } }]
    const context = drillContext(bar, steps) as { expression: { type: string; children: { op: string; values: { value: string }[] }[] } }
    expect(context.expression.type).toBe('and')
    expect(context.expression.children.map(child => [child.op, child.values[0].value])).toEqual([['gte', '2026-08-01'], ['lt', '2026-09-01']])
  })

  it('has nothing to drill for a chart with no single grouping', () => {
    const split = { ...bar, encoding: { ...bar.encoding, color: { field_id: 'channel' } } } as ChartDefinition
    expect(drillOptions(split, fields)).toEqual([])
  })
})
