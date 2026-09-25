import { Fragment, useEffect, useMemo, useState } from 'react'
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
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import {
  LoadingLines,
  RefreshBanner,
} from '@/components/ui/loading-overlay'
import { PageHeader } from '@/components/ui/page-header'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
import { statusTone } from '../components/status-tone'

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

export function MonitoringQueryHistory() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [userFilter, setUserFilter] = useState<string>('')
  const [databaseFilter, setDatabaseFilter] = useState<string>('')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  // Fetch history
  const historyQuery = useQuery<QueryHistoryResponse>({
    queryKey: [
      'query-history',
      page,
      pageSize,
      statusFilter,
      userFilter,
      databaseFilter,
      searchQuery,
    ],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
      })
      if (statusFilter) params.set('status', statusFilter)
      if (userFilter) params.set('user_name', userFilter)
      if (databaseFilter) params.set('database_name', databaseFilter)
      if (searchQuery) params.set('search', searchQuery)
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
    id: 'monitoring.query_history', route: '/query-history', title: 'Query history',
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
        search: searchQuery,
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

  const formatTime = (isoString: string) => {
    const date = new Date(isoString)
    return date.toLocaleString('en-US', {
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

  const truncateSql = (sql: string, maxLength: number = 80) => {
    if (sql.length <= maxLength) return sql
    return sql.slice(0, maxLength).trim() + '...'
  }

  const hasFilters = Boolean(
    statusFilter || userFilter || databaseFilter || searchQuery
  )

  return (
    <div className='space-y-6'>
      <PageHeader
        title='Query History'
        description='Browse and search previously executed queries across all workspaces.'
      />
      <Button variant='outline' size='sm' onClick={() => void askNove(
        selectedQuery ? 'Explain why this query failed or took so long.' : 'Show only failed queries here.'
      )}>
        Ask Nove
      </Button>

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
      <SimpleTableViewport>
        {historyQuery.isFetching && !historyQuery.isLoading ? (
          <RefreshBanner label='Loading next results...' />
        ) : null}
        <table className='w-full'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Time
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
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Status
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Duration
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Rows
              </th>
            </tr>
          </thead>
          <tbody>
            {historyQuery.isLoading ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
                  <LoadingLines rows={6} />
                </td>
              </tr>
            ) : historyQuery.isError ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
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
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
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
              items.map((item) => (
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
                    <td className='px-4 py-3 text-xs text-muted-foreground whitespace-nowrap'>
                      {formatTime(item.event_time)}
                    </td>
                    <td className='px-4 py-3 text-sm'>
                      <div className='flex items-center gap-1.5'>
                        <User className='h-3 w-3 text-muted-foreground' />
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
                      <code className='rounded bg-muted px-1.5 py-0.5 text-xs font-mono'>
                        {truncateSql(item.sql_text)}
                      </code>
                    </td>
                    <td className='px-4 py-3'>
                      <StatusBadge tone={statusTone(item.status)}>
                        {item.status}
                      </StatusBadge>
                    </td>
                    <td className='px-4 py-3 text-right text-xs font-medium'>
                      {formatDuration(item.duration_ms)}
                    </td>
                    <td className='px-4 py-3 text-right text-xs text-muted-foreground'>
                      {(item.rows_affected ?? 0).toLocaleString()}
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
                            <pre className='max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs font-mono'>
                              {item.sql_text}
                            </pre>
                          </div>
                          {item.error_message && (
                            <div>
                              <p className='mb-1.5 flex items-center gap-1.5 text-xs font-medium text-destructive'>
                                <AlertCircle className='h-3 w-3' />
                                Error Message
                              </p>
                              <pre className='rounded-md bg-destructive/10 p-3 text-xs font-mono text-destructive'>
                                {item.error_message}
                              </pre>
                            </div>
                          )}
                          <div className='flex flex-wrap gap-4 text-xs text-muted-foreground'>
                            <div>
                              <span className='font-medium'>Query ID:</span>{' '}
                              {item.query_id}
                            </div>
                            <div>
                              <span className='font-medium'>Schema:</span>{' '}
                              {item.schema_name}
                            </div>
                            <div>
                              <span className='font-medium'>Session:</span>{' '}
                              {item.session_id}
                            </div>
                            {item.file_id && (
                              <div>
                                <span className='font-medium'>File ID:</span>{' '}
                                {item.file_id}
                              </div>
                            )}
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
