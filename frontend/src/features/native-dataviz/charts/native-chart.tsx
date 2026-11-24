import { Fragment, useCallback, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { ChartDefinition, DatasetQueryResult, FilterTree, ParameterInput, QueryColumn, QuerySort } from '../contracts'
import { buildChartOption } from './chart-options'
import { cellLabel, cellNumber, chartQueryShape, columnLabel, hasSplit, periodEnd, type Cell } from './chart-query'
import { chartRequest, combinesOther, MAX_PAGE_OFFSET, queryError, remainderOf, useChartData, useTableTotals, type TablePage } from './chart-data'
import { EChartHost, type MarkSelection, type VizTokens } from './echart-host'
import { cellStyle, matchedRule, MAX_PIVOT_COLUMNS, pivotModel, ranges, ruleMeaning } from './pivot'
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
  /** An unsaved chart in a Studio answer: rows come from its draft query as the viewer. */
  draftRef?: string
}

/** A measure's number format; `short` is its compact form (175.2M) for a value that does not fit. */
function measureFormatter(chart: ChartDefinition, index: number, short = false) {
  const encoding = chart.encoding
  const format = encoding.kind === 'single_measure'
    ? (index === 0 ? encoding.measure.format : encoding.comparison?.format)
    : 'measures' in encoding ? encoding.measures?.[index]?.format : undefined
  const compact: Intl.NumberFormatOptions = short ? { notation: 'compact', maximumFractionDigits: Math.min(format?.decimals ?? 1, 1) } : {}
  if (!format) return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2, ...compact })
  const options: Intl.NumberFormatOptions = { maximumFractionDigits: format.decimals ?? 2, minimumFractionDigits: 0, ...compact }
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
  return <div className='flex min-h-0 min-w-0 flex-1 flex-col justify-center gap-1 px-1'>
    {value === null
      ? <p className='viz-numerals truncate text-3xl font-medium tabular-nums' data-testid='kpi-value'><span aria-hidden>–</span></p>
      : <KpiValue full={formatter.format(value)} short={measureFormatter(chart, 0, true).format(value)} />}
    {value === null && <p className='text-sm text-muted-foreground' role='status'>No data for the current filters</p>}
    {delta !== null && Number.isFinite(delta) && <p className={cn('flex items-center gap-1 text-sm',
      delta >= 0 ? 'text-success-strong' : 'text-destructive')}>
      {delta >= 0 ? <ArrowUp className='size-4' aria-hidden /> : <ArrowDown className='size-4' aria-hidden />}
      <span>{`${delta >= 0 ? '+' : ''}${(delta * 100).toFixed(1)}%`}</span>
      <span className='text-muted-foreground'>{comparison !== null ? `vs ${columnLabel(result.columns[1]).toLowerCase()}` : 'vs target'}</span>
    </p>}
  </div>
}

/**
 * A KPI value in full, or in its compact form when the full one does not fit
 * the box. Cutting a number short would misread it. Screen readers and the
 * tooltip keep the full value.
 */
function KpiValue({ full, short }: { full: string; short: string }) {
  const box = useRef<HTMLParagraphElement>(null)
  const probe = useRef<HTMLSpanElement>(null)
  const [compact, setCompact] = useState(false)
  useLayoutEffect(() => {
    const node = box.current
    const measure = probe.current
    if (!node || !measure || full === short) { setCompact(false); return }
    const check = () => setCompact(measure.offsetWidth > node.clientWidth)
    check()
    const observer = new ResizeObserver(check)
    observer.observe(node)
    return () => observer.disconnect()
  }, [full, short])
  return <>
    <span ref={probe} aria-hidden className='viz-numerals pointer-events-none invisible absolute text-3xl font-medium whitespace-nowrap tabular-nums'>{full}</span>
    <p ref={box} className='viz-numerals truncate text-3xl font-medium tabular-nums' data-testid='kpi-value' title={compact ? full : undefined}>
      {compact ? <><span aria-hidden>{short}</span><span className='sr-only'>{full}</span></> : full}
    </p>
  </>
}

