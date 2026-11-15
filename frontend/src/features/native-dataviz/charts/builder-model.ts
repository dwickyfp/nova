import type { ChartDefinition, DatasetField } from '../contracts'
import type { DateGrain, SupportedAggregation } from './chart-query'

export type Visual = ChartDefinition['visual']
export type BuilderDimension = { field_id: string; date_grain: DateGrain | null }
export type BuilderMeasure = { field_id: string; aggregation: SupportedAggregation; series: 'bar' | 'line' | 'area'; axis: 'left' | 'right' }
export type NumberStyle = { kind: 'number' | 'currency' | 'percent'; decimals: number; currency: string }

export type BuilderState = {
  chart_id: string
  title: string
  dataset_id: string
  dataset_revision: number
  visual: Visual
  dimensions: BuilderDimension[]
  measures: BuilderMeasure[]
  color: BuilderDimension | null
  /** Raw numeric fields for scatter X/Y/size and histogram value. */
  x: string
  y: string
  size: string
  orientation: 'vertical' | 'horizontal'
  stack: 'none' | 'stacked' | 'percent'
  smooth: boolean
  donut: boolean
  maxCategories: number
  bins: number
  grandTotal: boolean
  sort: 'none' | 'asc' | 'desc'
  rowLimit: number
  showTitle: boolean
  showLegend: boolean
  showLabels: boolean
  showAxisTitles: boolean
  palette: 'neutral' | 'categorical' | 'single_tone'
  number: NumberStyle
  filters: ChartDefinition['filters']
}

export const VISUALS: { value: Visual; label: string; kind: ChartDefinition['encoding']['kind'] }[] = [
  { value: 'kpi', label: 'KPI', kind: 'single_measure' },
  { value: 'bar', label: 'Bar', kind: 'cartesian' },
  { value: 'line', label: 'Line', kind: 'cartesian' },
  { value: 'area', label: 'Area', kind: 'cartesian' },
  { value: 'combo', label: 'Combo', kind: 'cartesian' },
  { value: 'pie', label: 'Pie', kind: 'category' },
  { value: 'funnel', label: 'Funnel', kind: 'category' },
  { value: 'table', label: 'Table', kind: 'table' },
  { value: 'scatter', label: 'Scatter', kind: 'scatter' },
  { value: 'heatmap', label: 'Heatmap', kind: 'heatmap' },
  { value: 'treemap', label: 'Treemap', kind: 'hierarchy' },
  { value: 'sankey', label: 'Flow', kind: 'flow' },
  { value: 'histogram', label: 'Histogram', kind: 'distribution' },
  { value: 'gauge', label: 'Gauge', kind: 'single_measure' },
]

export const AGGREGATION_LABELS: Record<SupportedAggregation, string> = {
  sum: 'Sum', avg: 'Average', min: 'Minimum', max: 'Maximum', count: 'Count', count_distinct: 'Distinct count',
}

export function kindOf(visual: Visual) {
  return VISUALS.find(item => item.value === visual)?.kind ?? 'cartesian'
}

export const isNumeric = (field?: DatasetField) => Boolean(field && ['integer', 'decimal', 'float'].includes(field.logical_type))
export const isTemporal = (field?: DatasetField) => Boolean(field && ['date', 'timestamp'].includes(field.logical_type))

export function defaultAggregation(field?: DatasetField): SupportedAggregation {
  return isNumeric(field) ? 'sum' : 'count_distinct'
}

export function initialState(input: {
  chart_id: string; dataset_id: string; dataset_revision: number; fields: DatasetField[]; visual?: Visual
}): BuilderState {
  const dimension = input.fields.find(field => field.semantic_role === 'dimension' && !isNumeric(field))
  const measure = input.fields.find(isNumeric)
  return {
    chart_id: input.chart_id, title: '', dataset_id: input.dataset_id, dataset_revision: input.dataset_revision,
    visual: input.visual ?? 'bar',
    dimensions: dimension ? [{ field_id: dimension.field_id, date_grain: isTemporal(dimension) ? 'month' : null }] : [],
    measures: measure ? [{ field_id: measure.field_id, aggregation: 'sum', series: 'bar', axis: 'left' }]
      : [], color: null, x: measure?.field_id ?? '', y: input.fields.filter(isNumeric)[1]?.field_id ?? measure?.field_id ?? '', size: '',
    orientation: 'vertical', stack: 'none', smooth: false, donut: false, maxCategories: 12, bins: 20,
    grandTotal: true, sort: 'none', rowLimit: 1000, showTitle: true, showLegend: true, showLabels: false,
    showAxisTitles: true, palette: 'neutral', number: { kind: 'number', decimals: 2, currency: 'USD' }, filters: null,
  }
}

