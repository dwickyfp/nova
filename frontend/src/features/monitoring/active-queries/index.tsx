import { Fragment, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Database,
  User,
  XCircle,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

type ActiveQuery = {
  id: number
  user: string
  host: string
  db: string | null
  command: string
  time: number
  state: string
  info: string | null
  query_id: string | null
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return remainingSeconds > 0
      ? `${minutes}m ${remainingSeconds}s`
      : `${minutes}m`
  }

  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`
}

function truncateText(value: string, maxLength: number = 80) {
  if (value.length <= maxLength) return value
  return `${value.slice(0, maxLength).trim()}...`
}

/** Command is a classification, not a health state, so it stays informational. */
function commandTone(command: string) {
  const normalized = command.toUpperCase()
  if (normalized === 'QUERY') return 'info' as const
  if (normalized === 'SLEEP') return 'neutral' as const
  return 'primary' as const
}

export function MonitoringActiveQueries() {
  const queryClient = useQueryClient()
  const [userFilter, setUserFilter] = useState('')
  const [databaseFilter, setDatabaseFilter] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [killTarget, setKillTarget] = useState<ActiveQuery | null>(null)
  const [expandedRow, setExpandedRow] = useState<number | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const { data, isLoading, isFetching } = useQuery<ActiveQuery[]>({
    queryKey: ['monitoring', 'active-queries'],
    queryFn: () => api.get<ActiveQuery[]>('/monitoring/queries/active'),
    refetchInterval: 5000,
  })

  const killMutation = useMutation({
    mutationFn: (query: ActiveQuery) =>
      api.post('/monitoring/queries/kill', { connection_id: query.id }),
    onSuccess: () => {
      toast.success('Query killed successfully')
      void queryClient.invalidateQueries({
        queryKey: ['monitoring', 'active-queries'],
      })
      setKillTarget(null)
    },
    onError: (error: Error) => {
      toast.error(`Failed to kill query: ${error.message}`)
      setKillTarget(null)
    },
  })

  const queries = data ?? []

  const userOptions = useMemo(() => {
    return [
      ...new Set(queries.map((query) => query.user).filter(Boolean)),
    ] as string[]
  }, [queries])

  const databaseOptions = useMemo(() => {
    return [
      ...new Set(queries.map((query) => query.db).filter(Boolean)),
    ] as string[]
  }, [queries])

  const filteredQueries = useMemo(() => {
    const normalizedSearchQuery = searchQuery.trim().toLowerCase()

    return queries.filter((query) => {
      if (userFilter && query.user !== userFilter) return false
      if (databaseFilter && query.db !== databaseFilter) return false
      if (!normalizedSearchQuery) return true

      return [
        query.info ?? '',
        query.state,
        query.command,
        query.host,
        query.query_id ?? '',
      ].some((value) => value.toLowerCase().includes(normalizedSearchQuery))
    })
  }, [databaseFilter, queries, searchQuery, userFilter])

  const total = filteredQueries.length
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(page, totalPages)
  const pagedQueries = filteredQueries.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize
  )

  const handlePageChange = (newPage: number) => {
    setPage(Math.max(1, Math.min(newPage, totalPages)))
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
        title='Active Queries'
        description='View currently running queries and inspect their execution details.'
      />

      <SimpleTableToolbar
        search={searchQuery}
        onSearchChange={(value) => {
          setSearchQuery(value)
          setPage(1)
          setExpandedRow(null)
        }}
        searchPlaceholder='Search query, host, state...'
        resultLabel={`${total} ${total === 1 ? 'active query' : 'active queries'}`}
        filters={[
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

      <SimpleTableViewport>
        {isFetching && !isLoading ? (
          <RefreshBanner label='Refreshing active queries...' />
        ) : null}

        <table className='w-full'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                User
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Database
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Command
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Query
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                State
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Time
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
                  <LoadingLines rows={5} />
                </td>
              </tr>
            ) : pagedQueries.length === 0 ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
                  <EmptyState
                    icon={Activity}
                    title={
                      queries.length > 0
                        ? 'No active queries match these filters'
                        : 'Nothing is running right now'
                    }
                    description={
                      queries.length > 0
                        ? 'Clear the user or database filter to see the full list.'
                        : 'This view shows queries in flight. It refreshes every 5 seconds.'
                    }
                  />
                </td>
              </tr>
            ) : (
              pagedQueries.map((query) => (
                <Fragment key={query.id}>
                  <tr
                    onClick={() =>
                      setExpandedRow(expandedRow === query.id ? null : query.id)
                    }
                    className={cn(
                      'cursor-pointer border-b border-border transition-colors hover:bg-muted/50',
                      expandedRow === query.id && 'bg-muted/30'
                    )}
                  >
                    <td className='px-4 py-3 text-sm'>
                      <div className='flex items-center gap-1.5'>
                        <User className='h-3 w-3 text-muted-foreground' />
                        <span className='text-xs'>{query.user}</span>
                      </div>
                    </td>
                    <td className='px-4 py-3 text-sm'>
                      <div className='flex items-center gap-1.5'>
                        <Database className='h-3 w-3 text-muted-foreground' />
                        <span className='text-xs'>{query.db ?? '—'}</span>
                      </div>
                    </td>
                    <td className='px-4 py-3'>
                      <StatusBadge tone={commandTone(query.command)}>
                        {query.command}
                      </StatusBadge>
                    </td>
                    <td className='px-4 py-3 text-sm'>
                      <code className='rounded bg-muted px-1.5 py-0.5 text-xs font-mono'>
                        {truncateText(query.info ?? '—')}
                      </code>
                    </td>
                    <td className='px-4 py-3'>
                      <StatusBadge tone={statusTone(query.state)} dot>
                        {query.state}
                      </StatusBadge>
                    </td>
                    <td className='px-4 py-3 text-right text-xs font-medium'>
                      {formatDuration(query.time ?? 0)}
                    </td>
                    <td className='px-4 py-3 text-right'>
                      <Button
                        variant='ghost'
                        size='sm'
                        onClick={(event) => {
                          event.stopPropagation()
                          setKillTarget(query)
                        }}
                        className='text-destructive hover:text-destructive'
                      >
                        <XCircle className='h-4 w-4' />
                        Kill
                      </Button>
                    </td>
                  </tr>
                  {expandedRow === query.id && (
                    <tr className='border-b border-border bg-muted/20'>
                      <td colSpan={7} className='px-4 py-4'>
                        <div className='space-y-3'>
                          <div>
                            <p className='mb-1.5 text-xs font-medium text-muted-foreground'>
                              Full Query
                            </p>
                            <pre className='max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs font-mono'>
                              {query.info ?? '—'}
                            </pre>
                          </div>
                          <div className='flex flex-wrap gap-4 text-xs text-muted-foreground'>
                            <div>
                              <span className='font-medium'>
                                Connection ID:
                              </span>{' '}
                              {query.id}
                            </div>
                            <div>
                              <span className='font-medium'>Host:</span>{' '}
                              {query.host}
                            </div>
                            <div>
                              <span className='font-medium'>Query ID:</span>{' '}
                              {query.query_id ?? '—'}
                            </div>
                            <div>
                              <span className='font-medium'>Command:</span>{' '}
                              {query.command}
                            </div>
                          </div>
                          {query.state ? (
                            <div>
                              <p className='mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground'>
                                <AlertCircle className='h-3 w-3' />
                                Current State
                              </p>
                              <pre className='rounded-md bg-muted p-3 text-xs font-mono'>
                                {query.state}
                              </pre>
                            </div>
                          ) : null}
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

      <SimpleTablePagination
        page={safePage}
        pageSize={pageSize}
        total={total}
        onPageChange={handlePageChange}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
          setExpandedRow(null)
        }}
      />

      <Dialog open={!!killTarget} onOpenChange={() => setKillTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2'>
              <AlertTriangle className='h-5 w-5 text-destructive' />
              Kill Query
            </DialogTitle>
            <DialogDescription>
              Are you sure you want to kill this query? This action cannot be
              undone.
            </DialogDescription>
          </DialogHeader>
          {killTarget ? (
            <div className='rounded-md bg-muted p-3 text-sm'>
              <p>
                <span className='font-medium'>Connection ID:</span>{' '}
                {killTarget.id}
              </p>
              <p>
                <span className='font-medium'>User:</span> {killTarget.user}
              </p>
              <p>
                <span className='font-medium'>Query:</span>{' '}
                <code className='text-xs'>
                  {truncateText(killTarget.info ?? 'N/A', 100)}
                </code>
              </p>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant='outline' onClick={() => setKillTarget(null)}>
              Cancel
            </Button>
            <Button
              variant='destructive'
              onClick={() =>
                killTarget ? killMutation.mutate(killTarget) : null
              }
              disabled={killMutation.isPending}
            >
              {killMutation.isPending ? 'Killing...' : 'Kill Query'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
