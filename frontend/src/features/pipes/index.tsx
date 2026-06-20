import { Fragment, useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { SimpleTablePagination, SimpleTableToolbar, SimpleTableViewport } from '@/components/data-table/simple-table-controls'
import { Plus, Trash2, Pause, Play, ChevronDown, ChevronRight, Upload } from 'lucide-react'

// ── Types ────────────────────────────────────────────────────────────────────

type PipeState = 'RUNNING' | 'SUSPENDED' | 'ERROR'
type FileState = 'LOADED' | 'LOADING' | 'ERROR'

interface Pipe {
  name: string
  state: PipeState
  database: string
  sql: string
  auto_ingest: boolean
  poll_interval: number
  batch_size: string
  batch_files: number
}

interface PipeFile {
  file_name: string
  state: FileState
  file_size: number
  error_message: string | null
}

interface CreatePipePayload {
  name: string
  database: string
  sql: string
  auto_ingest: boolean
  poll_interval: number
  batch_size: string
  batch_files: number
}

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 10

const stateBadge: Record<PipeState, { label: string; className: string }> = {
  RUNNING: { label: 'Running', className: 'bg-green-100 text-green-800' },
  SUSPENDED: { label: 'Suspended', className: 'bg-yellow-100 text-yellow-800' },
  ERROR: { label: 'Error', className: 'bg-red-100 text-red-800' },
}

const fileStateBadge: Record<FileState, { label: string; className: string }> = {
  LOADED: { label: 'Loaded', className: 'bg-green-100 text-green-800' },
  LOADING: { label: 'Loading', className: 'bg-blue-100 text-blue-800' },
  ERROR: { label: 'Error', className: 'bg-red-100 text-red-800' },
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

function truncate(sql: string, max = 80): string {
  return sql.length > max ? sql.slice(0, max) + '…' : sql
}

// ── Component ────────────────────────────────────────────────────────────────

export default function PipesPage() {
  const queryClient = useQueryClient()

  // UI state
  const [search, setSearch] = useState('')
  const [dbFilter, setDbFilter] = useState('')
  const [page, setPage] = useState(0)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  // Create form state
  const [form, setForm] = useState<CreatePipePayload>({
    name: '',
    database: '',
    sql: '',
    auto_ingest: true,
    poll_interval: 300,
    batch_size: '1GB',
    batch_files: 256,
  })

  // ── Data fetching ────────────────────────────────────────────────────────

  const { data: pipes = [], isLoading } = useQuery({
    queryKey: ['pipes', search, dbFilter],
    queryFn: () => api.get<Pipe[]>('/api/v1/pipes'),
    placeholderData: keepPreviousData,
  })

  const { data: pipeFiles } = useQuery({
    queryKey: ['pipe-files', expanded],
    queryFn: () => api.get<PipeFile[]>(`/api/v1/pipes/${expanded}/files`),
    enabled: !!expanded,
  })

  // ── Mutations ────────────────────────────────────────────────────────────

  const createMutation = useMutation({
    mutationFn: (payload: CreatePipePayload) =>
      api.post('/api/v1/pipes', payload),
    onSuccess: () => {
      toast.success('Pipe created')
      queryClient.invalidateQueries({ queryKey: ['pipes'] })
      setDialogOpen(false)
      resetForm()
    },
    onError: () => toast.error('Failed to create pipe'),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ name, action }: { name: string; action: 'suspend' | 'resume' }) =>
      api.patch(`/api/v1/pipes/${name}`, { action }),
    onSuccess: () => {
      toast.success('Pipe state updated')
      queryClient.invalidateQueries({ queryKey: ['pipes'] })
    },
    onError: () => toast.error('Failed to update pipe'),
  })

  const deleteMutation = useMutation({
    mutationFn: (name: string) => api.delete(`/api/v1/pipes/${name}`),
    onSuccess: () => {
      toast.success('Pipe deleted')
      queryClient.invalidateQueries({ queryKey: ['pipes'] })
    },
    onError: () => toast.error('Failed to delete pipe'),
  })

  // ── Derived data ─────────────────────────────────────────────────────────

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    const db = dbFilter.toLowerCase()
    return pipes.filter(
      (p) =>
        (!q || p.name.toLowerCase().includes(q) || p.sql.toLowerCase().includes(q)) &&
        (!db || p.database.toLowerCase().includes(db))
    )
  }, [pipes, search, dbFilter])

  const databases = useMemo(
    () => [...new Set(pipes.map((p) => p.database))].sort(),
    [pipes]
  )

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  // Reset page when filters change
  useEffect(() => { setPage(0) }, [search, dbFilter])

  // ── Form helpers ─────────────────────────────────────────────────────────

  function resetForm() {
    setForm({ name: '', database: '', sql: '', auto_ingest: true, poll_interval: 300, batch_size: '1GB', batch_files: 256 })
  }

  function handleCreate() {
    if (!form.name || !form.database || !form.sql) {
      toast.error('Name, database, and SQL are required')
      return
    }
    createMutation.mutate(form)
  }

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Pipes</h1>
        <p className="text-muted-foreground">
          Manage continuous data ingestion pipes from object storage.
        </p>
      </div>

      {/* Toolbar */}
      <SimpleTableToolbar>
        <Input
          placeholder="Search pipes…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <select
          value={dbFilter}
          onChange={(e) => setDbFilter(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="">All databases</option>
          {databases.map((db) => (
            <option key={db} value={db}>{db}</option>
          ))}
        </select>
        <div className="flex-1" />
        <Button onClick={() => setDialogOpen(true)}>
          <Plus className="mr-2 h-4 w-4" /> Create Pipe
        </Button>
      </SimpleTableToolbar>

      {/* Table */}
      <SimpleTableViewport>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              <th className="w-8" />
              <th className="px-3 py-2 text-left font-medium">Name</th>
              <th className="px-3 py-2 text-left font-medium">State</th>
              <th className="px-3 py-2 text-left font-medium">Database</th>
              <th className="px-3 py-2 text-left font-medium">SQL</th>
              <th className="px-3 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">Loading…</td></tr>
            )}
            {!isLoading && paged.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">No pipes found.</td></tr>
            )}
            {paged.map((pipe) => {
              const isOpen = expanded === pipe.name
              const badge = stateBadge[pipe.state]
              return (
                <Fragment key={pipe.name}>
                  <tr
                    className="border-b hover:bg-muted/50 cursor-pointer"
                    onClick={() => setExpanded(isOpen ? null : pipe.name)}
                  >
                    <td className="px-3 py-2">
                      {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </td>
                    <td className="px-3 py-2 font-medium">{pipe.name}</td>
                    <td className="px-3 py-2">
                      <Badge variant="outline" className={badge.className}>{badge.label}</Badge>
                    </td>
                    <td className="px-3 py-2">{pipe.database}</td>
                    <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{truncate(pipe.sql)}</td>
                    <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() =>
                            toggleMutation.mutate({
                              name: pipe.name,
                              action: pipe.state === 'RUNNING' ? 'suspend' : 'resume',
                            })
                          }
                          title={pipe.state === 'RUNNING' ? 'Suspend' : 'Resume'}
                        >
                          {pipe.state === 'RUNNING' ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => {
                            if (confirm(`Delete pipe "${pipe.name}"?`)) deleteMutation.mutate(pipe.name)
                          }}
                          title="Delete"
                        >
                          <Trash2 className="h-4 w-4 text-red-500" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={6} className="bg-muted/30 px-6 py-4 space-y-4">
                        {/* Full SQL */}
                        <div>
                          <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">SQL Statement</p>
                          <pre className="rounded bg-muted p-3 text-xs font-mono whitespace-pre-wrap">{pipe.sql}</pre>
                        </div>
                        {/* File status */}
                        <div>
                          <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground flex items-center gap-1">
                            <Upload className="h-3 w-3" /> File Ingestion Status
                          </p>
                          {!pipeFiles && <p className="text-xs text-muted-foreground">Loading files…</p>}
                          {pipeFiles && pipeFiles.length === 0 && (
                            <p className="text-xs text-muted-foreground">No files ingested yet.</p>
                          )}
                          {pipeFiles && pipeFiles.length > 0 && (
                            <table className="w-full text-xs border rounded">
                              <thead>
                                <tr className="border-b bg-background">
                                  <th className="px-3 py-1.5 text-left font-medium">File Name</th>
                                  <th className="px-3 py-1.5 text-left font-medium">State</th>
                                  <th className="px-3 py-1.5 text-left font-medium">File Size</th>
                                  <th className="px-3 py-1.5 text-left font-medium">Error Message</th>
                                </tr>
                              </thead>
                              <tbody>
                                {pipeFiles.map((f) => {
                                  const fb = fileStateBadge[f.state]
                                  return (
                                    <tr key={f.file_name} className="border-b last:border-0">
                                      <td className="px-3 py-1.5 font-mono">{f.file_name}</td>
                                      <td className="px-3 py-1.5">
                                        <Badge variant="outline" className={fb.className}>{fb.label}</Badge>
                                      </td>
                                      <td className="px-3 py-1.5">{formatBytes(f.file_size)}</td>
                                      <td className="px-3 py-1.5 text-red-600">{f.error_message ?? '—'}</td>
                                    </tr>
                                  )
                                })}
                              </tbody>
                            </table>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </SimpleTableViewport>

      {/* Pagination */}
      <SimpleTablePagination
        page={page}
        pageCount={pageCount}
        onPageChange={setPage}
        totalRows={filtered.length}
      />

      {/* Create Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Create Pipe</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <Label htmlFor="pipe-name">Name</Label>
                <Input
                  id="pipe-name"
                  placeholder="my-pipe"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="pipe-db">Database</Label>
                <Input
                  id="pipe-db"
                  placeholder="analytics"
                  value={form.database}
                  onChange={(e) => setForm({ ...form, database: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="pipe-sql">SQL Statement</Label>
              <Textarea
                id="pipe-sql"
                rows={4}
                placeholder="INSERT INTO target SELECT * FROM FILES('s3://bucket/path/')"
                value={form.sql}
                onChange={(e) => setForm({ ...form, sql: e.target.value })}
              />
            </div>
            <div className="flex items-center space-x-2">
              <Checkbox
                id="auto-ingest"
                checked={form.auto_ingest}
                onCheckedChange={(v) => setForm({ ...form, auto_ingest: !!v })}
              />
              <Label htmlFor="auto-ingest">Auto-ingest (continuous polling)</Label>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-1">
                <Label htmlFor="poll-interval">Poll Interval (s)</Label>
                <Input
                  id="poll-interval"
                  type="number"
                  value={form.poll_interval}
                  onChange={(e) => setForm({ ...form, poll_interval: Number(e.target.value) })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="batch-size">Batch Size</Label>
                <Input
                  id="batch-size"
                  placeholder="1GB"
                  value={form.batch_size}
                  onChange={(e) => setForm({ ...form, batch_size: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="batch-files">Batch Files</Label>
                <Input
                  id="batch-files"
                  type="number"
                  value={form.batch_files}
                  onChange={(e) => setForm({ ...form, batch_files: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={handleCreate} disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Creating…' : 'Create Pipe'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}


