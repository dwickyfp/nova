import { describe, expect, it } from 'vitest'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { buildChartOption, OTHER } from './chart-options'
import { combinesOther, remainderOf } from './chart-data'
import { chartQueryShape } from './chart-query'
import type { VizTokens } from './echart-host'

// Release QA for native chart options (docs/reports/native-dataviz/QA_MATRIX.csv).

const DATASET = 'aaaaaaaa-0000-4000-8000-0000000000a1'
const tokens: VizTokens = { series: ['s1', 's2', 's3', 's4'], tones: ['t1', 't2'], mark: 'mark', highlight: 'hi', text: 'text',
  muted: 'muted', grid: 'grid', axis: 'axis', surface: 'surface', reference: 'ref' }

type Column = DatasetQueryResult['columns'][number]
type Row = DatasetQueryResult['rows'][number]
const dim = (key: string, field_id: string, logical_type = 'string'): Column =>
  ({ key, name: field_id, logical_type, role: 'dimension', field_id } as Column)
const meas = (key: string, field_id: string, logical_type = 'decimal'): Column =>
  ({ key, name: field_id, logical_type, role: 'measure', field_id, aggregation: 'sum' } as Column)
const s = (value: string) => ({ type: 'string' as const, value })
const d = (value: string | null) => value === null ? null : ({ type: 'decimal' as const, value })
const result = (columns: Column[], rows: Row[]): DatasetQueryResult => ({ dataset_id: DATASET, revision: 1, columns, rows, truncated: false })
const chart = (visual: ChartDefinition['visual'], encoding: unknown, extra: Partial<ChartDefinition> = {}) =>
  ({ schema_version: 2, chart_id: 'c1', title: 'QA', dataset_id: DATASET, dataset_revision: 1, visual, encoding, ...extra } as ChartDefinition)

type Axis = { type: string; name?: string; data?: string[]; splitLine?: { show?: boolean }; axisLabel: { formatter?: (value: number) => string } }
type Series = { name: string; type: string; data: (number | null)[]; stack?: string; xAxisIndex?: number; yAxisIndex?: number; label: { position: string } }
type Option = { xAxis: Axis | Axis[]; yAxis: Axis | Axis[]; series: Series[]; tooltip: { axisPointer?: { type: string } } }

describe('NDV-CHART-005 horizontal bar', () => {
  it('puts the measure on a value x-axis and the categories on y, with no unit on the category axis', () => {
    const horizontal = chart('bar', {
      kind: 'cartesian', dimensions: [{ field_id: 'region' }], orientation: 'horizontal',
      measures: [{ field_id: 'revenue', aggregation: 'sum', format: { kind: 'currency', currency: 'IDR', decimals: 0 } }],
    }, { left_axis: { title: 'Revenue (IDR)' } } as Partial<ChartDefinition>)
    const data = result([dim('d0', 'region'), meas('m0', 'revenue')],
      [[s('Jakarta'), d('1500000')], [s('Medan'), d('250000')]])
    const option = buildChartOption(horizontal, data, tokens, null, 400) as Option

    const category = option.yAxis as Axis
    expect(category.type).toBe('category')
    expect(category.data).toEqual(['Jakarta', 'Medan'])
    // The unit title belongs to the value axis, never the category axis.
    expect(category.name).toBeUndefined()
    expect(category.axisLabel.formatter).toBeUndefined()

    const values = option.xAxis as Axis[]
    expect(values).toHaveLength(1)
    expect(values[0].type).toBe('value')
    expect(values[0].name).toBe('Revenue (IDR)')
    // Tick labels are plain numbers: no other currency or percent unit is invented.
    const tick = values[0].axisLabel.formatter!(1_500_000)
    expect(tick).toMatch(/^1[.,]5\s?M$/)
    expect(tick).not.toMatch(/[%$€£¥]|Rp|IDR/)

    expect(option.series[0]).toMatchObject({ type: 'bar', data: [1_500_000, 250_000], xAxisIndex: 0, label: { position: 'right' } })
    expect(option.series[0].yAxisIndex).toBeUndefined()
    expect(option.tooltip.axisPointer?.type).toBe('shadow')
  })
})