function dim(item: BuilderDimension) {
  return { field_id: item.field_id, discrete: true, date_grain: item.date_grain, date_mode: 'truncate' as const }
}

function format(number: NumberStyle) {
  return number.kind === 'currency'
    ? { kind: 'currency' as const, decimals: number.decimals, currency: number.currency, locale: 'en-US' }
    : { kind: number.kind, decimals: number.decimals, locale: 'en-US' }
}

function meas(item: BuilderMeasure, number: NumberStyle) {
  return { field_id: item.field_id, aggregation: item.aggregation, format: format(number), axis: item.axis, series: item.series }
}

/** What the builder still needs before the chart can be saved, in plain words. */
export function missing(state: BuilderState): string | null {
  const kind = kindOf(state.visual)
  if (!state.title.trim()) return 'Name the chart.'
  if (['cartesian', 'category', 'heatmap', 'hierarchy', 'flow'].includes(kind) && !state.dimensions.length) return 'Choose a field to group by.'
  if ((kind === 'heatmap' || kind === 'flow') && state.dimensions.length < 2) return 'Choose two fields to group by.'
  if (['cartesian', 'single_measure', 'category', 'heatmap', 'hierarchy', 'flow'].includes(kind) && !state.measures.length) return 'Choose a value to measure.'
  if (kind === 'scatter' && (!state.x || !state.y)) return 'Choose numeric fields for both axes.'
  if (kind === 'distribution' && !state.x) return 'Choose a numeric field.'
  if (kind === 'table' && !state.dimensions.length && !state.measures.length) return 'Add a column.'
  return null
}

export function toChart(state: BuilderState): ChartDefinition {
  const kind = kindOf(state.visual)
  const number = state.number
  let encoding: ChartDefinition['encoding']
  switch (kind) {
    case 'single_measure':
      encoding = { kind, measure: meas(state.measures[0], number),
        ...(state.measures[1] ? { comparison: meas(state.measures[1], number) } : {}) }
      break
    case 'category':
      encoding = { kind, category: dim(state.dimensions[0]), measure: meas(state.measures[0], number),
        donut: state.donut, max_categories: state.maxCategories, overflow: 'error' }
      break
    case 'scatter':
      encoding = { kind, x: state.x, y: state.y, ...(state.size ? { size: state.size } : {}),
        ...(state.color ? { color: dim(state.color) } : {}) }
      break
    case 'heatmap':
      encoding = { kind, x: dim(state.dimensions[0]), y: dim(state.dimensions[1]), value: meas(state.measures[0], number) }
      break
    case 'distribution':
      encoding = { kind, field_id: state.x, bins: state.bins }
      break
    case 'table':
      encoding = { kind, dimensions: state.dimensions.map(dim), measures: state.measures.map(item => meas(item, number)),
        grand_total: state.grandTotal, page_size: 100 }
      break
    case 'hierarchy':
      encoding = { kind, levels: state.dimensions.map(dim), value: meas(state.measures[0], number) }
      break
    case 'flow':
      encoding = { kind, source: dim(state.dimensions[0]), target: dim(state.dimensions[1]), value: meas(state.measures[0], number) }
      break
    default:
      encoding = { kind: 'cartesian', dimensions: state.dimensions.slice(0, 2).map(dim),
        measures: state.measures.map(item => meas(item, number)), ...(state.color ? { color: dim(state.color) } : {}),
        orientation: state.orientation, stack: state.stack, interpolation: state.smooth ? 'smooth' : 'linear', nulls: 'gap' }
  }
  const sortTarget = state.measures[0]
  return {
    schema_version: 2, chart_id: state.chart_id, title: state.title.trim() || 'Untitled chart',
    dataset_id: state.dataset_id, dataset_revision: state.dataset_revision, visual: state.visual, encoding,
    ...(state.filters ? { filters: state.filters } : {}),
    style: { show_title: state.showTitle, show_legend: state.showLegend, show_labels: state.showLabels,
      show_axis_titles: state.showAxisTitles, palette: state.palette, description: '' },
    sort: state.sort !== 'none' && sortTarget
      ? [{ field_id: sortTarget.field_id, direction: state.sort, aggregation: sortTarget.aggregation }] : [],
    row_limit: state.rowLimit, result_overflow: 'truncate_with_warning',
  }
}

type Measure = { field_id: string; aggregation: string; series?: string | null; axis?: string | null; format?: { kind?: string | null; decimals?: number | null; currency?: string | null } | null }
type Dimension = { field_id: string; date_grain?: DateGrain | null }

function fromDim(item: Dimension): BuilderDimension {
  return { field_id: item.field_id, date_grain: item.date_grain ?? null }
}

