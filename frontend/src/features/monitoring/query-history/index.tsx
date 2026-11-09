import { useEffect, useMemo, useState } from 'react'
import { z } from 'zod'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertCircle,
  CheckCircle2,
  Database,
  SearchX,
  User,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { useNoveSurface } from '@/features/assistant/nove-surface-hook'
import { defineNoveCapability } from '@/features/assistant/surface-registry'
import { safeNoveSql } from '@/features/workspaces/nove-feedback'
import { formatDateTime, formatDuration, formatNumber, truncate } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import {
  LoadingLines,
  RefreshBanner,
} from '@/components/ui/loading-overlay'
import { MonitoringHeader } from '../components/monitoring-header'
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

type QueryHistoryItem = {
  log_id: string
  event_time: string
  user_name: string
  object_name?: string
  action?: string
  sql_text: string
  status: string
  error_message: string | null
  duration_ms: number
  rows_affected: number
  query_id: string
  file_id: string | null
  database_name: string
  schema_name: string
  session_id: string
}

type QueryHistoryResponse = {
  items: QueryHistoryItem[]
  total: number
}

/** Seven data columns plus the details toggle. */
const COLUMN_COUNT = 8
const SEARCH_DEBOUNCE_MS = 300

/**
 * `embedded` drops the page header when the view sits inside the Queries
 * tabs, which carry their own title.
 */
