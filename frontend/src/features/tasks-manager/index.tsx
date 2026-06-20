import { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { SimpleTablePagination, SimpleTableToolbar, SimpleTableViewport } from '@/components/data-table/simple-table-controls'
import { Plus, Trash2, Pause, Play, ChevronDown, ChevronRight, ListTodo } from 'lucide-react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Task {
  name: string
  state: 'ACTIVE' | 'PAUSE'
  schedule: string
  database: string
  sql: string
  created_at: string
  interval?: string
}

interface TaskRun {
  run_time: string
  finish_time: string | null
  state: 'RUNNING' | 'SUCCESS' | 'FAILED'
  error: string | null
}

interface TasksResponse {
  tasks: Task[]
  total: number
}

type StateFilter = 'ALL' | 'ACTIVE' | 'PAUSE'
type ScheduleType = 'one-time' | 'periodic'

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const fetchTasks = async (): Promise<TasksResponse> => {
  const res = await api.get('/api/v1/tasks')
  return res.data
}

const fetchTaskRuns = async (name: string): Promise<TaskRun[]> => {
  const res = await api.get(`/api/v1/tasks/${encodeURIComponent(name)}/runs`)
  return res.data.runs ?? res.data
}

const createTask = async (payload: Record<string, unknown>) => {
  const res = await api.post('/api/v1/tasks', payload)
  return res.data
}

const patchTaskState = async ({ name, state }: { name: string; state: string }) => {
  const res = await api.patch(`/api/v1/tasks/${encodeURIComponent(name)}`, { state })
  return res.data
}

const deleteTask = async (name: string) => {
  await api.delete(`/api/v1/tasks/${encodeURIComponent(name)}`)
}

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

const stateBadgeVariant: Record<string, string> = {
  ACTIVE: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  PAUSE: 'bg-amber-100 text-amber-800 border-amber-200',
  RUNNING: 'bg-blue-100 text-blue-800 border-blue-200',
  SUCCESS: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  FAILED: 'bg-red-100 text-red-800 border-red-200',
}

