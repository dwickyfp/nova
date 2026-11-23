import type {
  CartesianEncoding, ChartDefinition, DatasetQueryRequest, DatasetQueryResult, FilterTree, QueryColumn,
} from '../contracts'

type Encoding = ChartDefinition['encoding']
export type DateGrain = 'day' | 'week' | 'month' | 'quarter' | 'year'
type ChartDimension = { field_id: string; date_grain?: DateGrain | null }
type ChartMeasure = { field_id: string; aggregation: string }
export type QueryShape = Pick<DatasetQueryRequest, 'dimensions' | 'measures' | 'sort' | 'limit'>
export type Cell = DatasetQueryResult['rows'][number][number]
type Expression = FilterTree['expression']

export const SUPPORTED_AGGREGATIONS = ['sum', 'avg', 'min', 'max', 'count', 'count_distinct'] as const
export type SupportedAggregation = typeof SUPPORTED_AGGREGATIONS[number]

export class ChartQueryError extends Error {}

/** A color split only counts when it is not already an axis field. */
export function hasSplit(encoding: CartesianEncoding): boolean {
  return Boolean(encoding.color && !encoding.dimensions.some(item => item.field_id === encoding.color!.field_id))
}

function dimension(item: ChartDimension) {
  return item.date_grain ? { field_id: item.field_id, date_grain: item.date_grain } : { field_id: item.field_id }
}

function measure(item: ChartMeasure) {
  if (!(SUPPORTED_AGGREGATIONS as readonly string[]).includes(item.aggregation)) {
    throw new ChartQueryError('This aggregation is not available for native charts yet.')
  }
  return { field_id: item.field_id, aggregation: item.aggregation as SupportedAggregation }
}

/** The query a chart needs: field IDs and aggregations only, never SQL. */
export function chartQueryShape(chart: Pick<ChartDefinition, 'encoding' | 'sort' | 'row_limit' | 'visual'>): QueryShape {
  const encoding: Encoding = chart.encoding
  let dimensions: ChartDimension[] = []
  let measures: ChartMeasure[] = []
  switch (encoding.kind) {
    case 'cartesian':
      dimensions = [...encoding.dimensions, ...(hasSplit(encoding) ? [encoding.color!] : [])]
      measures = [...encoding.measures]
      break
    case 'single_measure':
      measures = [encoding.measure, ...(encoding.comparison ? [encoding.comparison] : [])]
      break
    case 'category':
      dimensions = [encoding.category]
      measures = [encoding.measure]
      break
    case 'scatter':
      dimensions = [{ field_id: encoding.x }, { field_id: encoding.y },
        ...(encoding.size ? [{ field_id: encoding.size }] : []),
        ...(encoding.color ? [encoding.color] : [])]
      break
    case 'heatmap':
      dimensions = [encoding.x, ...(encoding.y ? [encoding.y] : [])]
      measures = [encoding.value]
      break
    case 'distribution':
      dimensions = [{ field_id: encoding.field_id }, ...(encoding.group ? [encoding.group] : [])]
      measures = [{ field_id: encoding.field_id, aggregation: 'count' }]
      break
    case 'table':
      dimensions = [...(encoding.dimensions ?? []), ...(encoding.pivot_columns ?? [])]
      measures = [...(encoding.measures ?? [])]
      break
    case 'hierarchy':
      dimensions = [...encoding.levels]
      measures = [encoding.value]
      break
    case 'flow':
      dimensions = [encoding.source, encoding.target]
      measures = [encoding.value]
      break
    case 'map':
      dimensions = [{ field_id: encoding.latitude }, { field_id: encoding.longitude },
        ...(encoding.group ? [encoding.group] : [])]
      measures = encoding.value ? [encoding.value] : [{ field_id: encoding.latitude, aggregation: 'count' }]
      break
  }
  if (!dimensions.length && !measures.length) throw new ChartQueryError('Add a field to this chart.')
  if (dimensions.length > 8) throw new ChartQueryError('A chart can group by at most eight fields.')
  const dims = dimensions.map(dimension)
  const meas = measures.map(measure)
  const keyOf = (fieldId: string, aggregation?: string | null) => {
    const m = measures.findIndex(item => item.field_id === fieldId && (!aggregation || item.aggregation === aggregation))
    if (m >= 0) return `m${m}`
    const d = dimensions.findIndex(item => item.field_id === fieldId)
    return d >= 0 ? `d${d}` : null
  }
  const sort = (chart.sort ?? []).flatMap(item => {
    const column = keyOf(item.field_id, item.aggregation)
    return column ? [{ column, direction: item.direction ?? 'asc' }] : []
  })
  const categoryLimit = encoding.kind === 'category' ? encoding.max_categories ?? 12 : null
  if (encoding.kind === 'category' && !sort.length) sort.push({ column: 'm0', direction: 'desc' })
  return {
    dimensions: dims, measures: meas, sort: sort.slice(0, 8),
    limit: Math.min(categoryLimit ?? chart.row_limit ?? 1000, 10_000),
  }
}

