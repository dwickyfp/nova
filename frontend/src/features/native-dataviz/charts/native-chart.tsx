import { Fragment, useCallback, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { ChartDefinition, DatasetQueryResult, FilterTree, ParameterInput, QueryColumn } from '../contracts'
import { buildChartOption } from './chart-options'
import { cellLabel, cellNumber, chartQueryShape, columnLabel, hasSplit, periodEnd, type Cell } from './chart-query'
import { queryError, useChartData } from './chart-data'
import { EChartHost, type MarkSelection, type VizTokens } from './echart-host'
import { cellStyle, MAX_PIVOT_COLUMNS, pivotModel, ranges } from './pivot'
import { NativeMap } from './native-map'

export type ValueSelection = {
  field_id: string
  value: NonNullable<Cell> | null
  label: string
  /** A date-grain mark selects its whole period: [from, to). */
  period?: { from: string; to: string }
  /** Where the mark was clicked inside the chart, for a menu beside it. */
  anchor?: { x: number; y: number }
  /** Add to this chart's selection (Shift, Ctrl or Cmd) instead of replacing it. */
  additive?: boolean
  /** Chosen from the legend: it filters directly, with no drill menu. */
  fromLegend?: boolean
}

type Props = {
  chart: ChartDefinition
  draft: boolean
  /** Dashboard filters and cross-filters that apply to this chart. */
  context?: FilterTree
  parameters?: ParameterInput[]
  onSelectValue?: (selection: ValueSelection) => void
  /** The category this chart's selection cross-filters by; other marks are dimmed. */
  selectedLabel?: string | readonly string[] | null
  className?: string
}

function measureFormatter(chart: ChartDefinition, index: number) {
  const encoding = chart.encoding
  const format = encoding.kind === 'single_measure'
    ? (index === 0 ? encoding.measure.format : encoding.comparison?.format)
    : 'measures' in encoding ? encoding.measures?.[index]?.format : undefined
  if (!format) return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })
  const options: Intl.NumberFormatOptions = { maximumFractionDigits: format.decimals ?? 2, minimumFractionDigits: 0 }
  if (format.kind === 'currency' && format.currency) Object.assign(options, { style: 'currency', currency: format.currency })
  if (format.kind === 'percent') options.style = 'percent'
  if (format.kind === 'scientific') options.notation = 'scientific'
  try {
    return new Intl.NumberFormat(format.locale, options)
  } catch {
    return new Intl.NumberFormat(undefined, options)
  }
}

function Kpi({ chart, result }: { chart: ChartDefinition; result: DatasetQueryResult }) {
  const value = cellNumber(result.rows[0]?.[0] ?? null)
  const comparison = result.columns.length > 1 ? cellNumber(result.rows[0]?.[1] ?? null) : null
  const target = chart.encoding.kind === 'single_measure' && chart.encoding.target ? Number(chart.encoding.target) : null
  const formatter = measureFormatter(chart, 0)
  const base = comparison ?? target
  const delta = value !== null && base ? (value - base) / Math.abs(base) : null
  return <div className='flex min-h-0 flex-1 flex-col justify-center gap-1 px-1'>
    <p className='viz-numerals truncate text-3xl font-medium tabular-nums' data-testid='kpi-value'>
      {value === null ? <span aria-hidden>–</span> : formatter.format(value)}
    </p>
    {value === null && <p className='text-sm text-muted-foreground' role='status'>No data for the current filters</p>}
    {delta !== null && Number.isFinite(delta) && <p className={cn('flex items-center gap-1 text-sm',
      delta >= 0 ? 'text-success-strong' : 'text-destructive')}>
      {delta >= 0 ? <ArrowUp className='size-4' aria-hidden /> : <ArrowDown className='size-4' aria-hidden />}
      <span>{`${delta >= 0 ? '+' : ''}${(delta * 100).toFixed(1)}%`}</span>
      <span className='text-muted-foreground'>{comparison !== null ? `vs ${columnLabel(result.columns[1]).toLowerCase()}` : 'vs target'}</span>
    </p>}
  </div>
}

