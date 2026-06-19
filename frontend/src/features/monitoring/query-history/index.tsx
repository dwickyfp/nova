import { Fragment, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Clock,
  CheckCircle2,
  XCircle,
  Activity,
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
import { Card, CardContent } from '@/components/ui/card'

type QueryHistoryItem = {
  log_id: string
  event_time: string
  user_name: string
  object_name: string
  action: string
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

type QueryStatsResponse = {
  total: number
  avg_duration_ms: number
  error_count: number
  success_count: number
  error_rate: number
}

export function MonitoringQueryHistory() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [userFilter, setUserFilter] = useState<string>('')
  const [databaseFilter, setDatabaseFilter] = useState<string>('')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  // Fetch stats
  const statsQuery = useQuery<QueryStatsResponse>({
    queryKey: ['monitoring-query-stats'],
    queryFn: () => api.get<QueryStatsResponse>('/monitoring/queries/stats'),
  })

  // Fetch history
  const historyQuery = useQuery<QueryHistoryResponse>({
    queryKey: [
      'monitoring-query-history',
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
  })

  const items = historyQuery.data?.items ?? []
  const total = historyQuery.data?.total ?? 0
  const totalPages = Math.ceil(total / pageSize)

  const stats = statsQuery.data

  // Handle errors with useEffect to avoid spamming toasts on every render
  useEffect(() => {
    if (historyQuery.error) {
      toast.error('Failed to load query history', {
        description: historyQuery.error.message,
      })
    }
  }, [historyQuery.error])

  useEffect(() => {
    if (statsQuery.error) {
      toast.error('Failed to load query statistics', {
        description: statsQuery.error.message,
      })
    }
  }, [statsQuery.error])

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
    setter(value === 'all' ? '' : value)
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

  return (
    <div className='space-y-6'>
      {/* Header */}
      <div>
        <h3 className='text-lg font-medium'>Query History</h3>
        <p className='text-sm text-muted-foreground'>
          Browse and search previously executed queries across all workspaces.
        </p>
      </div>

      {/* Stats Cards */}
      <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
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
                  {stats?.total ?? '—'}
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
                  Avg Duration
                </p>
                <p className='text-2xl font-bold'>
                  {stats ? formatDuration(stats.avg_duration_ms) : '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-muted p-2'>
                <CheckCircle2 className='h-4 w-4 text-muted-foreground' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Success Rate
                </p>
                <p className='text-2xl font-bold'>
                  {stats
                    ? `${((stats.success_count / (stats.total || 1)) * 100).toFixed(1)}%`
                    : '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className='rounded-md border border-border'>
          <CardContent className='pt-6'>
            <div className='flex items-center gap-3'>
              <div className='rounded-md bg-muted p-2'>
                <XCircle className='h-4 w-4 text-muted-foreground' />
              </div>
              <div>
                <p className='text-xs font-medium text-muted-foreground'>
                  Error Count
                </p>
                <p className='text-2xl font-bold'>
                  {stats?.error_count ?? '—'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filter Bar */}
      <div className='flex flex-wrap items-center gap-3'>
        <Select
          value={statusFilter || 'all'}
          onValueChange={(value: string) => handleFilterChange(setStatusFilter, value)}
        >
          <SelectTrigger className='w-[140px]'>
            <SelectValue placeholder='Status' />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>All Status</SelectItem>
            <SelectItem value='SUCCESS'>Success</SelectItem>
            <SelectItem value='ERROR'>Error</SelectItem>
          </SelectContent>
        </Select>

        <Input
          placeholder='User...'
          value={userFilter}
          onChange={(e) => {
            setUserFilter(e.target.value)
            setPage(1)
          }}
          className='w-[150px]'
        />

        <Input
          placeholder='Database...'
          value={databaseFilter}
          onChange={(e) => {
            setDatabaseFilter(e.target.value)
            setPage(1)
          }}
          className='w-[150px]'
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
      <div className='rounded-md border border-border'>
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
                          variant={
                            item.status === 'SUCCESS' ? 'default' : 'destructive'
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