/** All predicates that apply to one chart: its own filter, then dashboard context. */
export function combineFilters(...trees: (FilterTree | null | undefined)[]): FilterTree | undefined {
  const expressions: Expression[] = trees.flatMap(tree => tree ? [tree.expression] : [])
  if (!expressions.length) return undefined
  if (expressions.length === 1) return { expression: expressions[0] }
  return { expression: { type: 'and', children: expressions } }
}

export function cellNumber(cell: Cell): number | null {
  if (cell === null) return null
  if (cell.type === 'integer' || cell.type === 'decimal') return Number(cell.value)
  if (cell.type === 'float') return cell.value
  return null
}

const dateFormats = {
  day: { year: 'numeric', month: 'short', day: 'numeric' },
  week: { year: 'numeric', month: 'short', day: 'numeric' },
  month: { year: 'numeric', month: 'short' },
  quarter: { year: 'numeric', month: 'short' },
  year: { year: 'numeric' },
} as const

/** A label for a cell, honoring the column's date grain; exact text otherwise. */
export function cellLabel(cell: Cell, column?: Pick<QueryColumn, 'date_grain'>): string {
  if (cell === null) return '(empty)'
  if (cell.type === 'boolean') return cell.value ? 'True' : 'False'
  if (cell.type === 'date' || cell.type === 'timestamp') {
    const grain = column?.date_grain
    if (!grain) return cell.value
    const [year, month, day] = cell.value.slice(0, 10).split('-').map(Number)
    const date = new Date(Date.UTC(year, month - 1, day))
    if (grain === 'quarter') return `Q${Math.floor((month - 1) / 3) + 1} ${year}`
    return new Intl.DateTimeFormat(undefined, { ...dateFormats[grain], timeZone: 'UTC' }).format(date)
  }
  return String(cell.value)
}

const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 })
const full = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })

export function formatNumber(value: number | null, style: 'compact' | 'full' = 'full'): string {
  if (value === null || !Number.isFinite(value)) return '–'
  return (style === 'compact' && Math.abs(value) >= 10_000 ? compact : full).format(value)
}

/** The exclusive end of the calendar period that starts on `from` (YYYY-MM-DD). */
export function periodEnd(from: string, grain: DateGrain): string {
  const [year, month, day] = from.split('-').map(Number)
  const next = grain === 'day' ? new Date(Date.UTC(year, month - 1, day + 1))
    : grain === 'week' ? new Date(Date.UTC(year, month - 1, day + 7))
      : grain === 'month' ? new Date(Date.UTC(year, month, 1))
        : grain === 'quarter' ? new Date(Date.UTC(year, month + 2, 1))
          : new Date(Date.UTC(year + 1, 0, 1))
  return next.toISOString().slice(0, 10)
}

/** A field name as people read it: `order_date` → `Order date`. Names with spaces or capitals stay as written. */
const ACRONYMS = new Set(['id', 'ids', 'sku', 'url', 'api', 'kpi', 'gmv', 'aov', 'roi', 'vat', 'nik', 'npwp'])

export function humanize(name: string): string {
  // A qualified engine name (`table.column`) is labelled by its column.
  if (/^[a-z0-9_]+(\.[a-z0-9_]+)+$/.test(name)) name = name.slice(name.lastIndexOf('.') + 1)
  if (!/^[a-z0-9_]+$/.test(name)) return name
  // Initialisms read as such: `customer_id` is "Customer ID", not "Customer id".
  const words = name.split('_').filter(Boolean)
    .map(word => ACRONYMS.has(word) ? word.toUpperCase() : word).join(' ')
  return words ? words[0].toUpperCase() + words.slice(1) : name
}

const AGGREGATION_WORDS: Record<string, string> = {
  sum: 'Sum of', avg: 'Average', min: 'Minimum', max: 'Maximum', count: 'Count of', count_distinct: 'Distinct',
}

/** How a result column is named in legends, tooltips and table headers. */
export function columnLabel(column: Pick<QueryColumn, 'name' | 'aggregation' | 'field_id'>): string {
  if (column.aggregation === 'count' && !column.field_id) return 'Rows'
  const name = humanize(column.name)
  if (!column.aggregation) return name
  const word = AGGREGATION_WORDS[column.aggregation]
  return word ? `${word} ${name.charAt(0).toLowerCase()}${name.slice(1)}` : name
}
