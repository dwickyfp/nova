import { describe, expect, it } from 'vitest'
import type { ChartDefinition, DatasetField, DatasetQueryResult } from '../contracts'
import { fromChart, initialState, toChart } from './builder-model'
import { applyCalculations, type CalculationKind } from './calculations'

const columns = [
  { key: 'd0', name: 'month', logical_type: 'date', role: 'dimension', field_id: 'month', date_grain: 'month' },
  { key: 'd1', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
  { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' },
] as DatasetQueryResult['columns']
const d = (value: string) => ({ type: 'date' as const, value })
const s = (value: string) => ({ type: 'string' as const, value })
const n = (value: number | null) => value === null ? null : { type: 'decimal' as const, value: String(value) }

// Rows arrive in any order; months are deliberately shuffled.
const rows: DatasetQueryResult['rows'] = [
  [d('2026-02-01'), s('Jakarta'), n(20)], [d('2026-01-01'), s('Jakarta'), n(10)], [d('2026-03-01'), s('Jakarta'), n(null)],
  [d('2026-04-01'), s('Jakarta'), n(40)], [d('2026-01-01'), s('Bandung'), n(5)], [d('2026-02-01'), s('Bandung'), n(5)],
]
const result = (): DatasetQueryResult => ({ dataset_id: 'ds', revision: 1, columns, rows, truncated: false })

function chart(kind: CalculationKind, extra: Record<string, unknown> = {}) {
  return { table_calculations: [{
    calculation_id: 'c1', name: 'c', kind, measure: { field_id: 'revenue', aggregation: 'sum' },
    addressing: ['month'], partition_by: ['region'], order: [{ field_id: 'month', direction: 'asc' }],
    nulls: 'preserve', ...extra,
  }] } as unknown as ChartDefinition
}

const valuesFor = (output: DatasetQueryResult, region: string) => output.rows
  .filter(row => row[1]?.value === region)
  .sort((a, b) => String(a[0]?.value).localeCompare(String(b[0]?.value)))
  .map(row => row[2] === null ? null : Number(row[2].value))

describe('table calculations', () => {
  it('runs a total along months, separately per region, skipping a missing month', () => {
    const output = applyCalculations(chart('running_total'), result())
    expect(valuesFor(output, 'Jakarta')).toEqual([10, 30, null, 70])
    expect(valuesFor(output, 'Bandung')).toEqual([5, 10])
    expect(output.columns[2]).toMatchObject({ name: 'Running total of revenue', aggregation: null, logical_type: 'float' })
  })

  it('counts a missing value as zero only when asked', () => {
    const output = applyCalculations(chart('running_total', { nulls: 'zero' }), result())
    expect(valuesFor(output, 'Jakarta')).toEqual([10, 30, 30, 70])
  })

  it('divides by the total of each region as shown', () => {
    const output = applyCalculations(chart('percent_of_total'), result())
    expect(valuesFor(output, 'Jakarta')).toEqual([10 / 70 * 100, 20 / 70 * 100, null, 40 / 70 * 100].map(v => v === null ? null : Math.round(v * 1e9) / 1e9))
    expect(valuesFor(output, 'Bandung')).toEqual([50, 50])
    expect(output.columns[2].name).toBe('Percent of total of revenue (%)')
  })

  it('compares each month with the previous one; the first has nothing to compare', () => {
    expect(valuesFor(applyCalculations(chart('difference'), result()), 'Jakarta')).toEqual([null, 10, null, null])
    expect(valuesFor(applyCalculations(chart('percent_difference'), result()), 'Jakarta')).toEqual([null, 100, null, null])
  })

  it('shares a rank between ties: 1, 1, 3 and dense 1, 1, 2', () => {
    const tied = { ...result(), rows: [
      [d('2026-01-01'), s('A'), n(7)], [d('2026-02-01'), s('A'), n(9)], [d('2026-03-01'), s('A'), n(9)], [d('2026-04-01'), s('A'), n(3)],
    ] }
    expect(valuesFor(applyCalculations(chart('rank'), tied), 'A')).toEqual([3, 1, 1, 4])
    expect(valuesFor(applyCalculations(chart('dense_rank'), tied), 'A')).toEqual([2, 1, 1, 3])
  })

  it('averages the previous rows in order, over the values that exist', () => {
    const output = applyCalculations(chart('moving_average', { window_size: 2 }), result())
    expect(valuesFor(output, 'Jakarta')).toEqual([10, 15, null, 40])
    expect(output.columns[2].name).toBe('Moving average (2) of revenue')
  })

  it('leaves a result without calculations untouched', () => {
    const input = result()
    expect(applyCalculations({ table_calculations: [] }, input)).toBe(input)
  })
})

describe('Show as in the builder', () => {
  const field = (field_id: string, logical_type: DatasetField['logical_type']) =>
    ({ field_id, name: field_id, source_column: field_id, logical_type, physical_type: logical_type, semantic_role: 'dimension', nullable: true }) as DatasetField
  const fields = [field('month', 'date'), field('region', 'string'), field('revenue', 'decimal')]

  it('saves a running total along the first grouping, per series, and reads it back', () => {
    const state = { ...initialState({ chart_id: 'c', dataset_id: 'ds', dataset_revision: 1, fields }), title: 'Revenue',
      dimensions: [{ field_id: 'month', date_grain: 'month' as const }], color: { field_id: 'region', date_grain: null },
      measures: [{ field_id: 'revenue', aggregation: 'sum' as const, series: 'line' as const, axis: 'left' as const, showAs: 'running_total' as const }] }
    const saved = toChart(state)
    expect(saved.table_calculations).toEqual([expect.objectContaining({
      kind: 'running_total', addressing: ['month'], partition_by: ['region'], order: [{ field_id: 'month', direction: 'asc' }],
      measure: expect.objectContaining({ field_id: 'revenue', aggregation: 'sum' }) })])
    expect(fromChart(saved, fields).measures[0].showAs).toBe('running_total')
    const plain = toChart({ ...state, measures: [{ ...state.measures[0], showAs: null }] })
    expect(plain.table_calculations).toBeUndefined()
  })
})
