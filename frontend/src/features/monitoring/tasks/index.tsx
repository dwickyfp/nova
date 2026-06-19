import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ListTodo,
  Play,
  CheckCircle,
  XCircle,
  Clock,
  AlertTriangle,
  Code,
  Info,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'

// ── Types ──────────────────────────────────────────────────────────────────────

type DefinedTask = {
  task_name: string
  create_time: string
  schedule: string
  database: string
  definition: string
  properties: Record<string, unknown> | null
}

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

// ── Helpers ────────────────────────────────────────────────────────────────────

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
  const ms = new Date(finishTime).getTime() - new Date(createTime).getTime()
  if (ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(2)}s`
  const minutes = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  if (minutes < 60) return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
}

function stateBadgeClasses(state: string): string {
  const s = state.toUpperCase()
  if (s === 'SUCCESS')
    return 'bg-chart-2/10 text-chart-2 border-chart-2/20'
  if (s === 'FAILED')
    return 'bg-destructive/10 text-destructive border-destructive/20'
  if (s === 'RUNNING')
    return 'bg-warning/10 text-warning border-warning/20'
  return 'bg-muted text-muted-foreground border-border'
}

function stateIcon(state: string) {
  const s = state.toUpperCase()
  if (s === 'SUCCESS') return <CheckCircle className='mr-1 h-3 w-3' />
  if (s === 'FAILED') return <XCircle className='mr-1 h-3 w-3' />
  if (s === 'RUNNING') return <Clock className='mr-1 h-3 w-3' />
  return null
}

// ── Component ──────────────────────────────────────────────────────────────────

export function MonitoringTasks() {
  // Run history state
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [stateFilter, setStateFilter] = useState<string>('')

  // Fetch defined tasks
  const tasksQuery = useQuery<DefinedTask[]>({
    queryKey: ['monitoring-tasks-defined'],
    queryFn: () => api.get<DefinedTask[]>('/monitoring/tasks'),
  })

  // Fetch task run history
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
  })

  const tasks = tasksQuery.data ?? []
  const runs = runsQuery.data?.items ?? []
  const total = runsQuery.data?.total ?? 0
  const totalPages = Math.ceil(total / pageSize)

  // Toast errors
  useEffect(() => {
    if (tasksQuery.error) {
      toast.error('Failed to load defined tasks', {
        description: tasksQuery.error.message,
      })
    }
  }, [tasksQuery.error])

  useEffect(() => {
    if (runsQuery.error) {
      toast.error('Failed to load task run history', {
        description: runsQuery.error.message,
      })
    }
  }, [runsQuery.error])

  const handlePageChange = (newPage: number) => {
    setPage(newPage)
  }

  const handlePageSizeChange = (newSize: string) => {
    setPageSize(Number(newSize))
    setPage(1)
  }

  const handleStateFilterChange = (value: string) => {
    setStateFilter(value === 'all' ? '' : value)
    setPage(1)
  }

  return (
    <div className='space-y-8'>
      {/* Header */}
      <div>
        <h3 className='text-lg font-medium'>Tasks</h3>
        <p className='text-sm text-muted-foreground'>
          Monitor scheduled and background tasks across the system.
        </p>
      </div>

      {/* ─── Section 1: Defined Tasks ─────────────────────────────────── */}
      <section className='space-y-4'>
        <div className='flex items-center gap-2'>
          <ListTodo className='h-4 w-4 text-muted-foreground' />
          <h4 className='text-sm font-semibold'>Defined Tasks</h4>
          <span className='text-xs text-muted-foreground'>
            ({tasks.length})
          </span>
        </div>

        {tasksQuery.isLoading ? (
          <div className='rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground'>
            Loading tasks…
          </div>
        ) : tasks.length === 0 ? (
          <Card className='rounded-md border border-dashed'>
            <CardContent className='pt-6'>
              <div className='flex flex-col items-center gap-4 py-6 text-center'>
                <div className='rounded-full bg-muted p-3'>
                  <Info className='h-5 w-5 text-muted-foreground' />
                </div>
                <div className='space-y-2'>
                  <p className='text-sm font-medium'>No tasks defined</p>
                  <p className='max-w-md text-xs text-muted-foreground'>
                    Tasks are scheduled background jobs that run SQL statements
                    on a recurring basis. Create one using SQL:
                  </p>
                </div>
                <div className='w-full max-w-lg rounded-md bg-muted p-4'>
                  <div className='mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground'>
                    <Code className='h-3 w-3' />
                    Example
                  </div>
                  <pre className='overflow-x-auto text-left text-xs font-mono leading-relaxed text-foreground'>
{`CREATE TASK my_task
  AS SELECT * FROM my_table WHERE updated > now() - INTERVAL 1 HOUR
  SCHEDULE '0 */6 * * *'`}
                  </pre>
                </div>
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className='rounded-md border border-border'>
            <div className='overflow-x-auto'>
              <table className='w-full'>
                <thead className='border-b border-border bg-muted/50'>
                  <tr>
                    <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                      Task Name
                    </th>
                    <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                      Schedule
                    </th>
                    <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                      Database
                    </th>
                    <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                      Created
                    </th>
                    <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                      Definition
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((task) => (
                    <tr
                      key={task.task_name}
                      className='border-b border-border transition-colors last:border-b-0 hover:bg-muted/50'
                    >
                      <td className='px-4 py-3 text-sm font-medium'>
                        <div className='flex items-center gap-2'>
                          <Play className='h-3 w-3 text-muted-foreground' />
                          {task.task_name}
                        </div>
                      </td>
                      <td className='px-4 py-3'>
                        <code className='rounded bg-muted px-1.5 py-0.5 text-xs font-mono'>
                          {task.schedule || '—'}
                        </code>
                      </td>
                      <td className='px-4 py-3 text-xs text-muted-foreground'>
                        {task.database || '—'}
                      </td>
                      <td className='px-4 py-3 text-xs text-muted-foreground whitespace-nowrap'>
                        {formatTime(task.create_time)}
                      </td>
                      <td className='px-4 py-3 text-sm'>
                        <code className='rounded bg-muted px-1.5 py-0.5 text-xs font-mono'>
                          {task.definition
                            ? task.definition.length > 80
                              ? task.definition.slice(0, 80).trim() + '…'
                              : task.definition
                            : '—'}
                        </code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {/* ─── Section 2: Task Run History ──────────────────────────────── */}
      <section className='space-y-4'>
        <div className='flex items-center gap-2'>
          <Clock className='h-4 w-4 text-muted-foreground' />
          <h4 className='text-sm font-semibold'>Task Run History</h4>
        </div>

        {/* Filter Bar */}
        <div className='flex flex-wrap items-center gap-3'>
          <Select
            value={stateFilter || 'all'}
            onValueChange={handleStateFilterChange}
          >
            <SelectTrigger className='w-[140px]'>
              <SelectValue placeholder='State' />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='all'>All States</SelectItem>
              <SelectItem value='SUCCESS'>Success</SelectItem>
              <SelectItem value='FAILED'>Failed</SelectItem>
              <SelectItem value='RUNNING'>Running</SelectItem>
            </SelectContent>
          </Select>

          <div className='ml-auto text-sm text-muted-foreground'>
            {total} {total === 1 ? 'run' : 'runs'}
          </div>
        </div>

        {/* Runs Table */}
        <div className='rounded-md border border-border'>
          <div className='overflow-x-auto'>
            <table className='w-full'>
              <thead className='border-b border-border bg-muted/50'>
                <tr>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Query ID
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Task Name
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Created
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Finished
                  </th>
                  <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                    Duration
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    State
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Error
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
                      Loading run history…
                    </td>
                  </tr>
                ) : runs.length === 0 ? (
                  <tr>
                    <td
                      colSpan={7}
                      className='px-4 py-12 text-center text-sm text-muted-foreground'
                    >
                      <div className='flex flex-col items-center gap-2'>
                        <AlertTriangle className='h-4 w-4 text-muted-foreground' />
                        <span>No task runs found</span>
                        {stateFilter && (
                          <span className='text-xs'>
                            Try clearing the state filter
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ) : (
                  runs.map((run) => (
                    <tr
                      key={run.query_id}
                      className='border-b border-border transition-colors last:border-b-0 hover:bg-muted/50'
                    >
                      <td className='px-4 py-3'>
                        <code className='rounded bg-muted px-1.5 py-0.5 text-xs font-mono'>
                          {run.query_id.length > 12
                            ? run.query_id.slice(0, 12) + '…'
                            : run.query_id}
                        </code>
                      </td>
                      <td className='px-4 py-3 text-sm font-medium'>
                        {run.task_name}
                      </td>
                      <td className='px-4 py-3 text-xs text-muted-foreground whitespace-nowrap'>
                        {formatTime(run.create_time)}
                      </td>
                      <td className='px-4 py-3 text-xs text-muted-foreground whitespace-nowrap'>
                        {formatTime(run.finish_time)}
                      </td>
                      <td className='px-4 py-3 text-right text-xs font-medium'>
                        {formatDuration(run.create_time, run.finish_time)}
                      </td>
                      <td className='px-4 py-3'>
                        <Badge
                          variant='outline'
                          className={cn(
                            'text-xs',
                            stateBadgeClasses(run.state)
                          )}
                        >
                          {stateIcon(run.state)}
                          {run.state}
                        </Badge>
                      </td>
                      <td className='px-4 py-3 text-xs max-w-xs'>
                        {run.error_message ? (
                          <span
                            className='line-clamp-2 text-destructive'
                            title={run.error_message}
                          >
                            {run.error_message}
                          </span>
                        ) : (
                          <span className='text-muted-foreground'>—</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Pagination */}
        {total > 0 && (
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
        )}
      </section>
    </div>
  )
}