describe('NDV-CHART-008 100% stacked bar', () => {
  it('gives finite shares for normal, zero-total, negative and missing categories', () => {
    const percent = chart('bar', {
      kind: 'cartesian', dimensions: [{ field_id: 'region' }], color: { field_id: 'channel' }, stack: 'percent',
      measures: [{ field_id: 'revenue', aggregation: 'sum' }],
    })
    const rows: Row[] = [
      [s('North'), s('Online'), d('30')], [s('North'), s('Retail'), d('70')],
      [s('Zero'), s('Online'), d('0')], [s('Zero'), s('Retail'), d('0')],
      [s('Mixed'), s('Online'), d('-50')], [s('Mixed'), s('Retail'), d('150')],
      [s('Partial'), s('Online'), d(null)], [s('Partial'), s('Retail'), d('40')],
    ]
    const option = buildChartOption(percent, result([dim('d0', 'region'), dim('d1', 'channel'), meas('m0', 'revenue')], rows), tokens) as Option
    const [online, retail] = option.series
    expect(online.name).toBe('Online')
    expect(retail.name).toBe('Retail')
    for (const series of option.series) {
      expect(series.stack).toBe('total')
      for (const value of series.data) expect(value === null || Number.isFinite(value)).toBe(true)
    }
    // North: plain shares.
    expect(online.data[0]).toBeCloseTo(30)
    expect(retail.data[0]).toBeCloseTo(70)
    // Zero total: no division, values stay 0 rather than NaN.
    expect(online.data[1]).toBe(0)
    expect(retail.data[1]).toBe(0)
    // Negative value: shares of the absolute total, whose magnitudes add up to 100.
    expect(online.data[2]).toBeCloseTo(-25)
    expect(retail.data[2]).toBeCloseTo(75)
    expect(Math.abs(online.data[2]!) + Math.abs(retail.data[2]!)).toBeCloseTo(100)
    // A missing value stays missing; the rest is 100%.
    expect(online.data[3]).toBeNull()
    expect(retail.data[3]).toBeCloseTo(100)
    const axis = (option.yAxis as Axis[])[0]
    expect(axis.axisLabel.formatter!(50)).toBe('50%')
  })
})

describe('NDV-CHART-010 scatter', () => {
  const scatter = chart('scatter', { kind: 'scatter', x: 'price', y: 'units' })

  it('queries both quantitative fields as raw values with no aggregation', () => {
    const shape = chartQueryShape(scatter)
    expect(shape.dimensions).toEqual([{ field_id: 'price' }, { field_id: 'units' }])
    expect(shape.measures).toEqual([])
  })

  it('draws one point per returned row with exact values', () => {
    const rows: Row[] = [[d('10'), d('3')], [d('10'), d('5')], [d('12.5'), d('3')], [d('40'), d('1')]]
    const option = buildChartOption(scatter, result([dim('d0', 'price', 'decimal'), dim('d1', 'units', 'decimal')], rows), tokens) as Option
    expect(option.series).toHaveLength(1)
    expect(option.series[0].type).toBe('scatter')
    expect((option.series[0].data as unknown as number[][]).map(point => point.slice(0, 2)))
      .toEqual([[10, 3], [10, 5], [12.5, 3], [40, 1]])
    expect((option.xAxis as Axis).type).toBe('value')
    expect((option.yAxis as Axis).type).toBe('value')
  })
})

