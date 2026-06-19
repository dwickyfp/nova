import { Fragment, useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Database,
  User,
  AlertCircle,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { SearchableSelect } from '@/components/ui/searchable-select'

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
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const pageStart = total === 0 ? 0 : (page - 1) * pageSize + 1
  const pageEnd = Math.min(page * pageSize, total)

  const userOptions = useMemo(() => {
    if (!historyQuery.data?.items) return []
    return [...new Set(historyQuery.data.items.map((i) => i.user_name).filter(Boolean))] as string[]
  }, [historyQuery.data])

  const databaseOptions = useMemo(() => {
    if (!historyQuery.data?.items) return []
    return [...new Set(historyQuery.data.items.map((i) => i.database_name).filter(Boolean))] as string[]
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

  const handlePageSizeChange = (newSize: string) => {
    setPageSize(Number(newSize))
    setPage(1)
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

  const getStatusBadgeClassName = (status: string) => {
    const normalizedStatus = status.toUpperCase()

    if (normalizedStatus === 'SUCCESS') {
      return 'border-transparent bg-emerald-600 text-white hover:bg-emerald-600'
    }

    if (
      normalizedStatus === 'FAILED' ||
      normalizedStatus === 'FAILURE' ||
      normalizedStatus === 'ERROR'
    ) {
      return 'border-transparent bg-red-600 text-white hover:bg-red-600'
    }

    if (
      normalizedStatus === 'RUNNING' ||
      normalizedStatus === 'IN_PROGRESS' ||
      normalizedStatus === 'PENDING'
    ) {
      return 'border-transparent bg-amber-500 text-white hover:bg-amber-500'
    }

    return 'border-transparent bg-slate-600 text-white hover:bg-slate-600'
  }

  return (
    <div className='space-y-6'>
      {/* Header */}
      <div>
        <h3 className='text-lg font-medium'>Query History</h3>
        <p className='text-sm text-muted-foreground'>
          Browse and search previously executed queries across all workspaces.
        </p>
      </div>

      {/* Filter Bar */}
      <div className='flex flex-wrap items-center gap-3'>
        <SearchableSelect
          options={['SUCCESS', 'ERROR']}
          value={statusFilter}
          onChange={(value: string) => handleFilterChange(setStatusFilter, value)}
          label='Status'
          icon={<CheckCircle2 size={14} />}
        />

        <SearchableSelect
          options={userOptions}
          value={userFilter}
          onChange={(value: string) => handleFilterChange(setUserFilter, value)}
          label='User'
          icon={<User size={14} />}
        />

        <SearchableSelect
          options={databaseOptions}
          value={databaseFilter}
          onChange={(value: string) => handleFilterChange(setDatabaseFilter, value)}
          label='Database'
          icon={<Database size={14} />}
        />

        <Input
          placeholder='Search SQL...'
          value={searchQuery}
          onChange={(e) => {
            setSearchQuery(e.target.value)
            setPage(1)
          }}
          className='max-w-xs'
        />

        <div className='ml-auto text-sm text-muted-foreground'>
          {total} {total === 1 ? 'query' : 'queries'}
        </div>
      </div>

      {/* Data Table */}
      <div className='relative rounded-md border border-border'>
        {historyQuery.isFetching && !historyQuery.isLoading ? (
          <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
            <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
              <div className='h-1.5 w-full overflow-hidden bg-muted'>
                <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
              </div>
              <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                Loading next results...
              </div>
            </div>
          </div>
        ) : null}
        <div className='overflow-x-auto'>
          <table className='w-full'>
            <thead className='border-b border-border bg-muted/50'>
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
                  <td
                    colSpan={7}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    Loading...
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    No queries found
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
                        <Badge
                          variant='secondary'
                          className={cn(
                            'text-xs font-medium',
                            getStatusBadgeClassName(item.status)
                          )}
                        >
                          {item.status}
                        </Badge>
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
        </div>
      </div>

      {/* Pagination */}
      <div className='flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between'>
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
              <SelectItem value='10'>10</SelectItem>
              <SelectItem value='25'>25</SelectItem>
              <SelectItem value='50'>50</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className='flex flex-col gap-2 sm:flex-row sm:items-center'>
          <span className='text-sm text-muted-foreground'>
            Showing {pageStart}-{pageEnd} of {total}
          </span>
          <span className='text-sm text-muted-foreground'>
            Page {page} of {totalPages}
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