function PivotTable({ chart, result }: { chart: ChartDefinition; result: DatasetQueryResult }) {
  const model = pivotModel(chart, result)
  const formatters = model.measures.map((_, index) => measureFormatter(chart, index))
  const formats = chart.style?.conditional_formats
  const span = formats?.length ? ranges(model.rows.filter(row => !row.subtotal).flatMap(row =>
    row.values.flatMap(cells => cells.map((value, m) => ({ field_id: model.measures[m].field_id, value }))))) : undefined
  const many = model.measures.length > 1
  const cell = (value: number | null, m: number, shaded: boolean) => <td key={m} style={shaded ? cellStyle(formats, model.measures[m].field_id, value,
    span?.get(model.measures[m].field_id ?? '')) : undefined}
    className='px-2 py-1.5 text-right whitespace-nowrap viz-numerals'>{value === null ? '' : formatters[m].format(value)}</td>
  const head = 'border-b px-2 py-1.5 font-medium text-muted-foreground whitespace-nowrap'
  return <div className='flex min-h-0 flex-1 flex-col'>
    <div className='min-h-0 flex-1 overflow-auto'>
      <table className='w-full border-collapse text-sm'>
        <thead className='sticky top-0 bg-card'>
          <tr>
            {model.rowHeaders.map(header => <th key={header} scope='col' rowSpan={many ? 2 : 1} className={cn(head, 'text-left align-bottom')}>{header}</th>)}
            {model.columnKeys.map(key => <th key={key.join('|')} scope='colgroup' colSpan={model.measures.length}
              className={cn(head, many ? 'text-center' : 'text-right')}>{key.join(' · ')}</th>)}
            {model.rowTotals && <th scope='colgroup' colSpan={model.measures.length} className={cn(head, many ? 'text-center' : 'text-right')}>Total</th>}
          </tr>
          {many && <tr>
            {[...model.columnKeys, ...(model.rowTotals ? [['Total']] : [])].flatMap(key => model.measures.map(measure =>
              <th key={`${key.join('|')}:${measure.label}`} scope='col' className={cn(head, 'text-right')}>{measure.label}</th>))}
          </tr>}
        </thead>
        <tbody>
          {model.rows.map((row, r) => <tr key={r} className={cn('border-b last:border-b-0 hover:bg-muted/40', row.subtotal && 'bg-muted/30 font-medium')}>
            {row.key.map((label, k) => k === 0 || !row.subtotal
              ? <th key={k} scope='row' className='px-2 py-1.5 text-left font-normal whitespace-nowrap'>{row.subtotal ? `${label} total` : label}</th>
              : <td key={k} />)}
            {row.values.flatMap((cells, c) => cells.map((value, m) => <Fragment key={`${c}:${m}`}>{cell(value, m, !row.subtotal)}</Fragment>))}
            {model.rowTotals && model.rowTotals[r].map((value, m) => <Fragment key={`t:${m}`}>{cell(value, m, false)}</Fragment>)}
          </tr>)}
          {model.columnTotals && <tr className='font-medium'>
            {model.rowHeaders.map((_, k) => <th key={k} scope='row' className='border-t px-2 py-1.5 text-left font-medium'>{k === 0 ? 'Total' : ''}</th>)}
            {model.columnTotals.flatMap((cells, c) => cells.map((value, m) => <td key={`${c}:${m}`}
              className='border-t px-2 py-1.5 text-right whitespace-nowrap viz-numerals'>{value === null ? '' : formatters[m].format(value)}</td>))}
            {model.rowTotals && model.measures.map((_, m) => {
              const total = model.columnTotals!.reduce<number | null>((sum, cells) => cells[m] === null ? sum : (sum ?? 0) + cells[m]!, null)
              return <td key={`g:${m}`} className='border-t px-2 py-1.5 text-right whitespace-nowrap viz-numerals'>{total === null ? '' : formatters[m].format(total)}</td>
            })}
          </tr>}
        </tbody>
      </table>
    </div>
    {model.truncatedColumns && <p className='shrink-0 pt-1 text-xs text-muted-foreground'>
      Showing the first {MAX_PIVOT_COLUMNS} column groups. Add a filter to narrow them.
    </p>}
  </div>
}