describe('NDV-CHART-011 combo with two units', () => {
  it('draws each measure on its own titled value axis with its own formatter', () => {
    const combo = chart('combo', {
      kind: 'cartesian', dimensions: [{ field_id: 'region' }],
      measures: [
        { field_id: 'revenue', aggregation: 'sum', series: 'bar', axis: 'left', format: { kind: 'currency', currency: 'IDR' } },
        { field_id: 'units', aggregation: 'sum', series: 'line', axis: 'right', format: { kind: 'number', decimals: 0 } },
      ],
    }, { left_axis: { title: 'Revenue (IDR)' }, right_axis: { title: 'Units' } } as Partial<ChartDefinition>)
    const data = result([dim('d0', 'region'), meas('m0', 'revenue'), meas('m1', 'units', 'integer')],
      [[s('Jakarta'), d('125000000'), { type: 'integer', value: '420' }], [s('Medan'), d('98000000'), { type: 'integer', value: '310' }]])
    const option = buildChartOption(combo, data, tokens) as Option
    const axes = option.yAxis as Axis[]
    expect(axes).toHaveLength(2)
    expect(axes.map(axis => [axis.type, axis.name])).toEqual([['value', 'Revenue (IDR)'], ['value', 'Units']])
    expect(axes[1].splitLine?.show).toBe(false)
    expect(typeof axes[0].axisLabel.formatter).toBe('function')
    expect(typeof axes[1].axisLabel.formatter).toBe('function')
    expect(axes[1].axisLabel.formatter!(420)).toBe('420')
    expect(option.series.map(series => [series.name, series.type, series.yAxisIndex])).toEqual([
      ['Sum of revenue', 'bar', 0], ['Sum of units', 'line', 1],
    ])
    expect(option.series[0].data).toEqual([125_000_000, 98_000_000])
    expect(option.series[1].data).toEqual([420, 310])
  })
})

describe('NDV-CHART-006 monthly line with a missing month', () => {
  const month = (value: string) => ({ type: 'date' as const, value })
  const column = { ...dim('d0', 'month', 'date'), date_grain: 'month' } as Column
  const line = (nulls: 'gap' | 'zero') => chart('line', {
    kind: 'cartesian', dimensions: [{ field_id: 'month', date_grain: 'month' }], nulls,
    measures: [{ field_id: 'revenue', aggregation: 'sum' }] })
  const rows: Row[] = [[month('2024-01-01'), d('1')], [month('2024-02-01'), d('2')], [month('2024-04-01'), d('4')]]

  it('keeps every month on the axis in order and leaves the missing one as a gap', () => {
    const option = buildChartOption(line('gap'), result([column, meas('m0', 'revenue')], rows), tokens) as Option
    expect((option.xAxis as Axis).data).toHaveLength(4)
    expect(option.series[0].data).toEqual([1, 2, null, 4])
  })

  it('draws the missing month as zero only when the chart asks for it', () => {
    const option = buildChartOption(line('zero'), result([column, meas('m0', 'revenue')], rows), tokens) as Option
    expect(option.series[0].data).toEqual([1, 2, 0, 4])
  })

  it('does not reorder or fill a chart sorted by its measure', () => {
    const sorted: Row[] = [rows[2], rows[0], rows[1]]
    const option = buildChartOption(line('gap'), result([column, meas('m0', 'revenue')], sorted), tokens) as Option
    expect(option.series[0].data).toEqual([4, 1, 2])
  })
})

describe('NDV-CHART-007 series colours', () => {
  const split = chart('bar', { kind: 'cartesian', dimensions: [{ field_id: 'month' }], color: { field_id: 'region' },
    measures: [{ field_id: 'revenue', aggregation: 'sum' }] }, { style: { palette: 'categorical' } } as Partial<ChartDefinition>)
  const columns = [dim('d0', 'month'), dim('d1', 'region'), meas('m0', 'revenue')]
  const colorOf = (regions: string[]) => {
    const option = buildChartOption(split, result(columns, regions.map(region => [s('Jan'), s(region), d('1')])), tokens) as
      Option & { color: string[] }
    return Object.fromEntries(option.series.map((item, index) => [item.name, option.color[index]]))
  }

  it('keeps each region on its own colour when another region is filtered out', () => {
    const all = colorOf(['East', 'North', 'West'])
    expect(new Set(Object.values(all)).size).toBe(3)
    expect(colorOf(['East', 'West'])).toEqual({ East: all.East, West: all.West })
    expect(colorOf(['West'])).toEqual({ West: all.West })
  })
})