function StateBadge({ value }: { value: string }) {
  return (
    <Badge variant="outline" className={stateBadgeVariant[value] ?? ''}>
      {value}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Create Task Dialog
// ---------------------------------------------------------------------------

function CreateTaskDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [sql, setSql] = useState('')
  const [database, setDatabase] = useState('')
  const [scheduleType, setScheduleType] = useState<ScheduleType>('one-time')
  const [interval, setInterval] = useState('')

  const mutation = useMutation({
    mutationFn: createTask,
    onSuccess: () => {
      toast.success('Task created successfully')
      qc.invalidateQueries({ queryKey: ['tasks'] })
      resetAndClose()
    },
    onError: (err: Error & { response?: { data?: { detail?: string } } }) => {
      toast.error(err?.response?.data?.detail ?? 'Failed to create task')
    },
  })

  function resetAndClose() {
    setName('')
    setSql('')
    setDatabase('')
    setScheduleType('one-time')
    setInterval('')
    onOpenChange(false)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || !sql.trim() || !database.trim()) {
      toast.error('Name, SQL, and Database are required')
      return
    }
    const payload: Record<string, unknown> = { name: name.trim(), sql: sql.trim(), database: database.trim() }
    if (scheduleType === 'periodic') {
      if (!interval.trim()) {
        toast.error('Interval is required for periodic tasks')
        return
      }
      payload.schedule = 'periodic'
      payload.interval = interval.trim()
    } else {
      payload.schedule = 'one-time'
    }
    mutation.mutate(payload)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Create Task</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="task-name">Name</Label>
            <Input id="task-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="my_etl_task" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="task-sql">SQL</Label>
            <Textarea
              id="task-sql"
              rows={5}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              placeholder="INSERT INTO target SELECT * FROM source"
              className="font-mono text-sm"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="task-db">Database</Label>
            <Input id="task-db" value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="analytics" />
          </div>
          <div className="space-y-1">
            <Label>Schedule</Label>
            <Select value={scheduleType} onValueChange={(v) => setScheduleType(v as ScheduleType)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="one-time">One-time</SelectItem>
                <SelectItem value="periodic">Periodic</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {scheduleType === 'periodic' && (
            <div className="space-y-1">
              <Label htmlFor="task-interval">Interval</Label>
              <Input
                id="task-interval"
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
                placeholder="1 HOUR"
              />
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={resetAndClose}>Cancel</Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? 'Creating…' : 'Create Task'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Expanded Row – Run History
// ---------------------------------------------------------------------------

function RunHistory({ taskName }: { taskName: string }) {
  const { data: runs, isLoading } = useQuery({
    queryKey: ['task-runs', taskName],
    queryFn: () => fetchTaskRuns(taskName),
    placeholderData: keepPreviousData,
  })

  if (isLoading) return <p className="text-sm text-muted-foreground py-2">Loading run history…</p>
  if (!runs || runs.length === 0) return <p className="text-sm text-muted-foreground py-2">No runs recorded yet.</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border rounded-md">
        <thead>
          <tr className="border-b bg-muted/40">
            <th className="text-left px-3 py-1.5 font-medium">Run Time</th>
            <th className="text-left px-3 py-1.5 font-medium">Finish Time</th>
            <th className="text-left px-3 py-1.5 font-medium">State</th>
            <th className="text-left px-3 py-1.5 font-medium">Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r, i) => (
            <tr key={i} className="border-b last:border-b-0">
              <td className="px-3 py-1.5 whitespace-nowrap">{r.run_time}</td>
              <td className="px-3 py-1.5 whitespace-nowrap">{r.finish_time ?? '—'}</td>
              <td className="px-3 py-1.5"><StateBadge value={r.state} /></td>
              <td className="px-3 py-1.5 text-red-600 max-w-xs truncate">{r.error ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

const PAGE_SIZE = 10

export default function TasksManager() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [stateFilter, setStateFilter] = useState<StateFilter>('ALL')
  const [expandedName, setExpandedName] = useState<string | null>(null)
  const [page, setPage] = useState(0)
  const [dialogOpen, setDialogOpen] = useState(false)

  // Data
  const { data, isLoading, placeholderData } = useQuery({
    queryKey: ['tasks'],
    queryFn: fetchTasks,
    placeholderData: keepPreviousData,
  })

  const tasks: Task[] = data?.tasks ?? []

  // Client-side filtering
  const filtered = useMemo(() => {
    let result = tasks
    if (stateFilter !== 'ALL') result = result.filter((t) => t.state === stateFilter)
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          t.database.toLowerCase().includes(q) ||
          t.schedule.toLowerCase().includes(q),
      )
    }
    return result
  }, [tasks, stateFilter, search])

  // Client-side pagination
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages - 1)
  const paged = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)

  useEffect(() => {
    if (page !== safePage) setPage(safePage)
  }, [safePage, page])

  // Reset to page 0 when filters change
  useEffect(() => {
    setPage(0)
  }, [search, stateFilter])

  // Mutations
  const toggleState = useMutation({
    mutationFn: patchTaskState,
    onSuccess: () => {
      toast.success('Task state updated')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: () => toast.error('Failed to update task state'),
  })

  const removeTask = useMutation({
    mutationFn: deleteTask,
    onSuccess: () => {
      toast.success('Task deleted')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: () => toast.error('Failed to delete task'),
  })

  function handleToggle(task: Task) {
    const next = task.state === 'ACTIVE' ? 'PAUSE' : 'ACTIVE'
    toggleState.mutate({ name: task.name, state: next })
  }

  function handleDelete(name: string) {
    if (confirm(`Delete task "${name}"? This cannot be undone.`)) {
      removeTask.mutate(name)
    }
  }

  return (
    <div className="space-y-4 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <ListTodo className="h-7 w-7 text-primary" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
          <p className="text-sm text-muted-foreground">Manage asynchronous ETL tasks and monitor their execution.</p>
        </div>
      </div>

      {/* Toolbar */}
      <SimpleTableToolbar>
        <div className="flex items-center gap-2 flex-1">
          <Input
            placeholder="Search tasks…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="max-w-xs h-9"
          />
          <Select value={stateFilter} onValueChange={(v) => setStateFilter(v as StateFilter)}>
            <SelectTrigger className="w-32 h-9"><SelectValue placeholder="State" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">All</SelectItem>
              <SelectItem value="ACTIVE">Active</SelectItem>
              <SelectItem value="PAUSE">Paused</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" onClick={() => setDialogOpen(true)}>
          <Plus className="h-4 w-4 mr-1" /> Create Task
        </Button>
      </SimpleTableToolbar>

      {/* Table */}
      <SimpleTableViewport>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="w-8" />
              <th className="text-left px-3 py-2 font-medium">Name</th>
              <th className="text-left px-3 py-2 font-medium">State</th>
              <th className="text-left px-3 py-2 font-medium">Schedule</th>
              <th className="text-left px-3 py-2 font-medium">Database</th>
              <th className="text-left px-3 py-2 font-medium">Created</th>
              <th className="text-right px-3 py-2 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={7} className="text-center py-8 text-muted-foreground">Loading tasks…</td>
              </tr>
            )}
            {!isLoading && paged.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center py-8 text-muted-foreground">No tasks found.</td>
              </tr>
            )}
            {paged.map((task) => {
              const isExpanded = expandedName === task.name
              return (
                <TableRow
                  key={task.name}
                  task={task}
                  isExpanded={isExpanded}
                  onToggleExpand={() => setExpandedName(isExpanded ? null : task.name)}
                  onToggleState={() => handleToggle(task)}
                  onDelete={() => handleDelete(task.name)}
                  isPending={toggleState.isPending || removeTask.isPending}
                />
              )
            })}
          </tbody>
        </table>
      </SimpleTableViewport>

      {/* Pagination */}
      <SimpleTablePagination
        page={safePage}
        pageCount={totalPages}
        pageSize={PAGE_SIZE}
        total={filtered.length}
        onPageChange={setPage}
      />

      {/* Create Dialog */}
      <CreateTaskDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Table Row (extracted to avoid deep nesting in JSX)
// ---------------------------------------------------------------------------

function TableRow({
  task,
  isExpanded,
  onToggleExpand,
  onToggleState,
  onDelete,
  isPending,
}: {
  task: Task
  isExpanded: boolean
  onToggleExpand: () => void
  onToggleState: () => void
  onDelete: () => void
  isPending: boolean
}) {
  return (
    <>
      <tr
        className="border-b hover:bg-muted/30 cursor-pointer transition-colors"
        onClick={onToggleExpand}
      >
        <td className="px-2 py-2 text-muted-foreground">
          {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </td>
        <td className="px-3 py-2 font-medium">{task.name}</td>
        <td className="px-3 py-2"><StateBadge value={task.state} /></td>
        <td className="px-3 py-2 text-muted-foreground">{task.schedule}{task.interval ? ` (${task.interval})` : ''}</td>
        <td className="px-3 py-2 text-muted-foreground">{task.database}</td>
        <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
          {task.created_at ? new Date(task.created_at).toLocaleDateString() : '—'}
        </td>
        <td className="px-3 py-2">
          <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              disabled={isPending}
              onClick={onToggleState}
              title={task.state === 'ACTIVE' ? 'Pause task' : 'Resume task'}
            >
              {task.state === 'ACTIVE' ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-red-600 hover:text-red-700"
              disabled={isPending}
              onClick={onDelete}
              title="Delete task"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </td>
      </tr>
      {isExpanded && (
        <tr className="bg-muted/20">
          <td colSpan={7} className="px-6 py-4 space-y-4">
            {/* SQL */}
            <div>
              <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-1">SQL</h4>
              <pre className="bg-muted rounded-md p-3 text-xs font-mono overflow-x-auto whitespace-pre-wrap">
                {task.sql || '—'}
              </pre>
            </div>
            {/* Run history */}
            <div>
              <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-1">Run History</h4>
              <RunHistory taskName={task.name} />
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