export function MonitoringQueryHistory({ embedded = false }: { embedded?: boolean } = {}) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [userFilter, setUserFilter] = useState<string>('')
  const [databaseFilter, setDatabaseFilter] = useState<string>('')
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  // Typing should not fire a request per keystroke; the query key follows
  // the search text once it settles.
  useEffect(() => {
    const timer = setTimeout(
      () => setDebouncedSearch(searchQuery),
      SEARCH_DEBOUNCE_MS
    )
    return () => clearTimeout(timer)
  }, [searchQuery])

  // Fetch history
  const historyQuery = useQuery<QueryHistoryResponse>({
    queryKey: [
      'query-history',
      page,
      pageSize,
      statusFilter,
      userFilter,
      databaseFilter,
      debouncedSearch,
    ],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
      })
      if (statusFilter) params.set('status', statusFilter)
      if (userFilter) params.set('user_name', userFilter)
      if (databaseFilter) params.set('database_name', databaseFilter)
      if (debouncedSearch) params.set('search', debouncedSearch)
      return api.get<QueryHistoryResponse>(
        `/monitoring/queries/history?${params.toString()}`
      )
    },
    placeholderData: keepPreviousData,
  })

  const items = historyQuery.data?.items ?? []
  const total = historyQuery.data?.total ?? 0
  const selectedQuery = items.find((item) => item.log_id === expandedRow)
  const { askNove } = useNoveSurface({
    id: 'monitoring.query_history', route: '/monitoring/queries', title: 'Query history',
    context: () => ({
      entity: selectedQuery ? {
        type: 'query', id: selectedQuery.query_id || selectedQuery.log_id,
        metadata: { status: selectedQuery.status, durationMs: selectedQuery.duration_ms },
      } : undefined,
      selection: selectedQuery ? {
        type: 'query', ids: [selectedQuery.log_id], text: safeNoveSql(selectedQuery.sql_text),
      } : undefined,
      execution: selectedQuery ? {
        type: 'query', executionId: selectedQuery.query_id || selectedQuery.log_id,
        status: selectedQuery.status === 'ERROR' ? 'error' : 'success',
        errorMessage: selectedQuery.error_message
          ? safeNoveSql(selectedQuery.error_message) ?? 'Error details omitted.'
          : null,
        elapsedMs: selectedQuery.duration_ms,
        rowCount: selectedQuery.rows_affected,
      } : undefined,
      view: {
        filters: { status: statusFilter, user: userFilter, database: databaseFilter },
        search: debouncedSearch,
      },
    }),
    capabilities: [
      defineNoveCapability({
        name: 'surface.refresh', risk: 'safe', argsSchema: z.object({}),
        execute: () => historyQuery.refetch({ throwOnError: true }),
      }),
      defineNoveCapability({
        name: 'surface.set_filter', risk: 'safe',
        argsSchema: z.object({ filter: z.enum(['status', 'user', 'database', 'search']), value: z.string().max(160) }),
        execute: ({ filter, value }) => {
          if (filter === 'status') {
            if (!['', 'SUCCESS', 'ERROR', 'FAILED'].includes(value)) throw new Error('Unsupported query status')
            setStatusFilter(value === 'FAILED' ? 'ERROR' : value)
          } else if (filter === 'user') setUserFilter(value)
          else if (filter === 'database') setDatabaseFilter(value)
          else setSearchQuery(value)
          setPage(1)
          setExpandedRow(null)
        },
      }),
      defineNoveCapability({
        name: 'surface.select', risk: 'safe', argsSchema: z.object({ id: z.string().min(1).max(160) }),
        execute: ({ id }) => {
          if (!items.some((item) => item.log_id === id)) throw new Error('Query is not in the current list')
          setExpandedRow(id)
        },
      }),
    ],
    suggestedActions: [
      { label: 'Show failed queries', prompt: 'Show only failed queries here.' },
      { label: 'Investigate a query', prompt: 'Help me investigate a query in this history.' },
    ],
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

  // Handle errors with useEffect to avoid spamming toasts on every render
  useEffect(() => {
    if (historyQuery.error) {
      toast.error('Failed to load query history', {
        description: historyQuery.error.message,
      })
    }
  }, [historyQuery.error])

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

  const hasFilters = Boolean(
    statusFilter || userFilter || databaseFilter || searchQuery
  )

  return (
    <div className='space-y-6'>
      <MonitoringHeader
        embedded={embedded}
        title='Query History'
        description='Browse and search previously executed queries across all workspaces.'
        actions={
          <Button variant='outline' size='sm' onClick={() => void askNove(
            selectedQuery ? 'Explain why this query failed or took so long.' : 'Show only failed queries here.'
          )}>
            Ask Nove
          </Button>
        }
      />

      {/* Filter Bar */}
      <SimpleTableToolbar
        search={searchQuery}
        onSearchChange={(value) => {
          setSearchQuery(value)
          setPage(1)
        }}
        searchPlaceholder='Search SQL...'
        resultLabel={`${total} ${total === 1 ? 'query' : 'queries'}`}
        filters={[
          {
            label: 'Status',
            value: statusFilter,
            options: ['SUCCESS', 'ERROR'],
            onChange: (value) => handleFilterChange(setStatusFilter, value),
            icon: <CheckCircle2 size={14} />,
          },
          {
            label: 'User',
            value: userFilter,
            options: userOptions,
            onChange: (value) => handleFilterChange(setUserFilter, value),
            icon: <User size={14} />,
          },
          {
            label: 'Database',
            value: databaseFilter,
            options: databaseOptions,
            onChange: (value) => handleFilterChange(setDatabaseFilter, value),
            icon: <Database size={14} />,
          },
        ]}
      />

      {/* Data Table */}
      <MonitoringTableViewport>
        {historyQuery.isFetching && !historyQuery.isLoading ? (
          <RefreshBanner label='Loading next results...' />
        ) : null}
        <Table>
          <MonitoringTableHeader>
            <TableHead>Time</TableHead>
            <TableHead>User</TableHead>
            <TableHead>Database</TableHead>
            <TableHead>SQL</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className='text-right'>Duration</TableHead>
            <TableHead className='text-right'>Rows</TableHead>
          </MonitoringTableHeader>
          <TableBody>
            {historyQuery.isLoading ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <LoadingLines rows={6} />
              </TableMessageRow>
            ) : historyQuery.isError ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <EmptyState
                  variant='error'
                  icon={AlertCircle}
                  title='Could not load query history'
                  description='The monitoring API did not respond. Check the connection and reload.'
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
              </TableMessageRow>
            ) : items.length === 0 ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <EmptyState
                  icon={SearchX}
                  title={
                    hasFilters
                      ? 'No queries match these filters'
                      : 'No queries recorded yet'
                  }
                  description={
                    hasFilters
                      ? 'Clear the status, user, or database filter to widen the search.'
                      : 'Query history fills as soon as anyone runs SQL against this workspace.'
                  }
                  action={
                    hasFilters ? (
                      <Button
                        variant='outline'
                        size='sm'
                        onClick={() => {
                          setStatusFilter('')
                          setUserFilter('')
                          setDatabaseFilter('')
                          setSearchQuery('')
                          setDebouncedSearch('')
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
              items.map((item) => (
                <ExpandableRow
                  key={item.log_id}
                  expanded={expandedRow === item.log_id}
                  onToggle={() =>
                    setExpandedRow(
                      expandedRow === item.log_id ? null : item.log_id
                    )
                  }
                  label={`query ${item.query_id || item.log_id}`}
                  colSpan={COLUMN_COUNT}
                  detail={
                    <>
                      <DetailBlock label='Full SQL Query'>
                        {item.sql_text}
                      </DetailBlock>
                      {item.error_message ? (
                        <DetailBlock label='Error Message' tone='error'>
                          {item.error_message}
                        </DetailBlock>
                      ) : null}
                      <DetailFields
                        fields={[
                          { label: 'Query ID', value: item.query_id },
                          { label: 'Schema', value: item.schema_name },
                          { label: 'Session', value: item.session_id },
                          ...(item.file_id
                            ? [{ label: 'File ID', value: item.file_id }]
                            : []),
                        ]}
                      />
                    </>
                  }
                >
                  <TableCell className='px-4 py-3 text-xs text-muted-foreground'>
                    {formatDateTime(item.event_time)}
                  </TableCell>
                  <TableCell className='px-4 py-3'>
                    <div className='flex items-center gap-1.5'>
                      <User className='size-3 text-muted-foreground' />
                      <span className='text-xs'>{item.user_name}</span>
                    </div>
                  </TableCell>
                  <TableCell className='px-4 py-3'>
                    <div className='flex items-center gap-1.5'>
                      <Database className='size-3 text-muted-foreground' />
                      <span className='text-xs'>{item.database_name}</span>
                    </div>
                  </TableCell>
                  <TableCell className='px-4 py-3'>
                    <code className='rounded bg-muted px-1.5 py-0.5 font-mono text-xs'>
                      {truncate(item.sql_text)}
                    </code>
                  </TableCell>
                  <TableCell className='px-4 py-3'>
                    <StatusBadge tone={statusTone(item.status)}>
                      {statusLabel(item.status)}
                    </StatusBadge>
                  </TableCell>
                  <TableCell className='px-4 py-3 text-right text-xs font-medium'>
                    {formatDuration(item.duration_ms)}
                  </TableCell>
                  <TableCell className='px-4 py-3 text-right text-xs text-muted-foreground'>
                    {formatNumber(item.rows_affected ?? 0)}
                  </TableCell>
                </ExpandableRow>
              ))
            )}
          </TableBody>
        </Table>
      </MonitoringTableViewport>

      {/* Pagination */}
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