function ResultTable({ chart, result }: { chart: ChartDefinition; result: DatasetQueryResult }) {
  const pageSize = chart.encoding.kind === 'table' ? chart.encoding.page_size ?? 100 : 100
  const [page, setPage] = useState(0)
  const pages = Math.max(1, Math.ceil(result.rows.length / pageSize))
  const visible = result.rows.slice(page * pageSize, (page + 1) * pageSize)
  const measureIndex = (column: QueryColumn) => result.columns.filter(c => c.role === 'measure').indexOf(column)
  const formatters = result.columns.map(column => column.role === 'measure' ? measureFormatter(chart, measureIndex(column)) : null)
  const showTotal = chart.encoding.kind === 'table' && chart.encoding.grand_total
  const totals = result.columns.map(column => column.role === 'measure' && ['sum', 'count'].includes(column.aggregation ?? '')
    ? result.rows.reduce((sum, row) => sum + (cellNumber(row[result.columns.indexOf(column)]) ?? 0), 0) : null)
  const text = (cell: Cell, index: number) => {
    const number = cellNumber(cell)
    return number !== null && formatters[index] ? formatters[index]!.format(number) : cellLabel(cell, result.columns[index])
  }
  const formats = chart.style?.conditional_formats
  const span = formats?.length ? ranges(result.rows.flatMap(row => row.map((cell, c) =>
    ({ field_id: result.columns[c].role === 'measure' ? result.columns[c].field_id ?? null : null, value: cellNumber(cell) })))) : undefined
  const shade = (cell: Cell, c: number) => {
    const column = result.columns[c]
    if (column.role !== 'measure' || !column.field_id) return undefined
    return cellStyle(formats, column.field_id, cellNumber(cell), span?.get(column.field_id))
  }
  return <div className='flex min-h-0 flex-1 flex-col'>
    <div className='min-h-0 flex-1 overflow-auto'>
      <table className='w-full border-collapse text-sm'>
        <thead className='sticky top-0 bg-card'>
          <tr>{result.columns.map(column => <th key={column.key} scope='col'
            className={cn('border-b px-2 py-1.5 text-left font-medium text-muted-foreground whitespace-nowrap',
              column.role === 'measure' && 'text-right')}>
            {columnLabel(column)}
          </th>)}</tr>
        </thead>
        <tbody>
          {visible.map((row, r) => <tr key={r} className='border-b last:border-b-0 hover:bg-muted/40'>
            {row.map((cell, c) => <td key={c} style={shade(cell, c)} className={cn('px-2 py-1.5 whitespace-nowrap',
              result.columns[c].role === 'measure' && 'text-right viz-numerals')}>{text(cell, c)}</td>)}
          </tr>)}
          {showTotal && <tr className='font-medium'>
            {result.columns.map((column, c) => <td key={column.key} className={cn('border-t px-2 py-1.5',
              column.role === 'measure' && 'text-right viz-numerals')}>
              {c === 0 ? 'Total' : totals[c] !== null ? formatters[c]?.format(totals[c]!) : ''}
            </td>)}
          </tr>}
        </tbody>
      </table>
    </div>
    {pages > 1 && <div className='flex shrink-0 items-center justify-end gap-1 pt-1 text-xs text-muted-foreground'>
      <span>Page {page + 1} of {pages}</span>
      <Button variant='ghost' size='icon' className='size-8' aria-label='Previous page' disabled={page === 0}
        onClick={() => setPage(page - 1)}><ChevronLeft /></Button>
      <Button variant='ghost' size='icon' className='size-8' aria-label='Next page' disabled={page >= pages - 1}
        onClick={() => setPage(page + 1)}><ChevronRight /></Button>
    </div>}
  </div>
}

