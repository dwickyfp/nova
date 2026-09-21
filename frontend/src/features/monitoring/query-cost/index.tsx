import { Fragment, useEffect, useMemo, useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  TrendingUp,
  Clock,
  AlertTriangle,
  CheckCircle,
  Database,
  SearchX,
  Users,
  Activity,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react'
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { useTheme } from '@/context/theme-provider'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import {
  LoadingLines,
  LoadingOverlay,
} from '@/components/ui/loading-overlay'
import { MetricCard } from '@/components/ui/metric-card'
import { PageHeader } from '@/components/ui/page-header'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
import { statusTone } from '../components/status-tone'

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type CostHistoryItem = {
  log_id: string
  event_time: string
  user_name: string
  database_name: string
  sql_text: string
  status: string
  duration_ms: number
  rows_affected: number
  query_id: string
}

type CostHistoryResponse = {
  items: CostHistoryItem[]
  total: number
}

type AggregationItem = {
  period: string
  query_count: number
  avg_duration_ms: number
  total_rows: number
  error_count: number
}

type FEMetricsResponse = {
  metrics: {
    query_total: number
    query_success: number
    query_err: number
    slow_query: number
    connection_total: number
  }
}

type SortField = 'event_time' | 'duration_ms' | 'rows_affected'
type SortDir = 'asc' | 'desc'

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatTime(isoString: string) {
  return new Date(isoString).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const formatDuration = (ms: number | null | undefined) => {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function truncateSql(sql: string, max = 80) {
  if (sql.length <= max) return sql
  return sql.slice(0, max).trim() + '...'
}

function formatNumber(n: number | null | undefined) {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

function formatPeriodLabel(period: string, groupBy: 'hour' | 'day') {
  const date = new Date(period)
  if (Number.isNaN(date.getTime())) return period

  if (groupBy === 'hour') {
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  })
}

/**
 * Recharts needs concrete colour strings, not classes, so the chart reads the
 * resolved token values off the document instead of carrying its own palette.
 * That keeps dark mode automatic when a token changes.
 */
function readToken(name: string, fallback: string) {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  return value || fallback
}

function getChartColors() {
  return {
    barTop: readToken('--chart-1', '#f05a47'),
    barBottom: readToken('--chart-4', '#4f7ee8'),
    line: readToken('--chart-2', '#14a89a'),
    dotFill: readToken('--card', '#ffffff'),
    dotStroke: readToken('--chart-2', '#14a89a'),
    activeDotStroke: readToken('--foreground', '#0f172a'),
    axis: readToken('--muted-foreground', '#64748b'),
    grid: readToken('--border', '#d9e2ec'),
    tooltipBg: readToken('--popover', '#ffffff'),
    tooltipBorder: readToken('--border', '#d9e2ec'),
  }
}

/* ------------------------------------------------------------------ */
/*  Chart tooltip                                                      */
/* ------------------------------------------------------------------ */

function ChartTooltipContent({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className='rounded-md border border-border bg-popover p-3 shadow-md'>
      <p className='mb-1.5 text-xs font-medium text-muted-foreground'>
        {label}
      </p>
      {payload.map((entry) => (
        <div key={entry.name} className='flex items-center gap-2 text-xs'>
          <span
            className='h-2 w-2 rounded-full'
            style={{ backgroundColor: entry.color }}
          />
          <span className='text-muted-foreground'>{entry.name}:</span>
          <span className='font-medium'>
            {entry.name === 'Avg Duration (ms)'
              ? formatDuration(entry.value)
              : formatNumber(entry.value)}
          </span>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function MonitoringQueryCost() {
  /* ---- state ---- */
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [userFilter, setUserFilter] = useState('')
  const [databaseFilter, setDatabaseFilter] = useState('')
  const [groupBy, setGroupBy] = useState<'hour' | 'day'>('hour')
  const [sortField, setSortField] = useState<SortField>('event_time')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const { resolvedTheme } = useTheme()

  // Tokens resolve to different values per theme, so the chart re-reads them
  // when the theme flips instead of caching one palette.
  const chartColors = useMemo(() => getChartColors(), [resolvedTheme])

  /* ---- queries ---- */

  const metricsQuery = useQuery<FEMetricsResponse>({
    queryKey: ['monitoring', 'cost', 'metrics'],
    queryFn: () => api.get<FEMetricsResponse>('/monitoring/metrics/fe'),
  })

  const aggregationQuery = useQuery<AggregationItem[]>({
    queryKey: ['monitoring', 'cost', 'aggregation', groupBy],
    queryFn: () =>
      api.get<AggregationItem[]>(
        `/monitoring/cost/aggregation?group_by=${groupBy}`
      ),
  })

  const historyQuery = useQuery<CostHistoryResponse>({
    queryKey: [
      'monitoring',
      'cost',
      'history',
      page,
      pageSize,
      userFilter,
      databaseFilter,
    ],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
      })
      if (userFilter) params.set('user_name', userFilter)
      if (databaseFilter) params.set('database_name', databaseFilter)
      return api.get<CostHistoryResponse>(
        `/monitoring/cost/history?${params.toString()}`
      )
    },
    placeholderData: keepPreviousData,
  })

  const userOptions = useMemo(() => {
    if (!historyQuery.data?.items) return []
    return [
      ...new Set(
        historyQuery.data.items.map((i) => i.user_name).filter(Boolean)
      ),
    ] as string[]
  }, [historyQuery.data])

  const databaseOptions = useMemo(() => {
    if (!historyQuery.data?.items) return []
    return [
      ...new Set(
        historyQuery.data.items.map((i) => i.database_name).filter(Boolean)
      ),
    ] as string[]
  }, [historyQuery.data])

  /* ---- derived ---- */

  const metrics = metricsQuery.data?.metrics
  const aggregation = aggregationQuery.data ?? []
  const items = historyQuery.data?.items ?? []
  const total = historyQuery.data?.total ?? 0

  const successRate =
    metrics && metrics.query_total > 0
      ? ((metrics.query_success / (metrics.query_total || 1)) * 100).toFixed(1)
      : null

  const sortedItems = useMemo(() => {
    const copy = [...items]
    copy.sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      if (sortField === 'event_time') {
        return (
          dir *
          (new Date(a.event_time).getTime() - new Date(b.event_time).getTime())
        )
      }
      return dir * (a[sortField] - b[sortField])
    })
    return copy
  }, [items, sortField, sortDir])

  /* ---- error handling ---- */

  useEffect(() => {
    if (metricsQuery.error) {
      toast.error('Failed to load FE metrics', {
        description: metricsQuery.error.message,
      })
    }
  }, [metricsQuery.error])

  useEffect(() => {
    if (aggregationQuery.error) {
      toast.error('Failed to load aggregation data', {
        description: aggregationQuery.error.message,
      })
    }
  }, [aggregationQuery.error])

  useEffect(() => {
    if (historyQuery.error) {
      toast.error('Failed to load cost history', {
        description: historyQuery.error.message,
      })
    }
  }, [historyQuery.error])

  /* ---- handlers ---- */

  const handlePageChange = (p: number) => {
    setPage(p)
    setExpandedRow(null)
  }

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir('desc')
    }
  }

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field)
      return (
        <ArrowUpDown className='ml-1 inline h-3 w-3 text-muted-foreground/50' />
      )
    return sortDir === 'asc' ? (
      <ArrowUp className='ml-1 inline h-3 w-3' />
    ) : (
      <ArrowDown className='ml-1 inline h-3 w-3' />
    )
  }

  /* ---- render ---- */

  return (
    <div className='flex flex-col gap-6'>
      <PageHeader
        title='Query Cost'
        description='Analyze query resource consumption and cost breakdown.'
      />

      {metricsQuery.isError ? (
        <EmptyState
          variant='error'
          icon={AlertTriangle}
          title='Could not load query metrics'
          description='The monitoring API did not respond. Check the connection and retry.'
          action={
            <Button
              variant='outline'
              size='sm'
              onClick={() => void metricsQuery.refetch()}
            >
              Retry
            </Button>
          }
        />
      ) : (
        /*
         * Weight follows the decision: errors and slow queries are what you
         * act on, totals and connections are context. Six equal cards read as
         * "nothing here matters".
         */
        <div className='grid gap-4 lg:grid-cols-3'>
          <MetricCard
            weight='primary'
            label='Error Count'
            value={metrics ? formatNumber(metrics.query_err) : '—'}
            icon={AlertTriangle}
            tone='danger'
            hint='Queries that failed in this window'
          />
          <MetricCard
            weight='primary'
            label='Slow Queries'
            value={metrics ? formatNumber(metrics.slow_query) : '—'}
            icon={Clock}
            tone='warning'
            hint='Above the slow-query threshold'
          />
          <MetricCard
            weight='primary'
            label='Success Rate'
            value={successRate ? `${successRate}%` : '—'}
            icon={CheckCircle}
            tone='success'
            hint={`${metrics ? formatNumber(metrics.query_success) : '—'} of ${metrics ? formatNumber(metrics.query_total) : '—'} succeeded`}
          />
          <MetricCard
            weight='compact'
            label='Total Queries'
            value={metrics ? formatNumber(metrics.query_total) : '—'}
            icon={Activity}
            tone='info'
          />
          <MetricCard
            weight='compact'
            label='Active Connections'
            value={metrics ? formatNumber(metrics.connection_total) : '—'}
            icon={Users}
          />
        </div>
      )}

      <Card className='rounded-2xl border-border/70 bg-card/90 shadow-sm'>
        <CardContent className='p-6 sm:p-7'>
          <div className='mb-5 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between'>
            <div className='flex items-center gap-2'>
              <TrendingUp className='h-4 w-4 text-muted-foreground' />
              <h4 className='text-sm font-medium'>
                Query Volume &amp; Duration Over Time
              </h4>
            </div>
            <Select
              value={groupBy}
              onValueChange={(v: string) => setGroupBy(v as 'hour' | 'day')}
            >
              <SelectTrigger className='h-10 w-[132px] rounded-xl border-border/70 bg-background/70'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='hour'>By Hour</SelectItem>
                <SelectItem value='day'>By Day</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {aggregationQuery.isLoading ? (
            <LoadingOverlay
              label='Loading chart data'
              className='min-h-[340px] '
            />
          ) : aggregationQuery.isError ? (
            <EmptyState
              variant='error'
              icon={AlertTriangle}
              title='Could not load chart data'
              description='The aggregation endpoint did not respond. Check the connection and retry.'
              className='min-h-[340px] justify-center'
              action={
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => void aggregationQuery.refetch()}
                >
                  Retry
                </Button>
              }
            />
          ) : aggregation.length === 0 ? (
            <EmptyState
              icon={TrendingUp}
              title='No activity in this range'
              description='Nothing ran in the selected hour or day buckets. Widen the grouping or run a query.'
              className='min-h-[340px] justify-center'
            />
          ) : (
            <div className='rounded-2xl border border-border/60 bg-background/35 p-3 sm:p-4'>
              <ResponsiveContainer width='100%' height={340}>
                <ComposedChart
                  data={aggregation}
                  margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient
                      id='query-cost-bar'
                      x1='0'
                      y1='0'
                      x2='0'
                      y2='1'
                    >
                      <stop
                        offset='0%'
                        stopColor={chartColors.barTop}
                        stopOpacity={0.95}
                      />
                      <stop
                        offset='100%'
                        stopColor={chartColors.barBottom}
                        stopOpacity={0.78}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    stroke={chartColors.grid}
                    strokeDasharray='4 6'
                    vertical={false}
                  />
                  <XAxis
                    dataKey='period'
                    stroke={chartColors.axis}
                    fontSize={11}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(value: string) =>
                      formatPeriodLabel(value, groupBy)
                    }
                  />
                  <YAxis
                    yAxisId='left'
                    stroke={chartColors.axis}
                    fontSize={11}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(value: number) => formatNumber(value)}
                  />
                  <YAxis
                    yAxisId='right'
                    orientation='right'
                    stroke={chartColors.axis}
                    fontSize={11}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(value: number) => formatDuration(value)}
                  />
                  <Tooltip content={<ChartTooltipContent />} />
                  <Bar
                    yAxisId='left'
                    dataKey='query_count'
                    name='Query Count'
                    fill='url(#query-cost-bar)'
                    radius={[8, 8, 0, 0]}
                    barSize={aggregation.length > 30 ? 8 : 18}
                  />
                  <Line
                    yAxisId='right'
                    type='monotone'
                    dataKey='avg_duration_ms'
                    name='Avg Duration (ms)'
                    stroke={chartColors.line}
                    strokeWidth={3}
                    dot={{
                      r: aggregation.length <= 18 ? 4 : 0,
                      fill: chartColors.dotFill,
                      stroke: chartColors.dotStroke,
                      strokeWidth: 2,
                    }}
                    activeDot={{
                      r: 5,
                      fill: chartColors.line,
                      stroke: chartColors.activeDotStroke,
                      strokeWidth: 2,
                    }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Filter Bar ── */}
      <SimpleTableToolbar
        resultLabel={`${total} ${total === 1 ? 'query' : 'queries'}`}
        filters={[
          {
            label: 'User',
            value: userFilter,
            options: userOptions,
            onChange: (value) => {
              setUserFilter(value)
              setPage(1)
            },
          },
          {
            label: 'Database',
            value: databaseFilter,
            options: databaseOptions,
            onChange: (value) => {
              setDatabaseFilter(value)
              setPage(1)
            },
          },
        ]}
      />

      {/* ── Cost History Table ── */}
      <SimpleTableViewport className='min-h-[320px] shrink-0'>
        <table className='w-full'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Time
                <button
                  type='button'
                  onClick={() => toggleSort('event_time')}
                  className='ml-0.5 inline-flex'
                >
                  <SortIcon field='event_time' />
                </button>
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                User
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Database
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                SQL
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                <button
                  type='button'
                  onClick={() => toggleSort('duration_ms')}
                  className='inline-flex items-center'
                >
                  Duration
                  <SortIcon field='duration_ms' />
                </button>
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                <button
                  type='button'
                  onClick={() => toggleSort('rows_affected')}
                  className='inline-flex items-center'
                >
                  Rows
                  <SortIcon field='rows_affected' />
                </button>
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Status
              </th>
            </tr>
          </thead>
          <tbody>
            {historyQuery.isLoading ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
                  <LoadingLines rows={5} />
                </td>
              </tr>
            ) : historyQuery.isError ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
                  <EmptyState
                    variant='error'
                    icon={AlertTriangle}
                    title='Could not load cost history'
                    description='The monitoring API did not respond. Check the connection and retry.'
                    action={
                      <Button
                        variant='outline'
                        size='sm'
                        onClick={() => void historyQuery.refetch()}
                      >
                        Retry
                      </Button>
                    }
                  />
                </td>
              </tr>
            ) : sortedItems.length === 0 ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
                  <EmptyState
                    icon={SearchX}
                    title={
                      userFilter || databaseFilter
                        ? 'No queries match these filters'
                        : 'No cost history yet'
                    }
                    description={
                      userFilter || databaseFilter
                        ? 'Clear the user or database filter to widen the search.'
                        : 'Cost history builds from executed queries. Run SQL to populate it.'
                    }
                    action={
                      userFilter || databaseFilter ? (
                        <Button
                          variant='outline'
                          size='sm'
                          onClick={() => {
                            setUserFilter('')
                            setDatabaseFilter('')
                            setPage(1)
                          }}
                        >
                          Clear filters
                        </Button>
                      ) : undefined
                    }
                  />
                </td>
              </tr>
            ) : (
              sortedItems.map((item) => (
                <Fragment key={item.log_id}>
                  <tr
                    onClick={() =>
                      setExpandedRow(
                        expandedRow === item.log_id ? null : item.log_id
                      )
                    }
                    className={cn(
                      'cursor-pointer border-b border-border transition-colors hover:bg-muted/50',
                      expandedRow === item.log_id && 'bg-muted/30'
                    )}
                  >
                    <td className='whitespace-nowrap px-4 py-3 text-xs text-muted-foreground'>
                      {formatTime(item.event_time)}
                    </td>
                    <td className='px-4 py-3 text-sm'>
                      <div className='flex items-center gap-1.5'>
                        <Users className='h-3 w-3 text-muted-foreground' />
                        <span className='text-xs'>{item.user_name}</span>
                      </div>
                    </td>
                    <td className='px-4 py-3 text-sm'>
                      <div className='flex items-center gap-1.5'>
                        <Database className='h-3 w-3 text-muted-foreground' />
                        <span className='text-xs'>{item.database_name}</span>
                      </div>
                    </td>
                    <td className='px-4 py-3 text-sm'>
                      <code className='rounded bg-muted px-1.5 py-0.5 font-mono text-xs'>
                        {truncateSql(item.sql_text)}
                      </code>
                    </td>
                    <td className='px-4 py-3 text-right text-xs font-medium'>
                      <span
                        className={cn(
                          item.duration_ms > 5000 &&
                            'font-semibold text-destructive'
                        )}
                      >
                        {formatDuration(item.duration_ms)}
                      </span>
                    </td>
                    <td className='px-4 py-3 text-right text-xs text-muted-foreground'>
                      {(item.rows_affected ?? 0).toLocaleString()}
                    </td>
                    <td className='px-4 py-3'>
                      <StatusBadge tone={statusTone(item.status)}>
                        {item.status}
                      </StatusBadge>
                    </td>
                  </tr>
                  {expandedRow === item.log_id && (
                    <tr className='border-b border-border bg-muted/20'>
                      <td colSpan={7} className='px-4 py-4'>
                        <div className='space-y-3'>
                          <div>
                            <p className='mb-1.5 text-xs font-medium text-muted-foreground'>
                              Full SQL Query
                            </p>
                            <pre className='max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono text-xs'>
                              {item.sql_text}
                            </pre>
                          </div>
                          <div className='flex flex-wrap gap-4 text-xs text-muted-foreground'>
                            <div>
                              <span className='font-medium'>Query ID:</span>{' '}
                              <span className='font-mono'>{item.query_id}</span>
                            </div>
                            <div>
                              <span className='font-medium'>Log ID:</span>{' '}
                              <span className='font-mono'>{item.log_id}</span>
                            </div>
                            <div>
                              <span className='font-medium'>User:</span>{' '}
                              {item.user_name}
                            </div>
                            <div>
                              <span className='font-medium'>Database:</span>{' '}
                              {item.database_name}
                            </div>
                            <div>
                              <span className='font-medium'>Duration:</span>{' '}
                              {formatDuration(item.duration_ms)}
                            </div>
                            <div>
                              <span className='font-medium'>
                                Rows Affected:
                              </span>{' '}
                              {(item.rows_affected ?? 0).toLocaleString()}
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </SimpleTableViewport>

      {/* ── Pagination ── */}
      <SimpleTablePagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={handlePageChange}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
          setExpandedRow(null)
        }}
      />
    </div>
  )
}
