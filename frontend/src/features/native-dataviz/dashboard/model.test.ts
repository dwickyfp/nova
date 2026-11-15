import { describe, expect, it, vi } from 'vitest'
import type { CartesianEncoding, ChartDefinition, DashboardDefinition, DatasetField, DatasetQueryResult } from '../contracts'
import { fromChart, initialState, missing, switchVisual, toChart } from '../charts/builder-model'
import { buildChartOption } from '../charts/chart-options'
import { cellLabel, chartQueryShape, ChartQueryError, columnLabel, combineFilters, formatNumber, hasSplit, humanize, periodEnd } from '../charts/chart-query'
import type { VizTokens } from '../charts/echart-host'
import { capacityRetry, MAX_CONCURRENT_QUERIES, queuedQuery } from '../charts/query-queue'
import { chartContext, defaultValue, targetBinding, type FilterDefinition } from './filter-model'
import { firstFreeSlot, validPlacements, type Widget } from './layout'
import { applyLocal, inverse, sameLayout } from './operations'

const DATASET = 'aaaaaaaa-0000-4000-8000-000000000001'
const OTHER = 'aaaaaaaa-0000-4000-8000-000000000002'
const field = (field_id: string, logical_type: DatasetField['logical_type'], semantic_role: DatasetField['semantic_role'] = 'dimension'): DatasetField =>
  ({ field_id, name: field_id, source_column: field_id, logical_type, physical_type: logical_type === 'decimal' ? 'DECIMAL(18,2)' : 'VARCHAR(32)', semantic_role, nullable: true })
const FIELDS = [field('region', 'string'), field('day', 'date'), field('revenue', 'decimal', 'measure'), field('units', 'integer', 'measure')]
const tokens: VizTokens = { series: ['s1', 's2', 's3'], tones: ['t1', 't2'], mark: 'mark', highlight: 'hi', text: 'text', muted: 'muted', grid: 'grid', axis: 'axis', surface: 'surface', reference: 'ref' }