function fromMeasure(item: Measure): BuilderMeasure {
  const aggregation = (['sum', 'avg', 'min', 'max', 'count', 'count_distinct'].includes(item.aggregation)
    ? item.aggregation : 'sum') as SupportedAggregation
  return { field_id: item.field_id, aggregation, series: (item.series ?? 'bar') as BuilderMeasure['series'],
    axis: (item.axis ?? 'left') as BuilderMeasure['axis'] }
}

export function fromChart(chart: ChartDefinition, fields: DatasetField[]): BuilderState {
  const state = initialState({ chart_id: chart.chart_id, dataset_id: chart.dataset_id,
    dataset_revision: chart.dataset_revision, fields, visual: chart.visual })
  const encoding = chart.encoding
  const first = (encoding as { measure?: Measure }).measure ?? (encoding as { measures?: Measure[] }).measures?.[0]
    ?? (encoding as { value?: Measure }).value
  const style = chart.style ?? {}
  Object.assign(state, {
    title: chart.title, filters: chart.filters ?? null, rowLimit: chart.row_limit ?? 1000,
    showTitle: style.show_title ?? true, showLegend: style.show_legend ?? true, showLabels: style.show_labels ?? false,
    showAxisTitles: style.show_axis_titles ?? true, palette: style.palette ?? 'neutral',
    sort: chart.sort?.[0]?.direction ?? 'none', color: null, dimensions: [], measures: [],
    number: { kind: (first?.format?.kind === 'currency' || first?.format?.kind === 'percent') ? first.format.kind : 'number',
      decimals: first?.format?.decimals ?? 2, currency: first?.format?.currency ?? 'USD' },
  })
  switch (encoding.kind) {
    case 'cartesian':
      state.dimensions = encoding.dimensions.map(fromDim)
      state.measures = encoding.measures.map(fromMeasure)
      state.color = encoding.color ? fromDim(encoding.color) : null
      state.orientation = encoding.orientation ?? 'vertical'
      state.stack = encoding.stack ?? 'none'
      state.smooth = encoding.interpolation === 'smooth'
      break
    case 'single_measure':
      state.measures = [encoding.measure, ...(encoding.comparison ? [encoding.comparison] : [])].map(fromMeasure)
      break
    case 'category':
      state.dimensions = [fromDim(encoding.category)]
      state.measures = [fromMeasure(encoding.measure)]
      state.donut = encoding.donut ?? false
      state.maxCategories = encoding.max_categories ?? 12
      break
    case 'scatter':
      state.x = encoding.x; state.y = encoding.y; state.size = encoding.size ?? ''
      state.color = encoding.color ? fromDim(encoding.color) : null
      break
    case 'heatmap':
      state.dimensions = [encoding.x, ...(encoding.y ? [encoding.y] : [])].map(fromDim)
      state.measures = [fromMeasure(encoding.value)]
      break
    case 'distribution':
      state.x = encoding.field_id; state.bins = encoding.bins ?? 20
      break
    case 'table':
      state.dimensions = (encoding.dimensions ?? []).map(fromDim)
      state.measures = (encoding.measures ?? []).map(fromMeasure)
      state.grandTotal = encoding.grand_total ?? false
      break
    case 'hierarchy':
      state.dimensions = encoding.levels.map(fromDim)
      state.measures = [fromMeasure(encoding.value)]
      break
    case 'flow':
      state.dimensions = [fromDim(encoding.source), fromDim(encoding.target)]
      state.measures = [fromMeasure(encoding.value)]
      break
  }
  return state
}

/** Keep fields that still fit when the visualization changes, so switching types is not destructive. */
export function switchVisual(state: BuilderState, visual: Visual, fields: DatasetField[]): BuilderState {
  const next = { ...state, visual }
  const kind = kindOf(visual)
  const numeric = fields.filter(isNumeric)
  if ((kind === 'scatter' || kind === 'distribution') && !next.x) next.x = numeric[0]?.field_id ?? ''
  if (kind === 'scatter' && !next.y) next.y = numeric[1]?.field_id ?? numeric[0]?.field_id ?? ''
  if ((kind === 'heatmap' || kind === 'flow') && next.dimensions.length < 2) {
    const extra = fields.find(field => !isNumeric(field) && !next.dimensions.some(item => item.field_id === field.field_id))
    if (extra) next.dimensions = [...next.dimensions, { field_id: extra.field_id, date_grain: isTemporal(extra) ? 'month' : null }]
  }
  if (kind === 'single_measure' || kind === 'category') next.measures = next.measures.slice(0, kind === 'category' ? 1 : 2)
  if (visual === 'combo' && next.measures.length === 1 && numeric.length) {
    next.measures = [...next.measures, { ...next.measures[0], series: 'line', axis: 'right' }]
  }
  if (visual === 'kpi' && !next.title) next.showLegend = false
  return next
}
