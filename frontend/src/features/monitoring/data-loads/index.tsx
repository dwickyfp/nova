import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Database,
  SearchX,
  Table2,
  Upload,
  XCircle,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import {
  formatDateTime,
  formatDuration,
  formatNumber,
  truncate,
} from '@/lib/format'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines, RefreshBanner } from '@/components/ui/loading-overlay'
import { PageHeader } from '@/components/ui/page-header'
import { StatusBadge } from '@/components/ui/status-badge'
import { statusLabel, statusTone } from '@/components/ui/status-tone'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
} from '@/components/ui/table'
import {
  SimpleTablePagination,
  SimpleTableToolbar,
} from '@/components/data-table/simple-table-controls'
import {
  DetailBlock,
  DetailFields,
  ExpandableRow,
  MonitoringTableHeader,
  MonitoringTableViewport,
  TableMessageRow,
} from '../components/expandable-row'

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

/** Eleven data columns plus the details toggle. */
const COLUMN_COUNT = 12

/** Elapsed time from creation to finish, or commit when it has not finished. */
function loadDuration(item: DataLoadItem): string {
  const endTime = item.load_finish_time ?? item.load_commit_time
  if (!endTime) return 'In progress'
  return formatDuration(
    new Date(endTime).getTime() - new Date(item.create_time).getTime()
  )
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
  const hasFilters = Boolean(searchQuery || stateFilter || dbFilter || typeFilter)
  // Search runs over the page already loaded, so while it is active the
  // label counts matches on this page instead of the server total.
  const resultLabel = searchQuery.trim()
    ? `${filteredItems.length} of ${items.length} on this page`
    : `${total} ${total === 1 ? 'load' : 'loads'}`

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
        resultLabel={resultLabel}
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

      <MonitoringTableViewport>
        {loadsQuery.isFetching && !loadsQuery.isLoading ? (
          <RefreshBanner label='Loading data loads...' />
        ) : null}

        <Table>
          <MonitoringTableHeader>
            <TableHead>Label</TableHead>
            <TableHead>Database</TableHead>
            <TableHead>Table</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Progress</TableHead>
            <TableHead className='text-right'>Scan Rows</TableHead>
            <TableHead className='text-right'>Sink Rows</TableHead>
            <TableHead>Created</TableHead>
            <TableHead className='text-right'>Duration</TableHead>
            <TableHead className='text-center'>Error</TableHead>
          </MonitoringTableHeader>
          <TableBody>
            {loadsQuery.isLoading ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <LoadingLines rows={5} />
              </TableMessageRow>
            ) : loadsQuery.isError ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
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
              </TableMessageRow>
            ) : filteredItems.length === 0 ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <EmptyState
                  icon={SearchX}
                  title={
                    hasFilters
                      ? 'No loads match these filters'
                      : 'No load jobs recorded yet'
                  }
                  description={
                    hasFilters
                      ? 'Clear the state, database, or type filter to widen the search.'
                      : 'Loads appear here as soon as data is ingested into a table.'
                  }
                  action={
                    hasFilters ? (
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
              </TableMessageRow>
            ) : (
              filteredItems.map((item) => {
                const rowKey = `${item.label}-${item.create_time}`
                const progressPct = Math.round(
                  (typeof item.progress === 'number' ? item.progress : 0) * 100
                )
                const hasError = Boolean(item.error_msg)
                const normalizedState = item.state.toUpperCase()
                const duration = loadDuration(item)

                return (
                  <ExpandableRow
                    key={rowKey}
                    expanded={expandedRow === rowKey}
                    onToggle={() =>
                      setExpandedRow(expandedRow === rowKey ? null : rowKey)
                    }
                    label={`load ${item.label}`}
                    colSpan={COLUMN_COUNT}
                    detail={
                      <>
                        <DetailBlock label='Full Label'>{item.label}</DetailBlock>
                        <DetailFields
                          fields={[
                            {
                              label: 'Created',
                              value: formatDateTime(item.create_time, {
                                seconds: true,
                              }),
                            },
                            {
                              label: 'Load Start',
                              value: formatDateTime(item.load_start_time, {
                                seconds: true,
                              }),
                            },
                            {
                              label: 'Commit Time',
                              value: formatDateTime(item.load_commit_time, {
                                seconds: true,
                              }),
                            },
                            {
                              label: 'Finish Time',
                              value: formatDateTime(item.load_finish_time, {
                                seconds: true,
                              }),
                            },
                            { label: 'Duration', value: duration },
                            { label: 'Progress', value: `${progressPct}%` },
                            {
                              label: 'Scan Rows',
                              value: formatNumber(item.scan_rows ?? 0),
                            },
                            {
                              label: 'Sink Rows',
                              value: formatNumber(item.sink_rows ?? 0),
                            },
                          ]}
                        />
                        {item.error_msg ? (
                          <DetailBlock label='Error Message' tone='error'>
                            {item.error_msg}
                          </DetailBlock>
                        ) : null}
                      </>
                    }
                  >
                    <TableCell className='px-4 py-3'>
                      <span
                        className='block max-w-[240px] truncate text-sm font-medium'
                        title={item.label}
                      >
                        {truncate(item.label)}
                      </span>
                    </TableCell>

                    <TableCell className='px-4 py-3'>
                      <div className='flex items-center gap-1.5 text-xs text-muted-foreground'>
                        <Database className='size-3.5' />
                        <span>{item.db_name}</span>
                      </div>
                    </TableCell>

                    <TableCell className='px-4 py-3'>
                      <div className='flex items-center gap-1.5 text-xs text-muted-foreground'>
                        <Table2 className='size-3.5' />
                        <span>{item.table_name}</span>
                      </div>
                    </TableCell>

                    <TableCell className='px-4 py-3'>
                      <Badge variant='secondary' className='text-xs'>
                        {item.load_type}
                      </Badge>
                    </TableCell>

                    <TableCell className='px-4 py-3'>
                      <StatusBadge tone={statusTone(item.state)}>
                        {normalizedState === 'FINISHED' ? (
                          <CheckCircle2 className='size-3.5' />
                        ) : null}
                        {normalizedState === 'LOADING' ? (
                          <Clock3 className='size-3.5' />
                        ) : null}
                        {normalizedState === 'CANCELLED' ? (
                          <XCircle className='size-3.5' />
                        ) : null}
                        {statusLabel(item.state)}
                      </StatusBadge>
                    </TableCell>

                    <TableCell className='px-4 py-3'>
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
                    </TableCell>

                    <TableCell className='px-4 py-3 text-right text-xs tabular-nums text-muted-foreground'>
                      {formatNumber(item.scan_rows ?? 0)}
                    </TableCell>

                    <TableCell className='px-4 py-3 text-right text-xs tabular-nums text-muted-foreground'>
                      {formatNumber(item.sink_rows ?? 0)}
                    </TableCell>

                    <TableCell className='px-4 py-3 text-xs text-muted-foreground'>
                      {formatDateTime(item.create_time)}
                    </TableCell>

                    <TableCell className='px-4 py-3 text-right text-xs font-medium'>
                      {duration}
                    </TableCell>

                    <TableCell className='px-4 py-3 text-center'>
                      {hasError ? (
                        <AlertTriangle
                          className='mx-auto size-4 text-destructive'
                          aria-label='Load reported an error'
                        />
                      ) : (
                        <span className='text-muted-foreground'>—</span>
                      )}
                    </TableCell>
                  </ExpandableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </MonitoringTableViewport>

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
