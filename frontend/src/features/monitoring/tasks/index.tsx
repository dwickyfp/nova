import { Fragment, useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, CheckCircle2, Clock, ListTodo } from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'

type TaskRun = {
  query_id: string
  task_name: string
  create_time: string
  finish_time: string | null
  state: string
  error_code: number | null
  error_message: string | null
  progress: number | null
}

type TaskRunsResponse = {
  items: TaskRun[]
  total: number
}

function formatTime(isoString: string | null) {
  if (!isoString) return '—'
  const date = new Date(isoString)
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatDuration(createTime: string, finishTime: string | null) {
  if (!finishTime) return '—'
  const durationMs =
    new Date(finishTime).getTime() - new Date(createTime).getTime()
  if (durationMs < 0) return '—'
  if (durationMs < 1000) return `${durationMs}ms`

  const seconds = durationMs / 1000
  if (seconds < 60) return `${seconds.toFixed(2)}s`

  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.round(seconds % 60)
  if (minutes < 60) {
    return remainingSeconds > 0
      ? `${minutes}m ${remainingSeconds}s`
      : `${minutes}m`
  }

  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`
}

function truncateText(value: string | undefined | null, maxLength: number = 80) {
  if (!value) return '—'
  if (value.length <= maxLength) return value
  return `${value.slice(0, maxLength).trim()}...`
}

function getStateBadgeClassName(state: string) {
  const normalizedState = state.toUpperCase()

  if (normalizedState === 'SUCCESS') {
    return 'border-transparent bg-emerald-600 text-white hover:bg-emerald-600'
  }

  if (normalizedState === 'FAILED' || normalizedState === 'ERROR') {
    return 'border-transparent bg-red-600 text-white hover:bg-red-600'
  }

  if (normalizedState === 'RUNNING' || normalizedState === 'PENDING') {
    return 'border-transparent bg-amber-500 text-white hover:bg-amber-500'
  }

  return 'border-transparent bg-slate-600 text-white hover:bg-slate-600'
}

function formatProgress(progress: number | null) {
  if (progress == null) return '—'
  if (progress <= 1) return `${Math.round(progress * 100)}%`
  return `${Math.round(progress)}%`
}

export function MonitoringTasks() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [stateFilter, setStateFilter] = useState<string>('')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  const runsQuery = useQuery<TaskRunsResponse>({
    queryKey: ['monitoring-task-runs', page, pageSize, stateFilter],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
      })
      if (stateFilter) params.set('state', stateFilter)
      return api.get<TaskRunsResponse>(
        `/monitoring/tasks/runs?${params.toString()}`
      )
    },
    placeholderData: keepPreviousData,
  })

  useEffect(() => {
    if (runsQuery.error) {
      toast.error('Failed to load task run history', {
        description: runsQuery.error.message,
      })
    }
  }, [runsQuery.error])

  const queryRuns = runsQuery.data?.items ?? []
  const filteredRuns = useMemo(() => {
    const normalizedSearchQuery = searchQuery.trim().toLowerCase()
    if (!normalizedSearchQuery) return queryRuns

    return queryRuns.filter((run) =>
      [run.query_id ?? '', run.task_name ?? '', run.state ?? '', run.error_message ?? ''].some(
        (value) => value.toLowerCase().includes(normalizedSearchQuery)
      )
    )
  }, [queryRuns, searchQuery])

  const total = runsQuery.data?.total ?? 0

  const handlePageChange = (newPage: number) => {
    setPage(newPage)
    setExpandedRow(null)
  }

  const handleStateFilterChange = (value: string) => {
    setStateFilter(value)
    setPage(1)
    setExpandedRow(null)
  }

  return (
    <div className='space-y-6'>
      <div>
        <h3 className='text-lg font-medium'>Tasks</h3>
        <p className='text-sm text-muted-foreground'>
          Monitor scheduled and background task runs across the system.
        </p>
      </div>

      <SimpleTableToolbar
        search={searchQuery}
        onSearchChange={(value) => {
          setSearchQuery(value)
          setExpandedRow(null)
        }}
        searchPlaceholder='Search task, query id, error...'
        resultLabel={`${total} ${total === 1 ? 'run' : 'runs'}`}
        filters={[
          {
            label: 'State',
            value: stateFilter,
            options: ['SUCCESS', 'FAILED', 'RUNNING', 'PENDING'],
            onChange: handleStateFilterChange,
            icon: <CheckCircle2 size={14} />,
          },
        ]}
      />

      <SimpleTableViewport>
        {runsQuery.isFetching && !runsQuery.isLoading ? (
          <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
            <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
              <div className='h-1.5 w-full overflow-hidden bg-muted'>
                <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
              </div>
              <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                Loading task runs...
              </div>
            </div>
          </div>
        ) : null}

        <table className='w-full'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Task Name
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Query ID
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Created
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Finished
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                State
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Duration
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Progress
              </th>
            </tr>
          </thead>
          <tbody>
            {runsQuery.isLoading ? (
              <tr>
                <td
                  colSpan={7}
                  className='px-4 py-12 text-center text-sm text-muted-foreground'
                >
                  Loading...
                </td>
              </tr>
            ) : filteredRuns.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className='px-4 py-12 text-center text-sm text-muted-foreground'
                >
                  No task runs found
                </td>
              </tr>
            ) : (
              filteredRuns.map((run) => (
                <Fragment key={run.query_id}>
                  <tr
                    onClick={() =>
                      setExpandedRow(
                        expandedRow === run.query_id ? null : run.query_id
                      )
                    }
                    className={cn(
                      'cursor-pointer border-b border-border transition-colors hover:bg-muted/50',
                      expandedRow === run.query_id && 'bg-muted/30'
                    )}
                  >
                    <td className='px-4 py-3 text-sm'>
                      <div className='flex items-center gap-1.5'>
                        <ListTodo className='h-3 w-3 text-muted-foreground' />
                        <span className='text-xs font-medium'>
                          {run.task_name}
                        </span>
                      </div>
                    </td>
                    <td className='px-4 py-3 text-sm'>
                      <code className='rounded bg-muted px-1.5 py-0.5 text-xs font-mono'>
                        {truncateText(run.query_id, 18)}
                      </code>
                    </td>
                    <td className='px-4 py-3 text-xs text-muted-foreground whitespace-nowrap'>
                      {formatTime(run.create_time)}
                    </td>
                    <td className='px-4 py-3 text-xs text-muted-foreground whitespace-nowrap'>
                      {formatTime(run.finish_time)}
                    </td>
                    <td className='px-4 py-3'>
                      <Badge
                        variant='secondary'
                        className={cn(
                          'text-xs font-medium',
                          getStateBadgeClassName(run.state)
                        )}
                      >
                        {run.state}
                      </Badge>
                    </td>
                    <td className='px-4 py-3 text-right text-xs font-medium'>
                      {formatDuration(run.create_time, run.finish_time)}
                    </td>
                    <td className='px-4 py-3 text-right text-xs text-muted-foreground'>
                      {formatProgress(run.progress)}
                    </td>
                  </tr>
                  {expandedRow === run.query_id ? (
                    <tr className='border-b border-border bg-muted/20'>
                      <td colSpan={7} className='px-4 py-4'>
                        <div className='space-y-3'>
                          <div className='flex flex-wrap gap-4 text-xs text-muted-foreground'>
                            <div>
                              <span className='font-medium'>Task Name:</span>{' '}
                              {run.task_name}
                            </div>
                            <div>
                              <span className='font-medium'>Query ID:</span>{' '}
                              {run.query_id}
                            </div>
                            <div>
                              <span className='font-medium'>Error Code:</span>{' '}
                              {run.error_code ?? '—'}
                            </div>
                            <div>
                              <span className='font-medium'>Progress:</span>{' '}
                              {formatProgress(run.progress)}
                            </div>
                          </div>

                          {run.error_message ? (
                            <div>
                              <p className='mb-1.5 flex items-center gap-1.5 text-xs font-medium text-destructive'>
                                <AlertCircle className='h-3 w-3' />
                                Error Message
                              </p>
                              <pre className='rounded-md bg-destructive/10 p-3 text-xs font-mono text-destructive'>
                                {run.error_message}
                              </pre>
                            </div>
                          ) : (
                            <div className='flex items-center gap-1.5 text-xs text-muted-foreground'>
                              <Clock className='h-3 w-3' />
                              No error message for this run
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))
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