/** Renders one native chart from a governed query under the viewer's active role. */
export function NativeChart({ chart, draft, context, parameters, onSelectValue, selectedLabel, className }: Props) {
  let shapeError: string | null = null
  try {
    chartQueryShape(chart)
  } catch (error) {
    shapeError = queryError(error)
  }
  const query = useChartData({ chart, draft, context, parameters })
  const result = query.data
  const build = useCallback((tokens: VizTokens, width: number) => buildChartOption(chart, result!, tokens, selectedLabel ?? null, width || undefined), [chart, result, selectedLabel])
  const select = useCallback((mark: MarkSelection) => {
    if (!result || !onSelectValue) return
    const encoding = chart.encoding
    let columnIndex = 0
    if (mark.legend) {
      // Split series are named by the colour field; slices by the category field.
      if (encoding.kind === 'cartesian' && hasSplit(encoding)) columnIndex = encoding.dimensions.length
      else if (encoding.kind !== 'category') return
    }
    const column = result.columns[columnIndex]
    if (!column?.field_id || column.role !== 'dimension') return
    let row: DatasetQueryResult['rows'][number] | undefined
    if (mark.legend) row = result.rows.find(item => cellLabel(item[columnIndex], column) === mark.name)
    else if (encoding.kind === 'category' || encoding.kind === 'heatmap') row = result.rows[mark.dataIndex]
    else if (encoding.kind === 'cartesian') {
      row = result.rows.find(item => encoding.dimensions.map((_, i) => cellLabel(item[i], result.columns[i])).join(' · ') === mark.name)
    }
    if (!row) return
    const cell = row[columnIndex]
    const anchor = mark.x !== undefined && mark.y !== undefined ? { x: mark.x, y: mark.y } : undefined
    const how = { ...(mark.additive ? { additive: true } : {}), ...(mark.legend ? { fromLegend: true } : {}) }
    if (column.date_grain && cell && (cell.type === 'date' || cell.type === 'timestamp')) {
      const from = cell.value.slice(0, 10)
      onSelectValue({ field_id: column.field_id, value: cell, label: cellLabel(cell, column),
        period: { from, to: periodEnd(from, column.date_grain) }, anchor, ...how })
      return
    }
    onSelectValue({ field_id: column.field_id, value: cell, label: cellLabel(cell, column), anchor, ...how })
  }, [chart, result, onSelectValue])
  // A map point cross-filters by its group; coordinates alone are not a useful filter.
  const selectGroup = useCallback((row: number) => {
    const column = result?.columns[2]
    const cell = result?.rows[row]?.[2]
    if (!onSelectValue || chart.encoding.kind !== 'map' || !chart.encoding.group || !column?.field_id || cell === undefined) return
    onSelectValue({ field_id: column.field_id, value: cell, label: cellLabel(cell, column) })
  }, [chart, result, onSelectValue])

  const label = `${chart.title}: ${chart.visual} chart`
  if (shapeError) return <State tone='muted' text={shapeError} />
  if (query.isPending) return <div aria-busy='true' className='flex flex-1 flex-col gap-2 p-1' role='status' aria-label={`Loading ${chart.title}`}>
    <div className='h-4 w-1/3 animate-pulse rounded bg-muted' /><div className='min-h-0 flex-1 animate-pulse rounded bg-muted/60' />
  </div>
  if (query.isError) return <State tone='error' text={queryError(query.error)} retry={() => void query.refetch()} />
  if (!result) return null
  const empty = !result.rows.length
  return <div className={cn('relative flex min-h-0 flex-1 flex-col', className)}>
    {query.isFetching && <span role='status' className='sr-only'>Updating {chart.title}</span>}
    <div className={cn('flex min-h-0 flex-1 flex-col transition-opacity', query.isFetching && 'opacity-60')}>
      {empty && chart.visual !== 'kpi' ? <State tone='muted' text='No rows match the current filters.' />
        : chart.visual === 'kpi' ? <Kpi chart={chart} result={result} />
          : chart.visual === 'pivot' ? <PivotTable chart={chart} result={result} />
          : chart.visual === 'table' ? <ResultTable chart={chart} result={result} />
          : chart.visual === 'map' ? <NativeMap chart={chart} result={result} label={label} selectedLabel={selectedLabel} onSelectGroup={onSelectValue ? selectGroup : undefined} />
            : <>
              <EChartHost build={build} label={label} onSelect={onSelectValue ? select : undefined} />
              <DataAlternative title={chart.title} result={result} />
            </>}
    </div>
    {result.truncated && <p className='shrink-0 pt-1 text-xs text-warning-strong'>
      Showing the first {result.rows.length.toLocaleString()} rows. Add a filter to see everything.
    </p>}
  </div>
}

const ALTERNATIVE_ROWS = 50

/** The drawn chart's rows as a table for screen readers; colour never carries meaning alone. */
function DataAlternative({ title, result }: { title: string; result: DatasetQueryResult }) {
  const rows = result.rows.slice(0, ALTERNATIVE_ROWS)
  return <table className='sr-only'>
    <caption>{`Data for ${title}${result.rows.length > rows.length ? `, first ${rows.length} of ${result.rows.length} rows` : ''}`}</caption>
    <thead><tr>{result.columns.map(column => <th key={column.key} scope='col'>{columnLabel(column)}</th>)}</tr></thead>
    <tbody>{rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c}>{cellLabel(cell, result.columns[c])}</td>)}</tr>)}</tbody>
  </table>
}

function State({ text, tone, retry }: { text: string; tone: 'muted' | 'error'; retry?: () => void }) {
  return <div role={tone === 'error' ? 'alert' : undefined}
    className='flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-3 text-center text-sm text-muted-foreground'>
    <p>{text}</p>
    {retry && <Button variant='outline' size='sm' onClick={retry}>Retry</Button>}
  </div>
}
