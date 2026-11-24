import { describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import type { CartesianEncoding, ChartDefinition, DashboardDefinition, DatasetField, DatasetQueryResult } from '../contracts'
import { fromChart, initialState, missing, switchVisual, toChart } from '../charts/builder-model'
import { buildChartOption } from '../charts/chart-options'
import { cellLabel, chartQueryShape, ChartQueryError, columnLabel, combineFilters, formatNumber, hasSplit, humanize, periodEnd } from '../charts/chart-query'
import type { VizTokens } from '../charts/echart-host'
import { capacityRetry, MAX_CONCURRENT_QUERIES, queuedQuery } from '../charts/query-queue'
import { chartDataQuery, freshChartData } from '../charts/chart-data'
import { chartContext, defaultValue, targetBinding, type FilterDefinition } from './filter-model'
import { cellRect, firstFreeSlot, gridWidgets, slotAt, slotOnPage, upcastDashboard, upcastWidget, validPlacements, type Page, type Widget } from './layout'
import { applyLocal, inverse } from './operations'

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

  it('NDV-CHART-009: a new pie of a sum combines the rest into Other, and a reopened chart keeps its choice', () => {
    const base = { ...initialState({ chart_id: 'c1', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'P' }
    const sum = switchVisual(base, 'pie', FIELDS)
    const overflowOf = (state: typeof sum) => (toChart(state).encoding as { overflow?: string }).overflow
    expect(overflowOf(sum)).toBe('other')
    expect(overflowOf({ ...sum, otherSlice: false })).toBe('error')
    // An average cannot be split into the shown part and the rest.
    expect(overflowOf({ ...sum, measures: [{ ...sum.measures[0], aggregation: 'avg' }] })).toBe('error')
    // A funnel never gets Other.
    expect(overflowOf(switchVisual(base, 'funnel', FIELDS))).toBe('error')
    // Reopening keeps what was saved, including charts saved before Other existed.
    expect(overflowOf(fromChart(toChart({ ...sum, otherSlice: false }), FIELDS))).toBe('error')
    expect(overflowOf(fromChart(toChart(sum), FIELDS))).toBe('other')
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

  it('ORs values chosen together in one chart and ANDs them with other charts and filters', () => {
    const field = (id: string) => ({ dataset_id: DATASET, dataset_revision: 3, field_id: id })
    const pick = (widget: string, id: string, value: string) =>
      ({ source_widget_id: widget, field: field(id), value: { type: 'string' as const, value }, label: value })
    const cross = [pick('w1', 'region', 'Jakarta'), pick('w1', 'region', 'Bandung'), pick('w3', 'channel', 'Online')]
    const values = { f1: { kind: 'text' as const, value: 'a' } }
    const tree = chartContext({ definition: definition([filter()]), chart: target, widgetId: 'w2', pageId: 'p1', values, crossFilters: cross })
    expect(tree?.expression).toMatchObject({ type: 'and', children: [
      { op: 'like_literal' },
      { type: 'or', children: [{ op: 'eq', values: [{ value: 'Jakarta' }] }, { op: 'eq', values: [{ value: 'Bandung' }] }] },
      { op: 'eq', values: [{ value: 'Online' }] },
    ] })
    // Clearing every selection leaves only the dashboard filter.
    expect(chartContext({ definition: definition([filter()]), chart: target, widgetId: 'w2', pageId: 'p1', values, crossFilters: [] })?.expression)
      .toMatchObject({ op: 'like_literal' })
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
    upcastDashboard({ dashboard_id: 'd', title: 'Before', pages: [{ page_id: 'p', title: 'P', widgets }], filters: [] })

  it('places new widgets in the first free slot without overlap', () => {
    expect(firstFreeSlot([widget('a', 0, 0)], 6, 4)).toEqual({ x: 6, y: 0, w: 6, h: 4 })
    expect(firstFreeSlot([widget('a', 0, 0), widget('b', 6, 0)], 3, 2)).toEqual({ x: 0, y: 4, w: 3, h: 2 })
    expect(validPlacements([{ widget_id: 'a', layout: { x: 0, y: 0, w: 6, h: 4 } }, { widget_id: 'b', layout: { x: 4, y: 0, w: 6, h: 4 } }])).toBe(false)
  })

  it('computes exact inverses so undo restores the earlier document', () => {
    const before = dashboard([widget('a', 0, 0), widget('b', 6, 0)])
    const moves = [{ op: 'layout.apply' as const, page_id: 'p', placements: [{ widget_id: 'a', layout: { x: 0, y: 4, w: 6, h: 4 } }, { widget_id: 'b', layout: { x: 0, y: 0, w: 6, h: 4 } }] }]
    const after = moves.reduce(applyLocal, before)
    expect(gridWidgets(after.pages[0]).map(item => ({ widget_id: item.widget_id, layout: item.layout })))
      .toEqual(expect.arrayContaining(moves[0].placements))
    expect(applyLocal(after, inverse(moves[0], before)!)).toEqual(before)
    const removal = { op: 'widget.remove' as const, page_id: 'p', widget_id: 'a' }
    expect(gridWidgets(applyLocal(applyLocal(before, removal), inverse(removal, before)!).pages[0])).toHaveLength(2)
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

  it('keeps the slot of a sent query that is aborted until the server answers', async () => {
    // Switching pages aborts the old page's queries, but the server keeps running
    // them; releasing their slots early sent more than the server admits (429s).
    const releases: (() => void)[] = []
    const run = vi.fn(() => new Promise<string>(resolve => releases.push(() => resolve('done'))))
    const old = new AbortController()
    const sent = Array.from({ length: MAX_CONCURRENT_QUERIES }, () => queuedQuery(run, old.signal))
    old.abort()
    const next = queuedQuery(run)
    await Promise.resolve()
    expect(run).toHaveBeenCalledTimes(MAX_CONCURRENT_QUERIES)
    releases.shift()!()
    await expect(sent[0]).resolves.toBe('done')
    await Promise.resolve()
    expect(run).toHaveBeenCalledTimes(MAX_CONCURRENT_QUERIES + 1)
    while (releases.length) releases.shift()!()
    await Promise.all([...sent, next])
  })

  it('lets a chart request that was sent finish when its chart goes away', async () => {
    const signals: (AbortSignal | null | undefined)[] = []
    let answer: (response: Response) => void = () => undefined
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit = {}) => {
      signals.push(init.signal)
      return new Promise<Response>(resolve => { answer = resolve })
    }))
    try {
      const chart = { chart_id: 'c', dataset_id: 'aaaaaaaa-0000-4000-8000-000000000009', dataset_revision: 1 } as ChartDefinition
      const options = chartDataQuery(0, chart, { revision: 1, draft: false, measures: [{ field_id: 'f', aggregation: 'sum' }] } as never)
      const leaving = new AbortController()
      const pending = options.queryFn({ signal: leaving.signal })
      await vi.waitFor(() => expect(signals).toHaveLength(1))
      leaving.abort()
      // The request the server is running is not cut off.
      expect(signals[0]?.aborted ?? false).toBe(false)
      answer(new Response(JSON.stringify({ dataset_id: chart.dataset_id, revision: 1, truncated: false, offset: 0, next_offset: null, rows: [],
        columns: [{ key: 'm0', name: 'f', logical_type: 'decimal', role: 'measure', field_id: 'f', aggregation: 'sum' }] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }))
      await expect(pending).resolves.toMatchObject({ rows: [] })
    } finally {
      vi.unstubAllGlobals()
    }
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

  it('highlights several selected categories, or whole series picked from the legend', () => {
    const data = result([column('d0', 'string', 'dimension'), column('m0', 'decimal', 'measure')],
      [[{ type: 'string', value: 'Jakarta' }, { type: 'decimal', value: '1' }], [{ type: 'string', value: 'Medan' }, { type: 'decimal', value: '2' }],
        [{ type: 'string', value: 'Bandung' }, { type: 'decimal', value: '3' }]])
    const option = buildChartOption(chart(), data, tokens, ['Jakarta', 'Bandung']) as { series: { data: { itemStyle: { opacity: number } }[] }[] }
    expect(option.series[0].data.map(item => item.itemStyle.opacity)).toEqual([1, 0.3, 1])
    const none = buildChartOption(chart(), data, tokens, []) as { series: { data: unknown[] }[] }
    expect(none.series[0].data).toEqual([1, 2, 3])
  })

  it('keeps every category label of a short axis, shortened to its band', () => {
    const data = result([column('d0', 'string', 'dimension'), column('m0', 'decimal', 'measure')],
      ['Bandung', 'Jakarta', 'Makassar'].map(value => [{ type: 'string', value }, { type: 'decimal', value: '1' }]))
    const option = buildChartOption(chart(), data, tokens, null, 300) as { xAxis: { axisLabel: Record<string, unknown> } }
    expect(option.xAxis.axisLabel).toMatchObject({ interval: 0, hideOverlap: false, overflow: 'truncate', width: 77 })
    // A horizontal bar's vertical axis carries no title over its labels.
    const sideways = { ...chart(), encoding: { ...(chart().encoding as CartesianEncoding), orientation: 'horizontal' } } as ChartDefinition
    const side = (buildChartOption(sideways, data, tokens, null, 300) as { yAxis: { name?: string; data: string[] } }).yAxis
    expect(side.data).toEqual(['Bandung', 'Jakarta', 'Makassar'])
    expect(side.name).toBeUndefined()
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

describe('pivot and conditional formats in the builder', () => {
  const base = () => ({ ...initialState({ chart_id: 'c1', dataset_id: DATASET, dataset_revision: 3, fields: FIELDS }), title: 'Units' })

  it('switching to a pivot puts a date across the top and saves pivot columns', () => {
    const state = switchVisual(base(), 'pivot', FIELDS)
    expect(state.pivotColumns).toEqual([{ field_id: 'day', date_grain: 'month' }])
    expect(missing(state)).toBeNull()
    const saved = toChart(state)
    expect(saved.visual).toBe('pivot')
    expect(saved.encoding).toMatchObject({ kind: 'table', dimensions: [{ field_id: 'region' }], pivot_columns: [{ field_id: 'day', date_grain: 'month' }] })
    // A plain table never carries pivot columns.
    expect('pivot_columns' in toChart({ ...state, visual: 'table' }).encoding).toBe(false)
  })

  it('names what a pivot still needs', () => {
    expect(missing({ ...switchVisual(base(), 'pivot', FIELDS), pivotColumns: [] })).toBe('Choose a field for the columns')
    expect(missing({ ...switchVisual(base(), 'pivot', FIELDS), measures: [] })).toBe('Choose a value to measure')
  })

  it('keeps formats for measures still in the table and drops them elsewhere', () => {
    const state = { ...switchVisual(base(), 'table', FIELDS), formats: [
      { field_id: 'revenue', kind: 'scale' as const, tone: 'accent' as const },
      { field_id: 'gone', kind: 'scale' as const, tone: 'accent' as const },
    ] }
    expect(toChart(state).style?.conditional_formats).toEqual([{ field_id: 'revenue', kind: 'scale', tone: 'accent' }])
    expect(toChart({ ...state, visual: 'bar' }).style?.conditional_formats).toBeUndefined()
    const reopened = fromChart(toChart(state), FIELDS)
    expect(reopened.formats).toEqual([{ field_id: 'revenue', kind: 'scale', tone: 'accent' }])
  })
})

describe('dashboard links', () => {
  it('replaces actions locally and undoes back to the previous set', () => {
    const link = { action_id: 'a1', kind: 'navigate' as const, source_widget_id: 'w1', target_dashboard_id: 'd2',
      target_workspace_id: 'personal', mappings: [], run_on: 'menu' as const }
    const before = upcastDashboard({ dashboard_id: 'd1', title: 'Sales', filters: [], datasets: [],
      actions: [], pages: [{ page_id: 'p1', title: 'Overview', widgets: [] }] })
    const change = { op: 'actions.replace' as const, actions: [link] }
    expect(applyLocal(before, change).actions).toEqual([link])
    expect(inverse(change, before)).toEqual({ op: 'actions.replace', actions: [] })
  })
})

describe('governed downloads', () => {
  it('keeps rows read under one role apart from rows read under another', async () => {
    // A role switch moves the security epoch (auth-store.test.ts); rows are keyed by it.
    const chart = { chart_id: 'c', dataset_id: 'aaaaaaaa-0000-4000-8000-000000000011', dataset_revision: 1 } as ChartDefinition
    const request = { revision: 1, draft: false, measures: [{ field_id: 'f', aggregation: 'sum' }] } as never
    const marketing = chartDataQuery(4, chart, request).queryKey
    const finance = chartDataQuery(5, chart, request).queryKey
    expect(JSON.stringify(marketing)).not.toBe(JSON.stringify(finance))
    const client = new QueryClient()
    client.setQueryData(marketing, { rows: [['Jakarta']] })
    expect(client.getQueryData(finance)).toBeUndefined()
  })

  it('reads the rows again for each download instead of reusing an earlier result', async () => {
    const chart = { chart_id: 'c', dataset_id: 'aaaaaaaa-0000-4000-8000-000000000010', dataset_revision: 1 } as ChartDefinition
    const body = JSON.stringify({ dataset_id: chart.dataset_id, revision: 1, truncated: false, offset: 0, next_offset: null, rows: [],
      columns: [{ key: 'm0', name: 'f', logical_type: 'decimal', role: 'measure', field_id: 'f', aggregation: 'sum' }] })
    const fetcher = vi.fn(async () => new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetcher)
    try {
      const client = new QueryClient()
      const request = { revision: 1, draft: false, measures: [{ field_id: 'f', aggregation: 'sum' }] } as never
      // A chart on screen may reuse rows it just read ...
      await client.fetchQuery(chartDataQuery(0, chart, request))
      await client.fetchQuery(chartDataQuery(0, chart, request))
      expect(fetcher).toHaveBeenCalledTimes(1)
      // ... but each download asks the engine again under the current policy.
      await freshChartData(client, 0, chart, request)
      await freshChartData(client, 0, chart, request)
      expect(fetcher).toHaveBeenCalledTimes(3)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('NDV-CACHE-008: reuses rows on screen for 30 seconds, as /docs says, and reads again after', async () => {
    const chart = { chart_id: 'c', dataset_id: 'aaaaaaaa-0000-4000-8000-000000000011', dataset_revision: 1 } as ChartDefinition
    const body = JSON.stringify({ dataset_id: chart.dataset_id, revision: 1, truncated: false, offset: 0, next_offset: null, rows: [],
      columns: [{ key: 'm0', name: 'f', logical_type: 'decimal', role: 'measure', field_id: 'f', aggregation: 'sum' }] })
    const fetcher = vi.fn(async () => new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetcher)
    const start = Date.now()
    const now = vi.spyOn(Date, 'now').mockReturnValue(start)
    try {
      const client = new QueryClient()
      const request = { revision: 1, draft: false, measures: [{ field_id: 'f', aggregation: 'sum' }] } as never
      await client.fetchQuery(chartDataQuery(0, chart, request))
      now.mockReturnValue(start + 29_000)
      await client.fetchQuery(chartDataQuery(0, chart, request))
      expect(fetcher).toHaveBeenCalledTimes(1)
      now.mockReturnValue(start + 31_000)
      await client.fetchQuery(chartDataQuery(0, chart, request))
      expect(fetcher).toHaveBeenCalledTimes(2)
    } finally {
      now.mockRestore()
      vi.unstubAllGlobals()
    }
  })
})

describe('dropping a widget on a cell', () => {
  const at = (x: number, y: number, w: number, h: number) => ({ widget_id: `${x}-${y}`, kind: 'text', layout: { x, y, w, h } }) as unknown as Widget
  const widgets = [at(0, 0, 3, 2), at(3, 0, 9, 2), at(0, 2, 6, 4)]

  it('lands on the cell under the pointer when it fits', () => {
    expect(slotAt(widgets, 3, 2, { x: 7, y: 3 })).toEqual({ x: 7, y: 3, w: 3, h: 2 })
  })

  it('keeps a wide widget inside the grid', () => {
    expect(slotAt(widgets, 6, 4, { x: 10, y: 6 })).toEqual({ x: 6, y: 6, w: 6, h: 4 })
  })

  it('never overlaps: on an occupied cell it takes the first free slot from that row down', () => {
    expect(slotAt(widgets, 12, 2, { x: 4, y: 1 })).toEqual({ x: 0, y: 6, w: 12, h: 2 })
    expect(slotAt(widgets, 3, 2, { x: 1, y: 3 })).toEqual({ x: 6, y: 3, w: 3, h: 2 })
  })

  it('without a cell behaves like the toolbar button', () => {
    expect(slotAt(widgets, 3, 2, null)).toEqual(firstFreeSlot(widgets, 3, 2))
  })
})

describe('a new item on a fixed-size page', () => {
  const grid = { columns: 12, gutter: 16, margin: 16, baseline: 8 }
  const text = (layout: Widget['layout'], index: number) =>
    upcastWidget({ widget_id: `w${index}`, kind: 'text', title: '', locked: false, text: 'x', style: 'body', layout } as Widget, grid, 1280)!
  const page = (fit: 'width' | 'page', layouts: Widget['layout'][]) => ({
    page_id: 'p', title: 'P', tab_order: [], mobile: { order: [], hidden: [] }, legacy_spacers: [],
    canvas: { size: '16:9', width: 1280, height: 720, fit, background: null, grid: { ...grid, show: true, snap: true, snap_to_elements: true } },
    elements: layouts.map(text),
  }) as unknown as Page
  const inside = (layout: Widget['layout']) => {
    const box = cellRect(layout, grid, 1280)
    return box.x >= 0 && box.y >= 0 && box.x + box.w <= 1280 && box.y + box.h <= 720
  }
  const full = [{ x: 0, y: 0, w: 12, h: 8 }]

  it('takes the next slot below on a page that scrolls, which grows', () => {
    expect(slotOnPage(page('width', full), 6, 4)).toEqual({ x: 0, y: 8, w: 6, h: 4 })
  })

  it('takes the free slot when it fits inside the page', () => {
    const slot = slotOnPage(page('page', full), 6, 2)
    expect(slot).toEqual({ x: 0, y: 8, w: 6, h: 2 })
    expect(inside(slot)).toBe(true)
  })

  it('goes to the top-left, over what is there, when no free slot fits', () => {
    const slot = slotOnPage(page('page', full), 6, 4)
    expect(slot).toEqual({ x: 0, y: 0, w: 6, h: 4 })
    expect(inside(slot)).toBe(true)
  })

  it('shortens an item taller than the page', () => {
    const slot = slotOnPage(page('page', []), 12, 14)
    expect(slot).toEqual({ x: 0, y: 0, w: 12, h: 10 })
    expect(inside(slot)).toBe(true)
  })
})