describe('NDV-CHART-009 pie with more values than slices', () => {
  type Pie = { series: { type: string; data: { name: string; value: number | null; itemStyle?: { color?: string } }[]; label: { formatter: string } }[] }
  const pie = (aggregation: string, overflow: 'other' | 'error' = 'other') => chart('pie', {
    kind: 'category', category: { field_id: 'city' }, measure: { field_id: 'revenue', aggregation }, max_categories: 3, overflow,
  })
  const top3 = { ...result([dim('d0', 'city'), meas('m0', 'revenue')],
    [[s('Jakarta'), d('500')], [s('Bandung'), d('300')], [s('Medan'), d('100')]]), truncated: true }
  const whole = result([meas('m0', 'revenue')], [[d('1000')]])

  it('adds the rest as one Other slice worth exactly the total less the shown slices', () => {
    expect(combinesOther(pie('sum'))).toBe(true)
    expect(remainderOf(top3, whole)).toBe(100)
    const option = buildChartOption(pie('sum'), top3, tokens, null, 400, remainderOf(top3, whole)) as Pie
    const data = option.series[0].data
    expect(data.map(item => [item.name, item.value])).toEqual([['Jakarta', 500], ['Bandung', 300], ['Medan', 100], [OTHER, 100]])
    expect(data.reduce((sum, item) => sum + (item.value ?? 0), 0)).toBe(1000)
    expect(data[3].itemStyle?.color).toBe('mark')
    // Shares are of the real whole, so they can be shown.
    expect(option.series[0].label.formatter).toBe('{b}: {d}%')
  })

  it('shows names without shares while the whole is unknown or cannot be split', () => {
    const waiting = buildChartOption(pie('sum'), top3, tokens, null, 400, null) as Pie
    expect(waiting.series[0].data).toHaveLength(3)
    expect(waiting.series[0].label.formatter).toBe('{b}')
    // An average of the rest is not the rest of an average; a min or count of distinct values neither.
    for (const aggregation of ['avg', 'min', 'max', 'count_distinct']) expect(combinesOther(pie(aggregation))).toBe(false)
    expect(combinesOther(pie('sum', 'error'))).toBe(false)
  })

  it('keeps shares when nothing was left out, and adds no Other', () => {
    const complete = { ...top3, truncated: false }
    const option = buildChartOption(pie('sum'), complete, tokens, null, 400, 50) as Pie
    expect(option.series[0].data.map(item => item.name)).toEqual(['Jakarta', 'Bandung', 'Medan'])
    expect(option.series[0].label.formatter).toBe('{b}: {d}%')
  })

  it('never merges the rest with a real value named Other', () => {
    const named = { ...top3, rows: [[s('Other'), d('500')], ...top3.rows.slice(1)] }
    const option = buildChartOption(pie('sum'), named, tokens, null, 400, 100) as Pie
    expect(option.series[0].data.map(item => item.name)).toEqual(['Other', 'Bandung', 'Medan', 'Other (rest)'])
  })

  it('has no Other when the shown slices already make up the whole', () => {
    expect(remainderOf(top3, result([meas('m0', 'revenue')], [[d('900')]]))).toBeNull()
    expect(remainderOf(top3, undefined)).toBeNull()
  })
})

describe('Studio answer charts: numeric identifiers', () => {
  it('draws one band per customer id and a value axis sized by the plotted series', () => {
    // Top customers by revenue keyed by a numeric customer_id: each id is a band,
    // not a point on a 0-24 linear scale, and nothing is stacked or summed.
    const top = chart('bar', {
      kind: 'cartesian', dimensions: [{ field_id: 'customer_id' }],
      measures: [{ field_id: 'revenue', aggregation: 'sum' }],
    })
    const i = (value: string) => ({ type: 'integer' as const, value })
    const data = result([dim('d0', 'customer_id', 'integer'), meas('m0', 'revenue')], [
      [i('7'), d('31500000')], [i('12'), d('28000000')], [i('24'), d('27400000')],
      [i('3'), d('26100000')], [i('18'), d('25900000')],
    ] as Row[])
    const option = buildChartOption(top, data, tokens, null, 560) as Option

    const category = option.xAxis as Axis
    expect(category.type).toBe('category')
    expect(category.data).toEqual(['7', '12', '24', '3', '18'])
    const value = (option.yAxis as Axis[])[0] as Axis & { max?: unknown; min?: unknown }
    expect(value.type).toBe('value')
    // No fixed bound: ECharts rounds the domain up from the plotted maximum.
    expect(value.max).toBeUndefined()
    expect(value.min).toBeUndefined()
    expect(option.series).toHaveLength(1)
    expect(option.series[0].type).toBe('bar')
    expect(option.series[0].stack).toBeUndefined()
    expect(Math.max(...option.series[0].data.map(Number))).toBe(31_500_000)
  })
})
