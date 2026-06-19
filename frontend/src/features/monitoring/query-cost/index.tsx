import { Fragment, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  TrendingUp,
  Clock,
  AlertTriangle,
  CheckCircle,
  Database,
  Users,
  Activity,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react'
import {
  BarChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { SearchableSelect } from '@/components/ui/searchable-select'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'

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
  const [pageSize, setPageSize] = useState(50)
  const [userFilter, setUserFilter] = useState('')
  const [databaseFilter, setDatabaseFilter] = useState('')
  const [groupBy, setGroupBy] = useState<'hour' | 'day'>('hour')
  const [sortField, setSortField] = useState<SortField>('event_time')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

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
  })

  const userOptions = useMemo(() => {
    if (!historyQuery.data?.items) return []
    return [...new Set(historyQuery.data.items.map(i => i.user_name).filter(Boolean))] as string[]
  }, [historyQuery.data])

  const databaseOptions = useMemo(() => {
    if (!historyQuery.data?.items) return []
    return [...new Set(historyQuery.data.items.map(i => i.database_name).filter(Boolean))] as string[]
  }, [historyQuery.data])

  /* ---- derived ---- */

  const metrics = metricsQuery.data?.metrics
  const aggregation = aggregationQuery.data ?? []
  const items = historyQuery.data?.items ?? []
  const total = historyQuery.data?.total ?? 0
  const totalPages = Math.ceil(total / pageSize)

  const successRate =
    metrics && metrics.query_total > 0
      ? ((metrics.query_success / (metrics.query_total || 1)) * 100).toFixed(1)
      : null

  const sortedItems = useMemo(() => {
    const copy = [...items]
    copy.sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      if (sortField === 'event_time') {
        return dir * (new Date(a.event_time).getTime() - new Date(b.event_time).getTime())
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

  const handlePageSizeChange = (size: string) => {
    setPageSize(Number(size))
    setPage(1)
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
      return <ArrowUpDown className='ml-1 inline h-3 w-3 text-muted-foreground/50' />
    return sortDir === 'asc' ? (
      <ArrowUp className='ml-1 inline h-3 w-3' />
    ) : (
      <ArrowDown className='ml-1 inline h-3 w-3' />
    )
  }

  /* ---- render ---- */

  return (
    <div className='space-y-6'>
      {/* Header */}
      <div>
        <h3 className='text-lg font-medium'>Query Cost</h3>
        <p className='text-sm text-muted-foreground'>
          Analyze query resource consumption and cost breakdown.
        </p>
      </div>

      {/* ── Metric Cards ── */}
      <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-5'>
        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-muted p-2'>
                <Activity className='h-4 w-4 text-muted-foreground' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Total Queries
                </p>
                <p className='text-2xl font-bold'>
                  {metrics ? formatNumber(metrics.query_total) : '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-muted p-2'>
                <CheckCircle className='h-4 w-4 text-muted-foreground' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Success Rate
                </p>
                <p className='text-2xl font-bold'>
                  {successRate ? `${successRate}%` : '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-muted p-2'>
                <AlertTriangle className='h-4 w-4 text-muted-foreground' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Error Count
                </p>
                <p className='text-2xl font-bold'>
                  {metrics ? formatNumber(metrics.query_err) : '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-muted p-2'>
                <Clock className='h-4 w-4 text-muted-foreground' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Slow Queries
                </p>
                <p className='text-2xl font-bold'>
                  {metrics ? formatNumber(metrics.slow_query) : '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-muted p-2'>
                <Users className='h-4 w-4 text-muted-foreground' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Active Connections
                </p>
                <p className='text-2xl font-bold'>
                  {metrics ? formatNumber(metrics.connection_total) : '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Aggregation Chart ── */}
      <Card className='rounded-md border border-border'>
        <CardContent className='pt-6'>
          <div className='mb-4 flex items-center justify-between'>
            <div className='flex items-center gap-2'>
              <TrendingUp className='h-4 w-4 text-muted-foreground' />
              <h4 className='text-sm font-medium'>
                Query Volume &amp; Duration Over Time
              </h4>
            </div>
            <Select
              value={groupBy}
              onValueChange={(v: string) =>
                setGroupBy(v as 'hour' | 'day')
              }
            >
              <SelectTrigger className='w-[120px]'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='hour'>By Hour</SelectItem>
                <SelectItem value='day'>By Day</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {aggregationQuery.isLoading ? (
            <div className='flex h-[300px] items-center justify-center text-sm text-muted-foreground'>
              Loading chart data…
            </div>
          ) : aggregation.length === 0 ? (
            <div className='flex h-[300px] items-center justify-center text-sm text-muted-foreground'>
              No aggregation data available
            </div>
          ) : (
            <ResponsiveContainer width='100%' height={300}>
              <BarChart data={aggregation}>
                <CartesianGrid
                  strokeDasharray='3 3'
                  className='stroke-border'
                  vertical={false}
                />
                <XAxis
                  dataKey='period'
                  stroke='#888888'
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: string) => {
                    if (groupBy === 'hour') {
                      const d = new Date(v)
                      return isNaN(d.getTime())
                        ? v
                        : d.toLocaleTimeString('en-US', {
                            hour: '2-digit',
                            minute: '2-digit',
                          })
                    }
                    const d = new Date(v)
                    return isNaN(d.getTime())
                      ? v
                      : d.toLocaleDateString('en-US', {
                          month: 'short',
                          day: 'numeric',
                        })
                  }}
                />
                <YAxis
                  yAxisId='left'
                  stroke='#888888'
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => formatNumber(v)}
                />
                <YAxis
                  yAxisId='right'
                  orientation='right'
                  stroke='#888888'
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => formatDuration(v)}
                />
                <Tooltip content={<ChartTooltipContent />} />
                <Legend
                  wrapperStyle={{ fontSize: 12 }}
                  iconType='circle'
                  iconSize={8}
                />
                <Bar
                  yAxisId='left'
                  dataKey='query_count'
                  name='Query Count'
                  fill='#3b82f6'
                  radius={[4, 4, 0, 0]}
                  barSize={aggregation.length > 30 ? 8 : 20}
                />
                <Line
                  yAxisId='right'
                  type='monotone'
                  dataKey='avg_duration_ms'
                  name='Avg Duration (ms)'
                  stroke='#10b981'
                  strokeWidth={2}
                  dot={aggregation.length <= 30}
                  activeDot={{ r: 4 }}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* ── Filter Bar ── */}
      <div className='flex flex-wrap items-center gap-3'>
        <SearchableSelect
          options={userOptions}
          value={userFilter}
          onChange={(v) => { setUserFilter(v); setPage(1) }}
          label='User'
        />
        <SearchableSelect
          options={databaseOptions}
          value={databaseFilter}
          onChange={(v) => { setDatabaseFilter(v); setPage(1) }}
          label='Database'
        />
        <div className='ml-auto text-sm text-muted-foreground'>
          {total} {total === 1 ? 'query' : 'queries'}
        </div>
      </div>

      {/* ── Cost History Table ── */}
      <div className='rounded-md border border-border'>
        <div className='overflow-x-auto'>
          <table className='w-full'>
            <thead className='border-b border-border bg-muted/50'>
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
                  <td
                    colSpan={7}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    Loading…
                  </td>
                </tr>
              ) : sortedItems.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    No cost history found
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
                          <span className='text-xs'>
                            {item.database_name}
                          </span>
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
                        <Badge
                          variant={
                            item.status === 'SUCCESS'
                              ? 'default'
                              : 'destructive'
                          }
                          className={cn(
                            'text-xs',
                            item.status === 'SUCCESS' &&
                              'bg-primary text-primary-foreground hover:bg-primary/90'
                          )}
                        >
                          {item.status}
                        </Badge>
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
                                <span className='font-mono'>
                                  {item.query_id}
                                </span>
                              </div>
                              <div>
                                <span className='font-medium'>Log ID:</span>{' '}
                                <span className='font-mono'>
                                  {item.log_id}
                                </span>
                              </div>
                              <div>
                                <span className='font-medium'>User:</span>{' '}
                                {item.user_name}
                              </div>
                              <div>
                                <span className='font-medium'>
                                  Database:
                                </span>{' '}
                                {item.database_name}
                              </div>
                              <div>
                                <span className='font-medium'>
                                  Duration:
                                </span>{' '}
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
        </div>
      </div>

      {/* ── Pagination ── */}
      <div className='flex items-center justify-between gap-4'>
        <div className='flex items-center gap-2 text-sm'>
          <span className='text-muted-foreground'>Rows per page:</span>
          <Select
            value={String(pageSize)}
            onValueChange={handlePageSizeChange}
          >
            <SelectTrigger className='w-[70px]'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='25'>25</SelectItem>
              <SelectItem value='50'>50</SelectItem>
              <SelectItem value='100'>100</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className='flex items-center gap-2'>
          <span className='text-sm text-muted-foreground'>
            Page {page} of {totalPages || 1}
          </span>
          <div className='flex items-center gap-1'>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(1)}
              disabled={page === 1}
              className='h-8 w-8'
            >
              <ChevronsLeft className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(page - 1)}
              disabled={page === 1}
              className='h-8 w-8'
            >
              <ChevronLeft className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(page + 1)}
              disabled={page >= totalPages}
              className='h-8 w-8'
            >
              <ChevronRight className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(totalPages)}
              disabled={page >= totalPages}
              className='h-8 w-8'
            >
              <ChevronsRight className='h-4 w-4' />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
