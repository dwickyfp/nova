import { Fragment, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Upload,
  CheckCircle,
  XCircle,
  Clock,
  Database,
  Table2,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react'
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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DataLoadItem = {
  label: string
  db_name: string
  table_name: string
  load_type: string
  state: string
  progress: number
  create_time: string
  load_start_time: string | null
  load_commit_time: string | null
  load_finish_time: string | null
  error_msg: string | null
  sink_rows: number
  scan_rows: number
}

type DataLoadsResponse = {
  items: DataLoadItem[]
  total: number
}

type DataLoadStats = {
  total: number
  finished: number
  cancelled: number
  loading: number
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatShortTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function computeDuration(item: DataLoadItem): string {
  if (item.load_finish_time) {
    const ms =
      new Date(item.load_finish_time).getTime() -
      new Date(item.create_time).getTime()
    if (ms < 1000) return `${ms}ms`
    const seconds = ms / 1000
    if (seconds < 60) return `${seconds.toFixed(1)}s`
    const minutes = Math.floor(seconds / 60)
    const secs = Math.round(seconds % 60)
    if (minutes < 60) return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`
    const hours = Math.floor(minutes / 60)
    const mins = minutes % 60
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
  }
  return 'In progress'
}

function truncate(value: string, max: number = 40) {
  if (value.length <= max) return value
  return value.slice(0, max).trimEnd() + '…'
}

function stateBadgeClasses(state: string) {
  const s = state.toUpperCase()
  if (s === 'FINISHED')
    return 'bg-chart-2/10 text-chart-2 border-chart-2/20'
  if (s === 'CANCELLED')
    return 'bg-destructive/10 text-destructive border-destructive/20'
  if (s === 'LOADING')
    return 'bg-warning/10 text-warning border-warning/20'
  return 'bg-muted text-muted-foreground border-border'
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MonitoringDataLoads() {
  // Pagination
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)

  // Filters
  const [stateFilter, setStateFilter] = useState<string>('')
  const [dbFilter, setDbFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  // Expansion
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  // ----- Queries -----------------------------------------------------------

  const statsQuery = useQuery<DataLoadStats>({
    queryKey: ['monitoring-loads-stats'],
    queryFn: () => api.get<DataLoadStats>('/monitoring/loads/stats'),
    refetchInterval: 10_000,
  })

  const loadsQuery = useQuery<DataLoadsResponse>({
    queryKey: [
      'monitoring-loads',
      page,
      pageSize,
      stateFilter,
      dbFilter,
      typeFilter,
    ],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
      })
      if (stateFilter) params.set('state', stateFilter)
      if (dbFilter) params.set('db_name', dbFilter)
      if (typeFilter) params.set('load_type', typeFilter)
      return api.get<DataLoadsResponse>(`/monitoring/loads?${params.toString()}`)
    },
    refetchInterval: 8_000,
  })

  // ----- Derived data ------------------------------------------------------

  const items = loadsQuery.data?.items ?? []
  const total = loadsQuery.data?.total ?? 0
  const totalPages = Math.ceil(total / pageSize)
  const stats = statsQuery.data

  const databaseOptions = useMemo(() => {
    if (!loadsQuery.data?.items) return []
    return [...new Set(loadsQuery.data.items.map(i => i.db_name).filter(Boolean))] as string[]
  }, [loadsQuery.data])

  const typeOptions = useMemo(() => {
    if (!loadsQuery.data?.items) return []
    return [...new Set(loadsQuery.data.items.map(i => i.load_type).filter(Boolean))] as string[]
  }, [loadsQuery.data])

  // ----- Error toasts ------------------------------------------------------

  useEffect(() => {
    if (loadsQuery.error) {
      toast.error('Failed to load data loads', {
        description: (loadsQuery.error as Error).message,
      })
    }
  }, [loadsQuery.error])

  useEffect(() => {
    if (statsQuery.error) {
      toast.error('Failed to load load statistics', {
        description: (statsQuery.error as Error).message,
      })
    }
  }, [statsQuery.error])

  // ----- Handlers ----------------------------------------------------------

  const handlePageChange = (p: number) => {
    setPage(p)
    setExpandedRow(null)
  }

  const handlePageSizeChange = (size: string) => {
    setPageSize(Number(size))
    setPage(1)
    setExpandedRow(null)
  }

  // ----- Render ------------------------------------------------------------

  return (
    <div className='space-y-6'>
      {/* Header */}
      <div>
        <h3 className='text-lg font-medium'>Data Loads</h3>
        <p className='text-sm text-muted-foreground'>
          Track data import and load operations with status and progress.
        </p>
      </div>

      {/* Stats Cards */}
      <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
        {/* Total */}
        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-muted p-2'>
                <Upload className='h-4 w-4 text-muted-foreground' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Total Loads
                </p>
                <p className='text-2xl font-bold'>{stats?.total ?? '—'}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Finished */}
        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-chart-2/10 p-2'>
                <CheckCircle className='h-4 w-4 text-chart-2' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Finished
                </p>
                <p className='text-2xl font-bold text-chart-2'>
                  {stats?.finished ?? '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Cancelled */}
        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-destructive/10 p-2'>
                <XCircle className='h-4 w-4 text-destructive' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Cancelled
                </p>
                <p className='text-2xl font-bold text-destructive'>
                  {stats?.cancelled ?? '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* In Progress */}
        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-warning/10 p-2'>
                <Clock className='h-4 w-4 text-warning' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  In Progress
                </p>
                <p className='text-2xl font-bold text-warning'>
                  {stats?.loading ?? '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filter Bar */}
      <div className='flex flex-wrap items-center gap-3'>
        <SearchableSelect
          options={['FINISHED', 'CANCELLED', 'LOADING']}
          value={stateFilter}
          onChange={(v) => { setStateFilter(v); setPage(1) }}
          label='State'
        />
        <SearchableSelect
          options={databaseOptions}
          value={dbFilter}
          onChange={(v) => { setDbFilter(v); setPage(1) }}
          label='Database'
        />
        <SearchableSelect
          options={typeOptions}
          value={typeFilter}
          onChange={(v) => { setTypeFilter(v); setPage(1) }}
          label='Type'
        />

        <div className='ml-auto text-sm text-muted-foreground'>
          {total} {total === 1 ? 'load' : 'loads'}
        </div>
      </div>

      {/* Data Table */}
      <div className='rounded-md border border-border'>
        <div className='overflow-x-auto'>
          <table className='w-full'>
            <thead className='border-b border-border bg-muted/50'>
              <tr>
                <th className='w-8 px-3 py-3' />
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Label
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Database
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Table
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Type
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  State
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Progress
                </th>
                <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                  Scan Rows
                </th>
                <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                  Sink Rows
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Created
                </th>
                <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                  Duration
                </th>
                <th className='px-4 py-3 text-center text-xs font-medium text-muted-foreground'>
                  Error
                </th>
              </tr>
            </thead>
            <tbody>
              {loadsQuery.isLoading ? (
                <tr>
                  <td
                    colSpan={12}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    Loading data loads…
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td
                    colSpan={12}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    No data loads found
                  </td>
                </tr>
              ) : (
                items.map((item) => {
                  const rowKey = `${item.label}-${item.create_time}`
                  const isExpanded = expandedRow === rowKey
                  const isFinished = item.state.toUpperCase() === 'FINISHED'
                  const isCancelled = item.state.toUpperCase() === 'CANCELLED'
                  const isLoading = item.state.toUpperCase() === 'LOADING'
                  const progressPct = Math.round(
                    (typeof item.progress === 'number' ? item.progress : 0) * 100
                  )

                  return (
                    <Fragment key={rowKey}>
                      <tr
                        onClick={() =>
                          setExpandedRow(isExpanded ? null : rowKey)
                        }
                        className={cn(
                          'cursor-pointer border-b border-border transition-colors hover:bg-muted/50',
                          isExpanded && 'bg-muted/30'
                        )}
                      >
                        {/* Expand chevron */}
                        <td className='px-3 py-3'>
                          {isExpanded ? (
                            <ChevronDown className='h-4 w-4 text-muted-foreground' />
                          ) : (
                            <ChevronRight className='h-4 w-4 text-muted-foreground' />
                          )}
                        </td>

                        {/* Label (truncated) */}
                        <td className='px-4 py-3'>
                          <span
                            className='block max-w-[200px] truncate text-sm font-medium'
                            title={item.label}
                          >
                            {truncate(item.label)}
                          </span>
                        </td>

                        {/* Database */}
                        <td className='px-4 py-3'>
                          <div className='flex items-center gap-1.5'>
                            <Database className='h-3 w-3 text-muted-foreground' />
                            <span className='text-xs'>{item.db_name}</span>
                          </div>
                        </td>

                        {/* Table */}
                        <td className='px-4 py-3'>
                          <div className='flex items-center gap-1.5'>
                            <Table2 className='h-3 w-3 text-muted-foreground' />
                            <span className='text-xs'>{item.table_name}</span>
                          </div>
                        </td>

                        {/* Type */}
                        <td className='px-4 py-3'>
                          <Badge variant='secondary' className='text-xs'>
                            {item.load_type}
                          </Badge>
                        </td>

                        {/* State badge */}
                        <td className='px-4 py-3'>
                          <Badge
                            variant='outline'
                            className={cn(
                              'text-xs',
                              stateBadgeClasses(item.state)
                            )}
                          >
                            {isLoading && (
                              <span className='mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-warning' />
                            )}
                            {item.state}
                          </Badge>
                        </td>

                        {/* Progress */}
                        <td className='px-4 py-3'>
                          <div className='flex items-center gap-2'>
                            <div className='h-1.5 w-16 overflow-hidden rounded-full bg-muted'>
                              <div
                                className={cn(
                                  'h-full rounded-full transition-all',
                                  isFinished && 'bg-chart-2',
                                  isCancelled && 'bg-destructive',
                                  isLoading && 'bg-warning',
                                  !isFinished &&
                                    !isCancelled &&
                                    !isLoading &&
                                    'bg-muted-foreground'
                                )}
                                style={{ width: `${progressPct}%` }}
                              />
                            </div>
                            <span className='text-xs tabular-nums text-muted-foreground'>
                              {progressPct}%
                            </span>
                          </div>
                        </td>

                        {/* Scan Rows */}
                        <td className='px-4 py-3 text-right text-xs tabular-nums text-muted-foreground'>
                          {(item.scan_rows ?? 0).toLocaleString()}
                        </td>

                        {/* Sink Rows */}
                        <td className='px-4 py-3 text-right text-xs tabular-nums text-muted-foreground'>
                          {(item.sink_rows ?? 0).toLocaleString()}
                        </td>

                        {/* Created */}
                        <td className='whitespace-nowrap px-4 py-3 text-xs text-muted-foreground'>
                          {formatShortTime(item.create_time)}
                        </td>

                        {/* Duration */}
                        <td className='px-4 py-3 text-right text-xs font-medium'>
                          {computeDuration(item)}
                        </td>

                        {/* Error indicator */}
                        <td className='px-4 py-3 text-center'>
                          {item.error_msg ? (
                            <AlertTriangle className='inline h-4 w-4 text-destructive' />
                          ) : (
                            <span className='text-muted-foreground'>—</span>
                          )}
                        </td>
                      </tr>

                      {/* Expanded detail row */}
                      {isExpanded && (
                        <tr className='border-b border-border bg-muted/20'>
                          <td colSpan={12} className='px-6 py-4'>
                            <div className='space-y-3'>
                              {/* Full label */}
                              <div>
                                <p className='mb-1 text-xs font-medium text-muted-foreground'>
                                  Full Label
                                </p>
                                <code className='block break-all rounded-md bg-muted px-3 py-2 text-xs font-mono'>
                                  {item.label}
                                </code>
                              </div>

                              {/* Timing details */}
                              <div className='flex flex-wrap gap-x-8 gap-y-2 text-xs text-muted-foreground'>
                                <div>
                                  <span className='font-medium'>Created:</span>{' '}
                                  {formatTime(item.create_time)}
                                </div>
                                {item.load_start_time && (
                                  <div>
                                    <span className='font-medium'>
                                      Load Start:
                                    </span>{' '}
                                    {formatTime(item.load_start_time)}
                                  </div>
                                )}
                                {item.load_commit_time && (
                                  <div>
                                    <span className='font-medium'>
                                      Commit Time:
                                    </span>{' '}
                                    {formatTime(item.load_commit_time)}
                                  </div>
                                )}
                                {item.load_finish_time && (
                                  <div>
                                    <span className='font-medium'>
                                      Finish Time:
                                    </span>{' '}
                                    {formatTime(item.load_finish_time)}
                                  </div>
                                )}
                                <div>
                                  <span className='font-medium'>Duration:</span>{' '}
                                  {computeDuration(item)}
                                </div>
                              </div>

                              {/* Rows summary */}
                              <div className='flex flex-wrap gap-x-8 gap-y-2 text-xs text-muted-foreground'>
                                <div>
                                  <span className='font-medium'>Scan Rows:</span>{' '}
                                  {(item.scan_rows ?? 0).toLocaleString()}
                                </div>
                                <div>
                                  <span className='font-medium'>Sink Rows:</span>{' '}
                                  {(item.sink_rows ?? 0).toLocaleString()}
                                </div>
                                <div>
                                  <span className='font-medium'>Progress:</span>{' '}
                                  {progressPct}%
                                </div>
                              </div>

                              {/* Error message */}
                              {item.error_msg && (
                                <div>
                                  <p className='mb-1.5 flex items-center gap-1.5 text-xs font-medium text-destructive'>
                                    <AlertTriangle className='h-3 w-3' />
                                    Error Message
                                  </p>
                                  <pre className='overflow-auto rounded-md bg-destructive/10 p-3 text-xs font-mono text-destructive'>
                                    {item.error_msg}
                                  </pre>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
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
              <ChevronRight className='h-4 w-4 rotate-180' />
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