/** A value a highlight rule matched: a marker and words, so the meaning does not rely on colour. */
function Flagged({ meaning, children }: { meaning: string | undefined; children: ReactNode }) {
  if (!meaning) return children
  return <span className='inline-flex items-center gap-1.5' title={meaning} data-highlighted=''>
    <span aria-hidden className='size-1.5 shrink-0 rounded-full bg-current' />
    {children}<span className='sr-only'>, {meaning.charAt(0).toLowerCase() + meaning.slice(1)}</span>
  </span>
}

function PivotTable({ chart, result }: { chart: ChartDefinition; result: DatasetQueryResult }) {
  const model = pivotModel(chart, result)
  const formatters = model.measures.map((_, index) => measureFormatter(chart, index))
  const formats = chart.style?.conditional_formats
  const span = formats?.length ? ranges(model.rows.filter(row => !row.subtotal).flatMap(row =>
    row.values.flatMap(cells => cells.map((value, m) => ({ field_id: model.measures[m].field_id, value }))))) : undefined
  const many = model.measures.length > 1
  const cell = (value: number | null, m: number, shaded: boolean) => {
    const rule = shaded ? matchedRule(formats, model.measures[m].field_id, value) : undefined
    return <td key={m} style={shaded ? cellStyle(formats, model.measures[m].field_id, value,
      span?.get(model.measures[m].field_id ?? '')) : undefined}
      className='px-2 py-1.5 text-right whitespace-nowrap viz-numerals'>
      <Flagged meaning={rule && ruleMeaning(rule, formatters[m].format)}>{value === null ? '' : formatters[m].format(value)}</Flagged>
    </td>
  }
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

type TablePaging = {
  /** Where the shown page starts in the server's order. */
  offset: number
  hasNext: boolean
  busy: boolean
  onPrevious: () => void
  onNext: () => void
}

type TableProps = {
  chart: ChartDefinition
  result: DatasetQueryResult
  /** Server pages; without it the whole result is paged in the browser. */
  paging: TablePaging | null
  sort: QuerySort | null
  onSort: (column: string) => void
  /** Measure totals over every matching row, when the rows arrive a page at a time. */
  totalsResult?: DatasetQueryResult
}

function ResultTable({ chart, result, paging, sort, onSort, totalsResult }: TableProps) {
  const pageSize = chart.encoding.kind === 'table' ? chart.encoding.page_size ?? 100 : 100
  const [localPage, setLocalPage] = useState(0)
  const localPages = Math.max(1, Math.ceil(result.rows.length / pageSize))
  const page = Math.min(localPage, localPages - 1)
  const visible = paging ? result.rows : result.rows.slice(page * pageSize, (page + 1) * pageSize)
  const first = paging ? paging.offset : page * pageSize
  const measureIndex = (column: QueryColumn) => result.columns.filter(c => c.role === 'measure').indexOf(column)
  const formatters = result.columns.map(column => column.role === 'measure' ? measureFormatter(chart, measureIndex(column)) : null)
  const showTotal = chart.encoding.kind === 'table' && chart.encoding.grand_total
  const additive = (column: QueryColumn) => column.role === 'measure' && ['sum', 'count'].includes(column.aggregation ?? '')
  const totals = result.columns.map(column => {
    if (!additive(column)) return null
    if (totalsResult) {
      const index = totalsResult.columns.findIndex(item => item.key === column.key)
      return index < 0 ? null : cellNumber(totalsResult.rows[0]?.[index] ?? null)
    }
    return result.rows.reduce((sum, row) => sum + (cellNumber(row[result.columns.indexOf(column)]) ?? 0), 0)
  })
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
  const meaning = (cell: Cell, c: number) => {
    const column = result.columns[c]
    const formatter = formatters[c]
    if (column.role !== 'measure' || !column.field_id || !formatter) return undefined
    const rule = matchedRule(formats, column.field_id, cellNumber(cell))
    return rule && ruleMeaning(rule, formatter.format)
  }
  const hasPrevious = paging ? paging.offset > 0 : page > 0
  const hasNext = paging ? paging.hasNext : page < localPages - 1
  const range = `Rows ${(first + 1).toLocaleString()}–${(first + visible.length).toLocaleString()}`
  return <div className='flex min-h-0 flex-1 flex-col'>
    <div className='min-h-0 flex-1 overflow-auto'>
      <table className='w-full border-collapse text-sm'>
        <thead className='sticky top-0 bg-card'>
          <tr>{result.columns.map(column => {
            const direction = sort?.column === column.key ? sort.direction : null
            const Arrow = direction === 'desc' ? ArrowDown : ArrowUp
            return <th key={column.key} scope='col'
              aria-sort={direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : undefined}
              className={cn('border-b px-2 py-1.5 text-left font-medium text-muted-foreground whitespace-nowrap',
                column.role === 'measure' && 'text-right')}>
              <button type='button' onClick={() => onSort(column.key)}
                className='inline-flex items-center gap-1 rounded-sm hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'>
                {columnLabel(column)}
                {direction && <Arrow className='size-3.5' aria-hidden />}
              </button>
            </th>
          })}</tr>
        </thead>
        <tbody>
          {visible.map((row, r) => <tr key={r} className='border-b last:border-b-0 hover:bg-muted/40'>
            {row.map((cell, c) => <td key={c} style={shade(cell, c)} className={cn('px-2 py-1.5 whitespace-nowrap',
              result.columns[c].role === 'measure' && 'text-right viz-numerals')}><Flagged meaning={meaning(cell, c)}>{text(cell, c)}</Flagged></td>)}
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
    {(hasPrevious || hasNext) && <div className='flex shrink-0 items-center justify-end gap-1 pt-1 text-xs text-muted-foreground'>
      <span className='viz-numerals'>{paging ? range : `${range} of ${result.rows.length.toLocaleString()}`}</span>
      <Button variant='ghost' size='icon' className='size-8' aria-label='Previous page' disabled={!hasPrevious || paging?.busy}
        onClick={() => paging ? paging.onPrevious() : setLocalPage(page - 1)}><ChevronLeft /></Button>
      <Button variant='ghost' size='icon' className='size-8' aria-label='Next page' disabled={!hasNext || paging?.busy}
        onClick={() => paging ? paging.onNext() : setLocalPage(page + 1)}><ChevronRight /></Button>
    </div>}
  </div>
}

/** A table over a large result reads one server page at a time, in the query's total order. */
function useTableView(chart: ChartDefinition, base: string) {
  const pageSize = chart.encoding.kind === 'table' ? chart.encoding.page_size ?? 100 : 100
  const rowLimit = Math.min(chart.row_limit ?? 1000, MAX_PAGE_OFFSET)
  // Table calculations need every row at once, so those tables keep browser paging.
  const paged = chart.visual === 'table' && !chart.table_calculations?.length
  const sortScope = `${chart.chart_id}:${JSON.stringify(chart.encoding)}:${JSON.stringify(chart.sort ?? [])}`
  const [position, setPosition] = useState({ base, index: 0 })
  const [sorting, setSorting] = useState<{ scope: string; sort: QuerySort } | null>(null)
  const viewerSort = sorting?.scope === sortScope ? sorting.sort : null
  // New filters, parameters or a new definition start again at the first page.
  const index = position.base === base ? position.index : 0
  const offset = paged ? index * pageSize : 0
  const page = useMemo<TablePage | null>(() => {
    if (chart.visual !== 'table') return null
    if (paged) return { offset, limit: Math.min(pageSize, rowLimit - offset), sort: viewerSort }
    return viewerSort ? { offset: 0, limit: rowLimit, sort: viewerSort } : null
  }, [chart.visual, paged, offset, pageSize, rowLimit, viewerSort])
  let chartSort: QuerySort | null = null
  try {
    chartSort = chartQueryShape(chart).sort?.[0] ?? null
  } catch {
    chartSort = null
  }
  return {
    paged, page, offset, rowLimit,
    sort: viewerSort ?? chartSort,
    goTo: (next: number) => setPosition({ base, index: Math.max(0, next) }),
    index,
    sortBy: (column: string) => {
      const current = viewerSort ?? chartSort
      const direction = current?.column === column && current.direction === 'asc' ? 'desc' : 'asc'
      setSorting({ scope: sortScope, sort: { column, direction } })
      setPosition({ base, index: 0 })
    },
  }
}

/** Renders one native chart from a governed query under the viewer's active role. */
export function NativeChart({ chart, draft, context, parameters, onSelectValue, selectedLabel, className, draftRef }: Props) {
  let shapeError: string | null = null
  try {
    chartQueryShape(chart)
  } catch (error) {
    shapeError = queryError(error)
  }
  const base = useMemo(() => JSON.stringify(chartRequest({ chart, draft, context, parameters })),
    [chart, draft, context, parameters])
  const table = useTableView(chart, base)
  const query = useChartData({ chart, draft, context, parameters, page: table.page, draftRef })
  const result = query.data
  const pageOffset = result?.offset ?? 0
  const pageEnd = pageOffset + (result?.rows.length ?? 0)
  const hasNextPage = Boolean(table.paged && result?.truncated && result.next_offset != null && pageEnd < table.rowLimit)
  const multiPage = table.paged && (pageOffset > 0 || Boolean(result?.truncated))
  const showTotal = chart.encoding.kind === 'table' && Boolean(chart.encoding.grand_total)
  const totals = useTableTotals({ chart, draft, context, parameters, enabled: showTotal && multiPage, draftRef })
  // A capped pie that combines the rest asks the engine for the whole, under the same filters.
  const combine = combinesOther(chart) && Boolean(result?.truncated)
  const whole = useTableTotals({ chart, draft, context, parameters, enabled: combine, draftRef })
  const remainder = combine && result ? remainderOf(result, whole.data) : null
  const build = useCallback((tokens: VizTokens, width: number) => buildChartOption(chart, result!, tokens, selectedLabel ?? null, width || undefined, remainder),
    [chart, result, selectedLabel, remainder])
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
    <div className='h-4 w-1/3 motion-safe:animate-pulse rounded bg-muted' /><div className='min-h-0 flex-1 motion-safe:animate-pulse rounded bg-muted/60' />
  </div>
  if (query.isError) return <State tone='error' text={queryError(query.error, draftRef ? 'draft' : 'dataset')} retry={() => void query.refetch()} />
  if (!result) return null
  const empty = !result.rows.length
  // Clicking filters other charts by a category; a chart of totals alone has none to give.
  const unfilterable = onSelectValue && !result.columns.some(column => column.role === 'dimension' && column.field_id)
    ? NO_CATEGORY : undefined
  // Downloads wait while this is set, so an image never shows an older result.
  const updating = query.isFetching || whole.isFetching
  return <div className={cn('relative flex min-h-0 min-w-0 flex-1 flex-col', className)} title={unfilterable}
    data-updating={updating ? '' : undefined}>
    {query.isFetching && <span role='status' className='sr-only'>Updating {chart.title}</span>}
    <div className={cn('flex min-h-0 min-w-0 flex-1 flex-col motion-safe:transition-opacity', query.isFetching && 'opacity-60')}>
      {empty && chart.visual !== 'kpi' ? <State tone='muted' text='No rows match the current filters.' />
        : chart.visual === 'kpi' ? <Kpi chart={chart} result={result} />
          : chart.visual === 'pivot' ? <PivotTable chart={chart} result={result} />
          : chart.visual === 'table' ? <ResultTable chart={chart} result={result} sort={table.sort} onSort={table.sortBy}
            totalsResult={multiPage ? totals.data : undefined}
            paging={table.paged ? { offset: pageOffset, hasNext: hasNextPage, busy: query.isPlaceholderData,
              onPrevious: () => table.goTo(table.index - 1), onNext: () => table.goTo(table.index + 1) } : null} />
          : chart.visual === 'map' ? <NativeMap chart={chart} result={result} label={label} selectedLabel={selectedLabel} onSelectGroup={onSelectValue ? selectGroup : undefined} />
            : <>
              <EChartHost build={build} label={label} onSelect={onSelectValue ? select : undefined} />
              <DataAlternative title={chart.title} result={result} />
            </>}
    </div>
    {result.truncated && !hasNextPage && (chart.encoding.kind === 'category'
      ? <p className='shrink-0 pt-1 text-xs text-muted-foreground'>
        {remainder !== null ? `Showing the top ${result.rows.length} and the rest as Other.` : `Showing the top ${result.rows.length} only.`}
      </p>
      : <p className='shrink-0 pt-1 text-xs text-warning-strong'>
        Showing the first {(table.paged ? pageEnd : result.rows.length).toLocaleString()} rows. Add a filter to see everything.
      </p>)}
  </div>
}

const ALTERNATIVE_ROWS = 50

const NO_CATEGORY = 'This chart shows totals only, so clicking it cannot filter other charts.'

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
