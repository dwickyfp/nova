import { Fragment, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertCircle,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Database,
  User,
  XCircle,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { SearchableSelect } from '@/components/ui/searchable-select'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

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

function getCommandBadgeClassName(command: string) {
  const normalizedCommand = command.toUpperCase()

  if (normalizedCommand === 'QUERY') {
    return 'border-transparent bg-sky-600 text-white hover:bg-sky-600'
  }

  if (normalizedCommand === 'SLEEP') {
    return 'border-transparent bg-slate-500 text-white hover:bg-slate-500'
  }

  return 'border-transparent bg-violet-600 text-white hover:bg-violet-600'
}

function getStateBadgeClassName(state: string) {
  const normalizedState = state.toUpperCase()

  if (
    normalizedState.includes('RUN') ||
    normalizedState.includes('EXEC') ||
    normalizedState.includes('SEND')
  ) {
    return 'border-transparent bg-amber-500 text-white hover:bg-amber-500'
  }

  if (
    normalizedState.includes('FINISH') ||
    normalizedState.includes('DONE') ||
    normalizedState.includes('OK')
  ) {
    return 'border-transparent bg-emerald-600 text-white hover:bg-emerald-600'
  }

  if (
    normalizedState.includes('ERROR') ||
    normalizedState.includes('FAIL') ||
    normalizedState.includes('KILL')
  ) {
    return 'border-transparent bg-red-600 text-white hover:bg-red-600'
  }

  return 'border-transparent bg-slate-600 text-white hover:bg-slate-600'
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
    return [...new Set(queries.map((query) => query.user).filter(Boolean))] as string[]
  }, [queries])

  const databaseOptions = useMemo(() => {
    return [...new Set(queries.map((query) => query.db).filter(Boolean))] as string[]
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
  const pageStart = total === 0 ? 0 : (safePage - 1) * pageSize + 1
  const pageEnd = Math.min(safePage * pageSize, total)
  const pagedQueries = filteredQueries.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize
  )

  const handlePageChange = (newPage: number) => {
    setPage(Math.max(1, Math.min(newPage, totalPages)))
    setExpandedRow(null)
  }

  const handlePageSizeChange = (value: string) => {
    setPageSize(Number(value))
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

  return (
    <div className='space-y-6'>
      <div>
        <h3 className='text-lg font-medium'>Active Queries</h3>
        <p className='text-sm text-muted-foreground'>
          View currently running queries and inspect their execution details.
        </p>
      </div>

      <div className='flex flex-wrap items-center gap-3'>
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
          onChange={(value: string) =>
            handleFilterChange(setDatabaseFilter, value)
          }
          label='Database'
          icon={<Database size={14} />}
        />

        <Input
          placeholder='Search query, host, state...'
          value={searchQuery}
          onChange={(event) => {
            setSearchQuery(event.target.value)
            setPage(1)
            setExpandedRow(null)
          }}
          className='max-w-xs'
        />

        <div className='ml-auto text-sm text-muted-foreground'>
          {total} {total === 1 ? 'active query' : 'active queries'}
        </div>
      </div>

      <div className='relative rounded-md border border-border'>
        {isFetching && !isLoading ? (
          <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
            <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
              <div className='h-1.5 w-full overflow-hidden bg-muted'>
                <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
              </div>
              <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                Refreshing active queries...
              </div>
            </div>
          </div>
        ) : null}

        <div className='overflow-x-auto'>
          <table className='w-full'>
            <thead className='border-b border-border bg-muted/50'>
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
                  <td
                    colSpan={7}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    Loading...
                  </td>
                </tr>
              ) : pagedQueries.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    No active queries found
                  </td>
                </tr>
              ) : (
                pagedQueries.map((query) => (
                  <Fragment key={query.id}>
                    <tr
                      onClick={() =>
                        setExpandedRow(
                          expandedRow === query.id ? null : query.id
                        )
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
                        <Badge
                          variant='secondary'
                          className={cn(
                            'text-xs font-medium',
                            getCommandBadgeClassName(query.command)
                          )}
                        >
                          {query.command}
                        </Badge>
                      </td>
                      <td className='px-4 py-3 text-sm'>
                        <code className='rounded bg-muted px-1.5 py-0.5 text-xs font-mono'>
                          {truncateText(query.info ?? '—')}
                        </code>
                      </td>
                      <td className='px-4 py-3'>
                        <Badge
                          variant='secondary'
                          className={cn(
                            'text-xs font-medium',
                            getStateBadgeClassName(query.state)
                          )}
                        >
                          {query.state}
                        </Badge>
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
                                <span className='font-medium'>Connection ID:</span>{' '}
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
        </div>
      </div>

      <div className='flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between'>
        <div className='flex items-center gap-2 text-sm'>
          <span className='text-muted-foreground'>Rows per page:</span>
          <Select value={String(pageSize)} onValueChange={handlePageSizeChange}>
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
            Page {safePage} of {totalPages}
          </span>
          <div className='flex items-center gap-1'>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(1)}
              disabled={safePage === 1}
              className='h-8 w-8'
            >
              <ChevronsLeft className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(safePage - 1)}
              disabled={safePage === 1}
              className='h-8 w-8'
            >
              <ChevronLeft className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(safePage + 1)}
              disabled={safePage >= totalPages}
              className='h-8 w-8'
            >
              <ChevronRight className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(totalPages)}
              disabled={safePage >= totalPages}
              className='h-8 w-8'
            >
              <ChevronsRight className='h-4 w-4' />
            </Button>
          </div>
        </div>
      </div>

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
              onClick={() => (killTarget ? killMutation.mutate(killTarget) : null)}
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
