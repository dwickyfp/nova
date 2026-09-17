import { Fragment, useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Database,
  SearchX,
  Table2,
  Upload,
  XCircle,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines, RefreshBanner } from '@/components/ui/loading-overlay'
import { PageHeader } from '@/components/ui/page-header'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
import { statusTone } from '../components/status-tone'

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

function formatTime(iso: string | null) {
  if (!iso) return '—'

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
  const endTime = item.load_finish_time ?? item.load_commit_time

  if (!endTime) return 'In progress'

  const ms = new Date(endTime).getTime() - new Date(item.create_time).getTime()
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

function truncate(value: string, max: number = 80) {
  if (value.length <= max) return value
  return `${value.slice(0, max).trimEnd()}...`
}

/** The progress bar mirrors the status tone so the two never disagree. */
const PROGRESS_TONE_CLASS = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-destructive',
  info: 'bg-info',
  neutral: 'bg-muted-foreground',
  primary: 'bg-primary',
} as const

export function MonitoringDataLoads() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [stateFilter, setStateFilter] = useState<string>('')
  const [dbFilter, setDbFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

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
      return api.get<DataLoadsResponse>(
        `/monitoring/loads?${params.toString()}`
      )
    },
    placeholderData: keepPreviousData,
  })

  useEffect(() => {
    if (loadsQuery.error) {
      toast.error('Failed to load data loads', {
        description: loadsQuery.error.message,
      })
    }
  }, [loadsQuery.error])

  const items = loadsQuery.data?.items ?? []
  const filteredItems = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLowerCase()
    if (!normalizedSearch) return items

    return items.filter((item) =>
      [
        item.label,
        item.db_name,
        item.table_name,
        item.load_type,
        item.state,
        item.error_msg ?? '',
      ].some((value) => value.toLowerCase().includes(normalizedSearch))
    )
  }, [items, searchQuery])

  const total = loadsQuery.data?.total ?? 0

  const databaseOptions = useMemo(() => {
    if (!loadsQuery.data?.items) return []
    return [
      ...new Set(
        loadsQuery.data.items.map((item) => item.db_name).filter(Boolean)
      ),
    ] as string[]
  }, [loadsQuery.data])

  const typeOptions = useMemo(() => {
    if (!loadsQuery.data?.items) return []
    return [
      ...new Set(
        loadsQuery.data.items.map((item) => item.load_type).filter(Boolean)
      ),
    ] as string[]
  }, [loadsQuery.data])

  const handlePageChange = (newPage: number) => {
    setPage(newPage)
    setExpandedRow(null)
  }

  const handleFilterChange = (
    setter: (value: string) => void,
    value: string
  ) => {
    setter(value)
    setPage(1)
    setExpandedRow(null)
  }

  return (
    <div className='space-y-6'>
      <PageHeader
        title='Data Loads'
        description='Monitor load jobs, progress, and failures in one consistent view.'
      />

      <SimpleTableToolbar
        search={searchQuery}
        onSearchChange={(value) => {
          setSearchQuery(value)
          setExpandedRow(null)
        }}
        searchPlaceholder='Search label, table, error...'
        resultLabel={`${total} ${total === 1 ? 'load' : 'loads'}`}
        filters={[
          {
            label: 'State',
            value: stateFilter,
            options: ['FINISHED', 'CANCELLED', 'LOADING'],
            onChange: (value) => handleFilterChange(setStateFilter, value),
            icon: <CheckCircle2 size={14} />,
          },
          {
            label: 'Database',
            value: dbFilter,
            options: databaseOptions,
            onChange: (value) => handleFilterChange(setDbFilter, value),
            icon: <Database size={14} />,
          },
          {
            label: 'Type',
            value: typeFilter,
            options: typeOptions,
            onChange: (value) => handleFilterChange(setTypeFilter, value),
            icon: <Upload size={14} />,
          },
        ]}
      />

      <SimpleTableViewport>
        {loadsQuery.isFetching && !loadsQuery.isLoading ? (
          <RefreshBanner label='Loading data loads...' />
        ) : null}

        <table className='w-full'>
          <thead>
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
                Status
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
                <td colSpan={12} className='px-4 py-6'>
                  <LoadingLines rows={5} />
                </td>
              </tr>
            ) : loadsQuery.isError ? (
              <tr>
                <td colSpan={12} className='px-4 py-6'>
                  <EmptyState
                    variant='error'
                    icon={AlertTriangle}
                    title='Could not load data loads'
                    description='The monitoring API did not respond. Check the connection and retry.'
                    action={
                      <Button
                        variant='outline'
                        size='sm'
                        onClick={() => void loadsQuery.refetch()}
                      >
                        Retry
                      </Button>
                    }
                  />
                </td>
              </tr>
            ) : filteredItems.length === 0 ? (
              <tr>
                <td colSpan={12} className='px-4 py-6'>
                  <EmptyState
                    icon={SearchX}
                    title={
                      searchQuery || stateFilter || dbFilter || typeFilter
                        ? 'No loads match these filters'
                        : 'No load jobs recorded yet'
                    }
                    description={
                      searchQuery || stateFilter || dbFilter || typeFilter
                        ? 'Clear the state, database, or type filter to widen the search.'
                        : 'Loads appear here as soon as data is ingested into a table.'
                    }
                    action={
                      searchQuery || stateFilter || dbFilter || typeFilter ? (
                        <Button
                          variant='outline'
                          size='sm'
                          onClick={() => {
                            setSearchQuery('')
                            setStateFilter('')
                            setDbFilter('')
                            setTypeFilter('')
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
              filteredItems.map((item) => {
                const rowKey = `${item.label}-${item.create_time}`
                const isExpanded = expandedRow === rowKey
                const progressPct = Math.round(
                  (typeof item.progress === 'number' ? item.progress : 0) * 100
                )
                const hasError = Boolean(item.error_msg)
                const normalizedState = item.state.toUpperCase()

                return (
                  <Fragment key={rowKey}>
                    <tr
                      onClick={() => setExpandedRow(isExpanded ? null : rowKey)}
                      className={cn(
                        'cursor-pointer border-b border-border transition-colors hover:bg-muted/50',
                        isExpanded && 'bg-muted/30'
                      )}
                    >
                      <td className='px-3 py-3 text-muted-foreground'>
                        {isExpanded ? (
                          <ChevronDown className='h-4 w-4' />
                        ) : (
                          <ChevronRight className='h-4 w-4' />
                        )}
                      </td>

                      <td className='px-4 py-3'>
                        <span
                          className='block max-w-[240px] truncate text-sm font-medium'
                          title={item.label}
                        >
                          {truncate(item.label)}
                        </span>
                      </td>

                      <td className='px-4 py-3'>
                        <div className='flex items-center gap-1.5 text-xs text-muted-foreground'>
                          <Database className='h-3.5 w-3.5' />
                          <span>{item.db_name}</span>
                        </div>
                      </td>

                      <td className='px-4 py-3'>
                        <div className='flex items-center gap-1.5 text-xs text-muted-foreground'>
                          <Table2 className='h-3.5 w-3.5' />
                          <span>{item.table_name}</span>
                        </div>
                      </td>

                      <td className='px-4 py-3'>
                        <Badge variant='secondary' className='text-xs'>
                          {item.load_type}
                        </Badge>
                      </td>

                      <td className='px-4 py-3'>
                        <StatusBadge tone={statusTone(item.state)}>
                          {normalizedState === 'FINISHED' ? (
                            <CheckCircle2 className='h-3.5 w-3.5' />
                          ) : null}
                          {normalizedState === 'LOADING' ? (
                            <Clock3 className='h-3.5 w-3.5' />
                          ) : null}
                          {normalizedState === 'CANCELLED' ? (
                            <XCircle className='h-3.5 w-3.5' />
                          ) : null}
                          {item.state}
                        </StatusBadge>
                      </td>

                      <td className='px-4 py-3'>
                        <div className='flex items-center gap-2'>
                          <div className='h-1.5 w-20 overflow-hidden rounded-full bg-muted'>
                            <div
                              className={cn(
                                'h-full rounded-full transition-all',
                                PROGRESS_TONE_CLASS[statusTone(item.state)]
                              )}
                              style={{ width: `${progressPct}%` }}
                            />
                          </div>
                          <span className='text-xs tabular-nums text-muted-foreground'>
                            {progressPct}%
                          </span>
                        </div>
                      </td>

                      <td className='px-4 py-3 text-right text-xs tabular-nums text-muted-foreground'>
                        {(item.scan_rows ?? 0).toLocaleString()}
                      </td>

                      <td className='px-4 py-3 text-right text-xs tabular-nums text-muted-foreground'>
                        {(item.sink_rows ?? 0).toLocaleString()}
                      </td>

                      <td className='whitespace-nowrap px-4 py-3 text-xs text-muted-foreground'>
                        {formatShortTime(item.create_time)}
                      </td>

                      <td className='px-4 py-3 text-right text-xs font-medium'>
                        {computeDuration(item)}
                      </td>

                      <td className='px-4 py-3 text-center'>
                        {hasError ? (
                          <AlertTriangle className='mx-auto h-4 w-4 text-destructive' />
                        ) : (
                          <span className='text-muted-foreground'>—</span>
                        )}
                      </td>
                    </tr>

                    {isExpanded ? (
                      <tr className='border-b border-border bg-muted/20'>
                        <td colSpan={12} className='px-6 py-4'>
                          <div className='space-y-4'>
                            <div>
                              <p className='mb-1.5 text-xs font-medium text-muted-foreground'>
                                Full Label
                              </p>
                              <code className='block break-all rounded-md bg-muted px-3 py-2 text-xs font-mono'>
                                {item.label}
                              </code>
                            </div>

                            <div className='grid gap-3 text-xs text-muted-foreground sm:grid-cols-2 xl:grid-cols-3'>
                              <div>
                                <span className='font-medium text-foreground'>
                                  Created:
                                </span>{' '}
                                {formatTime(item.create_time)}
                              </div>
                              <div>
                                <span className='font-medium text-foreground'>
                                  Load Start:
                                </span>{' '}
                                {formatTime(item.load_start_time)}
                              </div>
                              <div>
                                <span className='font-medium text-foreground'>
                                  Commit Time:
                                </span>{' '}
                                {formatTime(item.load_commit_time)}
                              </div>
                              <div>
                                <span className='font-medium text-foreground'>
                                  Finish Time:
                                </span>{' '}
                                {formatTime(item.load_finish_time)}
                              </div>
                              <div>
                                <span className='font-medium text-foreground'>
                                  Duration:
                                </span>{' '}
                                {computeDuration(item)}
                              </div>
                              <div>
                                <span className='font-medium text-foreground'>
                                  Progress:
                                </span>{' '}
                                {progressPct}%
                              </div>
                              <div>
                                <span className='font-medium text-foreground'>
                                  Scan Rows:
                                </span>{' '}
                                {(item.scan_rows ?? 0).toLocaleString()}
                              </div>
                              <div>
                                <span className='font-medium text-foreground'>
                                  Sink Rows:
                                </span>{' '}
                                {(item.sink_rows ?? 0).toLocaleString()}
                              </div>
                            </div>

                            {item.error_msg ? (
                              <div>
                                <p className='mb-1.5 flex items-center gap-1.5 text-xs font-medium text-destructive'>
                                  <AlertTriangle className='h-3.5 w-3.5' />
                                  Error Message
                                </p>
                                <pre className='overflow-auto rounded-md bg-destructive/10 p-3 text-xs font-mono text-destructive'>
                                  {item.error_msg}
                                </pre>
                              </div>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                )
              })
            )}
          </tbody>
        </table>
      </SimpleTableViewport>

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
