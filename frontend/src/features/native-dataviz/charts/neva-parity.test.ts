import { describe, expect, it } from 'vitest'
import type { DatasetField } from '../contracts'
import parity from './__fixtures__/neva-parity.json'
import { initialState, switchVisual, toChart, type BuilderMeasure } from './builder-model'

// NDV-AGENT-001: the backend checks that Neva's build_chart saves these same charts.
const fields = parity.dataset_fields.map(field => ({ ...field, source_column: field.field_id,
  physical_type: field.logical_type.toUpperCase(), nullable: field.field_id !== 'order_id' })) as DatasetField[]
const base = { chart_id: '44444444-4444-4444-8444-444444444444', dataset_id: '33333333-3333-4333-8333-333333333333', dataset_revision: 1, fields }
const revenue = (series: BuilderMeasure['series']): BuilderMeasure[] => [{ field_id: 'revenue', aggregation: 'sum', series, axis: 'left' }]

describe('charts Neva and the chart builder save alike', () => {
  it('a bar chart sorted by its measure', () => {
    expect(toChart({ ...initialState(base), title: 'Revenue by city', measures: revenue('bar'), sort: 'desc' })).toEqual(parity.manual.bar)
  })

  it('a weekly line chart', () => {
    const line = switchVisual({ ...initialState(base), title: 'Revenue by week' }, 'line', fields)
    expect(toChart({ ...line, dimensions: [{ field_id: 'order_date', date_grain: 'week' }], measures: revenue('line') })).toEqual(parity.manual.line)
  })

  it('a KPI', () => {
    const kpi = switchVisual({ ...initialState(base), title: 'Revenue' }, 'kpi', fields)
    expect(toChart({ ...kpi, measures: revenue('bar') })).toEqual(parity.manual.kpi)
  })
})