function chart(overrides: Partial<ChartDefinition> = {}): ChartDefinition {
  return toChart({ ...initialState({ chart_id: 'c1', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'Revenue', ...overrides } as never) as ChartDefinition
}

describe('chart query shape', () => {
  it('maps a cartesian chart with a split, sort and limit to field IDs only', () => {
    const state = { ...initialState({ chart_id: 'c1', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'R',
      dimensions: [{ field_id: 'day', date_grain: 'month' as const }], color: { field_id: 'region', date_grain: null },
      measures: [{ field_id: 'revenue', aggregation: 'sum' as const, series: 'bar' as const, axis: 'left' as const }], sort: 'desc' as const, rowLimit: 500 }
    expect(chartQueryShape(toChart(state))).toEqual({
      dimensions: [{ field_id: 'day', date_grain: 'month' }, { field_id: 'region' }],
      measures: [{ field_id: 'revenue', aggregation: 'sum' }], sort: [{ column: 'm0', direction: 'desc' }], limit: 500,
    })
  })

  it('ignores a color split that repeats the axis field', () => {
    const definition = chart()
    const encoding = { ...(definition.encoding as CartesianEncoding), color: { field_id: 'region' } }
    expect(hasSplit(encoding as never)).toBe(false)
    expect(chartQueryShape({ ...definition, encoding } as ChartDefinition).dimensions).toEqual([{ field_id: 'region' }])
  })

  it('sorts category charts by value and caps them at the category limit', () => {
    const state = switchVisual({ ...initialState({ chart_id: 'c1', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'P', maxCategories: 7 }, 'pie', FIELDS)
    expect(chartQueryShape(toChart(state))).toMatchObject({ sort: [{ column: 'm0', direction: 'desc' }], limit: 7 })
  })

  it('refuses aggregations native charts cannot run', () => {
    const definition = chart()
    const encoding = definition.encoding as CartesianEncoding
    expect(() => chartQueryShape({ ...definition, encoding: { ...encoding, measures: [{ ...encoding.measures[0], aggregation: 'median' }] } } as ChartDefinition))
      .toThrow(ChartQueryError)
  })

  it('combines chart and dashboard filters with AND', () => {
    const a = { expression: { type: 'predicate', op: 'is_null', field: { dataset_id: DATASET, dataset_revision: 3, field_id: 'region' } } } as never
    const b = { expression: { type: 'predicate', op: 'is_not_null', field: { dataset_id: DATASET, dataset_revision: 3, field_id: 'day' } } } as never
    expect(combineFilters(null, undefined)).toBeUndefined()
    expect(combineFilters(a)).toEqual(a)
    expect(combineFilters(a, b)?.expression).toMatchObject({ type: 'and', children: [{ op: 'is_null' }, { op: 'is_not_null' }] })
  })

  it('labels cells exactly and formats numbers without inventing values', () => {
    expect(cellLabel({ type: 'date', value: '2026-04-01' }, { date_grain: 'quarter' })).toBe('Q2 2026')
    expect(cellLabel({ type: 'decimal', value: '12.50' })).toBe('12.50')
    expect(cellLabel(null)).toBe('(empty)')
    expect(formatNumber(null)).toBe('–')
  })
})

describe('chart builder model', () => {
  it('round-trips a saved chart and reports what is missing in plain words', () => {
    const state = { ...initialState({ chart_id: 'c1', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'Revenue by region',
      dimensions: [{ field_id: 'region', date_grain: null }], number: { kind: 'currency' as const, decimals: 0, currency: 'IDR' } }
    const saved = toChart(state)
    const reopened = fromChart(saved, FIELDS)
    expect(toChart(reopened)).toEqual(saved)
    expect(missing({ ...state, title: '' })).toBe('Name the chart')
    expect(missing({ ...state, dimensions: [] })).toBe('Choose a field to group by')
    expect(missing(switchVisual({ ...state, x: '', y: '' }, 'scatter', [field('region', 'string')]))).toBe('Choose numeric fields for both axes')
  })

  it('keeps compatible fields when switching visualization', () => {
    const state = { ...initialState({ chart_id: 'c1', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'R' }
    const combo = switchVisual(state, 'combo', FIELDS)
    expect(combo.measures).toHaveLength(2)
    expect(combo.measures[1]).toMatchObject({ series: 'line', axis: 'right' })
    const heatmap = switchVisual(state, 'heatmap', FIELDS)
    expect(heatmap.dimensions.map(item => item.field_id)).toEqual(['region', 'day'])
    expect(toChart(heatmap).encoding).toMatchObject({ kind: 'heatmap' })
  })
})

describe('dashboard filters', () => {
  const filter = (overrides: Partial<FilterDefinition> = {}): FilterDefinition => ({
    filter_id: 'f1', title: 'Region', kind: 'multi_value', field: { dataset_id: DATASET, dataset_revision: 3, field_id: 'region' },
    scope: { kind: 'global' }, mappings: [], defaults: [], allow_all: true, include_null: false, searchable: true, depends_on: [], ...overrides,
  } as FilterDefinition)
  const definition = (filters: FilterDefinition[]) => ({ dashboard_id: 'd', title: 'D', pages: [], filters } as unknown as DashboardDefinition)
  const target = { dataset_id: DATASET, dataset_revision: 3 } as ChartDefinition

  it('applies only to charts of the same dataset revision or an explicit mapping', () => {
    expect(targetBinding(filter(), target)).toMatchObject({ field_id: 'region' })
    expect(targetBinding(filter(), { dataset_id: OTHER, dataset_revision: 1 } as ChartDefinition)).toBeNull()
    const mapped = filter({ mappings: [{ source: filter().field!, target: { dataset_id: OTHER, dataset_revision: 1, field_id: 'area' }, transform: 'identity' }] })
    expect(targetBinding(mapped, { dataset_id: OTHER, dataset_revision: 1 } as ChartDefinition)).toMatchObject({ field_id: 'area' })
  })

  it('builds typed predicates, includes NULL explicitly and leaves "All" unfiltered', () => {
    const values = { f1: { kind: 'members' as const, values: [{ type: 'string' as const, value: 'Jakarta' }, null] } }
    const tree = chartContext({ definition: definition([filter()]), chart: target, widgetId: 'w1', pageId: 'p1', values, crossFilters: [] })
    expect(tree?.expression).toMatchObject({ type: 'or', children: [{ op: 'eq', values: [{ value: 'Jakarta' }] }, { op: 'is_null' }] })
    expect(chartContext({ definition: definition([filter()]), chart: target, widgetId: 'w1', pageId: 'p1', values: { f1: { kind: 'members', values: [] } }, crossFilters: [] })).toBeUndefined()
  })

  it('respects page and widget scope and never cross-filters the source widget', () => {
    const scoped = filter({ scope: { kind: 'page', page_id: 'p2' } })
    const values = { f1: { kind: 'text' as const, value: 'jak' } }
    expect(chartContext({ definition: definition([scoped]), chart: target, widgetId: 'w1', pageId: 'p1', values, crossFilters: [] })).toBeUndefined()
    expect(chartContext({ definition: definition([scoped]), chart: target, widgetId: 'w1', pageId: 'p2', values, crossFilters: [] })?.expression)
      .toMatchObject({ op: 'like_literal' })
    const cross = [{ source_widget_id: 'w1', field: { dataset_id: DATASET, dataset_revision: 3, field_id: 'region' }, value: { type: 'string' as const, value: 'Medan' }, label: 'Medan' }]
    expect(chartContext({ definition: definition([]), chart: target, widgetId: 'w1', pageId: 'p1', values: {}, crossFilters: cross })).toBeUndefined()
    expect(chartContext({ definition: definition([]), chart: target, widgetId: 'w2', pageId: 'p1', values: {}, crossFilters: cross })?.expression)
      .toMatchObject({ op: 'eq', values: [{ value: 'Medan' }] })
  })

  it('includes the whole end day of a date range, across month and leap-year ends', () => {
    const tree = chartContext({ definition: definition([filter({ kind: 'date_range' })]), chart: target, widgetId: 'w1', pageId: 'p1',
      values: { f1: { kind: 'range', from: { type: 'date', value: '2024-02-01' }, to: { type: 'date', value: '2024-02-29' } } }, crossFilters: [] })
    expect(tree?.expression).toMatchObject({ type: 'and', children: [
      { op: 'gte', values: [{ type: 'date', value: '2024-02-01' }] },
      { op: 'lt', values: [{ type: 'date', value: '2024-03-01' }] }] })
    const open = chartContext({ definition: definition([filter({ kind: 'date_range' })]), chart: target, widgetId: 'w1', pageId: 'p1',
      values: { f1: { kind: 'range', from: null, to: { type: 'date', value: '2025-12-31' } } }, crossFilters: [] })
    expect(open?.expression).toMatchObject({ op: 'lt', values: [{ value: '2026-01-01' }] })
    const numbers = chartContext({ definition: definition([filter({ kind: 'number_range_slider' })]), chart: target, widgetId: 'w1', pageId: 'p1',
      values: { f1: { kind: 'range', from: { type: 'decimal', value: '1.5' }, to: { type: 'integer', value: '10' } } }, crossFilters: [] })
    expect(numbers?.expression).toMatchObject({ type: 'and', children: [{ op: 'gte' }, { op: 'lte', values: [{ value: '10' }] }] })
  })

  it('cross-filters a selected date-grain mark by its whole period', () => {
    const cross = [{ source_widget_id: 'trend', label: 'Feb 2024', value: { type: 'date' as const, value: '2024-02-01' },
      period: { from: '2024-02-01', to: periodEnd('2024-02-01', 'month') },
      field: { dataset_id: DATASET, dataset_revision: 3, field_id: 'day' } }]
    expect(periodEnd('2024-11-01', 'quarter')).toBe('2025-02-01')
    expect(periodEnd('2024-12-30', 'week')).toBe('2025-01-06')
    expect(chartContext({ definition: definition([]), chart: target, widgetId: 'kpi', pageId: 'p1', values: {}, crossFilters: cross })?.expression)
      .toMatchObject({ type: 'and', children: [{ op: 'gte', values: [{ value: '2024-02-01' }] }, { op: 'lt', values: [{ value: '2024-03-01' }] }] })
  })

  it('derives defaults for ranges and relative dates', () => {
    expect(defaultValue(filter({ kind: 'date_range', defaults: [{ type: 'date', value: '2026-01-01' }, { type: 'date', value: '2026-03-31' }] })))
      .toMatchObject({ kind: 'range', from: { value: '2026-01-01' }, to: { value: '2026-03-31' } })
    expect(defaultValue(filter({ kind: 'relative_date', relative: { unit: 'month', count: 1, mode: 'to_date', timezone: 'UTC', week_start: 'iso', fiscal_start_month: 1 } })))
      .toEqual({ kind: 'relative', unit: 'month', count: 1, mode: 'to_date' })
    expect(defaultValue(filter())).toBeUndefined()
  })
})

describe('layout and operations', () => {
  const widget = (id: string, x: number, y: number, w = 6, h = 4): Widget => ({ kind: 'text', widget_id: id, text: id, layout: { x, y, w, h } } as Widget)
  const dashboard = (widgets: Widget[]): DashboardDefinition =>
    ({ dashboard_id: 'd', title: 'Before', pages: [{ page_id: 'p', title: 'P', widgets }], filters: [] } as unknown as DashboardDefinition)

  it('places new widgets in the first free slot without overlap', () => {
    expect(firstFreeSlot([widget('a', 0, 0)], 6, 4)).toEqual({ x: 6, y: 0, w: 6, h: 4 })
    expect(firstFreeSlot([widget('a', 0, 0), widget('b', 6, 0)], 3, 2)).toEqual({ x: 0, y: 4, w: 3, h: 2 })
    expect(validPlacements([{ widget_id: 'a', layout: { x: 0, y: 0, w: 6, h: 4 } }, { widget_id: 'b', layout: { x: 4, y: 0, w: 6, h: 4 } }])).toBe(false)
  })

  it('computes exact inverses so undo restores the earlier document', () => {
    const before = dashboard([widget('a', 0, 0), widget('b', 6, 0)])
    const moves = [{ op: 'layout.apply' as const, page_id: 'p', placements: [{ widget_id: 'a', layout: { x: 0, y: 4, w: 6, h: 4 } }, { widget_id: 'b', layout: { x: 0, y: 0, w: 6, h: 4 } }] }]
    const after = moves.reduce(applyLocal, before)
    expect(sameLayout(after.pages[0], moves[0].placements)).toBe(true)
    expect(applyLocal(after, inverse(moves[0], before)!)).toEqual(before)
    const removal = { op: 'widget.remove' as const, page_id: 'p', widget_id: 'a' }
    expect(applyLocal(applyLocal(before, removal), inverse(removal, before)!).pages[0].widgets).toHaveLength(2)
    const rename = { op: 'dashboard.rename' as const, title: 'After' }
    expect(applyLocal(applyLocal(before, rename), inverse(rename, before)!).title).toBe('Before')
  })
})

describe('query queue', () => {
  it('bounds concurrency, skips aborted waiters and retries only capacity refusals', async () => {
    let active = 0
    let peak = 0
    const releases: (() => void)[] = []
    const run = vi.fn(() => new Promise<number>(resolve => {
      active += 1
      peak = Math.max(peak, active)
      releases.push(() => { active -= 1; resolve(active) })
    }))
    const controller = new AbortController()
    const promises = Array.from({ length: MAX_CONCURRENT_QUERIES + 2 }, (_, index) =>
      queuedQuery(run, index === MAX_CONCURRENT_QUERIES ? controller.signal : undefined))
    controller.abort()
    await expect(promises[MAX_CONCURRENT_QUERIES]).rejects.toBeDefined()
    expect(run).toHaveBeenCalledTimes(MAX_CONCURRENT_QUERIES)
    while (releases.length) { releases.shift()!(); await Promise.resolve(); await Promise.resolve() }
    await Promise.all(promises.filter((_, index) => index !== MAX_CONCURRENT_QUERIES))
    expect(run).toHaveBeenCalledTimes(MAX_CONCURRENT_QUERIES + 1)
    expect(peak).toBe(MAX_CONCURRENT_QUERIES)
    expect(capacityRetry(0, { status: 429 })).toBe(true)
    expect(capacityRetry(5, { status: 429 })).toBe(false)
    expect(capacityRetry(0, { status: 403 })).toBe(false)
  })
})

describe('chart options', () => {
  const result = (columns: DatasetQueryResult['columns'], rows: DatasetQueryResult['rows']): DatasetQueryResult =>
    ({ dataset_id: DATASET, revision: 3, columns, rows, truncated: false })
  const column = (key: string, logical_type: string, role: 'dimension' | 'measure') => ({ key, name: key, logical_type, role } as DatasetQueryResult['columns'][number])

  it('draws bars from exact values, dims unselected marks and uses theme tokens', () => {
    const data = result([column('d0', 'string', 'dimension'), column('m0', 'decimal', 'measure')],
      [[{ type: 'string', value: 'Jakarta' }, { type: 'decimal', value: '979755000.00' }], [{ type: 'string', value: 'Medan' }, null]])
    const option = buildChartOption(chart(), data, tokens, 'Jakarta') as { series: { data: { value: number | null; itemStyle: { opacity: number } }[] }[]; color: string[]; xAxis: { data: string[] } }
    expect(option.xAxis.data).toEqual(['Jakarta', 'Medan'])
    expect(option.series[0].data).toEqual([{ value: 979755000, itemStyle: { opacity: 1 } }, { value: null, itemStyle: { opacity: 0.3 } }])
    expect(option.color).toEqual(['mark'])
  })

  it('keeps every category label of a short axis, shortened to its band', () => {
    const data = result([column('d0', 'string', 'dimension'), column('m0', 'decimal', 'measure')],
      ['Bandung', 'Jakarta', 'Makassar'].map(value => [{ type: 'string', value }, { type: 'decimal', value: '1' }]))
    const option = buildChartOption(chart(), data, tokens, null, 300) as { xAxis: { axisLabel: Record<string, unknown> } }
    expect(option.xAxis.axisLabel).toMatchObject({ interval: 0, hideOverlap: false, overflow: 'truncate', width: 77 })
    // Without a measured width ECharts keeps its own spacing.
    expect((buildChartOption(chart(), data, tokens) as { xAxis: { axisLabel: Record<string, unknown> } }).xAxis.axisLabel.interval).toBeUndefined()
  })

  it('builds pie and gauge options from the first measure', () => {
    const pie = toChart(switchVisual({ ...initialState({ chart_id: 'c', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'Share' }, 'pie', FIELDS))
    const data = result([column('d0', 'string', 'dimension'), column('m0', 'integer', 'measure')], [[{ type: 'string', value: 'A' }, { type: 'integer', value: '3' }]])
    expect((buildChartOption(pie, data, tokens) as { series: { type: string; data: unknown[] }[] }).series[0]).toMatchObject({ type: 'pie', data: [{ name: 'A', value: 3 }] })
    const gauge = toChart(switchVisual({ ...initialState({ chart_id: 'c', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'G' }, 'gauge', FIELDS))
    const single = result([column('m0', 'integer', 'measure')], [[{ type: 'integer', value: '40' }]])
    expect((buildChartOption(gauge, single, tokens) as { series: { type: string; data: { value: number }[] }[] }).series[0]).toMatchObject({ type: 'gauge', data: [{ value: 40 }] })
  })
})

describe('result column labels', () => {
  it('names engine columns in plain words without table prefixes', () => {
    expect(humanize('stock_date')).toBe('Stock date')
    expect(humanize('outlet_stock_daily.stock_date')).toBe('Stock date')
    expect(humanize('Region')).toBe('Region')
    expect(columnLabel({ name: 'orders.revenue', aggregation: 'sum', field_id: 'revenue' })).toBe('Sum of revenue')
    expect(columnLabel({ name: 'rows', aggregation: 'count', field_id: null })).toBe('Rows')
  })

})
