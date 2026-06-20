import { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { SimpleTableToolbar, SimpleTableViewport } from '@/components/data-table/simple-table-controls'
import { Plus, Trash2, Code2, ChevronDown, ChevronRight } from 'lucide-react'

// ── Types ────────────────────────────────────────────────────────────────────

interface BuiltinFunction {
  name: string
  category: string
  signature: string
  return_type: string
  description: string
}

interface UserDefinedFunction {
  name: string
  database: string
  function_type: string
  args: string
  return_type: string
  body: string
}

interface BuiltinResponse {
  functions: BuiltinFunction[]
  categories: string[]
}

interface UDFResponse {
  functions: UserDefinedFunction[]
  databases: string[]
}

type Tab = 'builtin' | 'udf'

// ── Built-in Functions Tab ───────────────────────────────────────────────────

function BuiltinFunctionsTab() {
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [expanded, setExpanded] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['functions', 'builtin'],
    queryFn: () => api.get<BuiltinResponse>('/functions/builtin'),
  })

  const categories = data?.categories ?? []
  const filtered = useMemo(() => {
    const fns = data?.functions ?? []
    return fns.filter((fn) => {
      const matchSearch =
        !search ||
        fn.name.toLowerCase().includes(search.toLowerCase()) ||
        fn.signature.toLowerCase().includes(search.toLowerCase())
      const matchCat = category === 'all' || fn.category === category
      return matchSearch && matchCat
    })
  }, [data?.functions, search, category])

  return (
    <div className="space-y-3">
      <SimpleTableToolbar>
        <Input
          placeholder="Search functions…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs h-8"
        />
        <Select value={category} onValueChange={setCategory}>
          <SelectTrigger className="w-40 h-8">
            <SelectValue placeholder="All categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All categories</SelectItem>
            {categories.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </SimpleTableToolbar>

      <SimpleTableViewport>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="w-8 px-3 py-2" />
              <th className="px-3 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">Category</th>
              <th className="px-3 py-2 font-medium">Signature</th>
              <th className="px-3 py-2 font-medium">Return Type</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">
                  Loading functions…
                </td>
              </tr>
            )}
            {!isLoading && filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">
                  No functions found.
                </td>
              </tr>
            )}
            {filtered.map((fn) => (
              <>
                <tr
                  key={fn.name}
                  className="border-b cursor-pointer hover:bg-muted/50 transition-colors"
                  onClick={() => setExpanded(expanded === fn.name ? null : fn.name)}
                >
                  <td className="px-3 py-2 text-muted-foreground">
                    {expanded === fn.name ? (
                      <ChevronDown className="h-3.5 w-3.5" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5" />
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono font-medium">{fn.name}</td>
                  <td className="px-3 py-2">
                    <Badge variant="secondary" className="text-xs">
                      {fn.category}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-muted-foreground">{fn.signature}</td>
                  <td className="px-3 py-2 font-mono text-muted-foreground">{fn.return_type}</td>
                </tr>
                {expanded === fn.name && (
                  <tr key={`${fn.name}-desc`} className="border-b bg-muted/30">
                    <td colSpan={5} className="px-10 py-3 text-sm text-muted-foreground">
                      {fn.description || 'No description available.'}
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </SimpleTableViewport>

      <p className="text-xs text-muted-foreground px-1">
        {filtered.length} function{filtered.length !== 1 ? 's' : ''}
      </p>
    </div>
  )
}

// ── Create UDF Dialog ────────────────────────────────────────────────────────

function CreateUDFDialog({
  open,
  onOpenChange,
  databases,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  databases: string[]
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [database, setDatabase] = useState('')
  const [functionType, setFunctionType] = useState('sql')
  const [args, setArgs] = useState('')
  const [returnType, setReturnType] = useState('')
  const [body, setBody] = useState('')

  useEffect(() => {
    if (!open) {
      setName('')
      setDatabase('')
      setFunctionType('sql')
      setArgs('')
      setReturnType('')
      setBody('')
    }
  }, [open])

  const mutation = useMutation({
    mutationFn: () =>
      api.post('/functions/udf', {
        name,
        database,
        function_type: functionType,
        args,
        return_type: returnType,
        body,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['functions', 'udf'] })
      toast.success(`Function "${name}" created`)
      onOpenChange(false)
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to create function')
    },
  })

  const canSubmit = name && database && returnType && body && !mutation.isPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Create User-Defined Function</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="my_func" />
            </div>
            <div className="space-y-1.5">
              <Label>Database</Label>
              <Select value={database} onValueChange={setDatabase}>
                <SelectTrigger>
                  <SelectValue placeholder="Select database" />
                </SelectTrigger>
                <SelectContent>
                  {databases.map((db) => (
                    <SelectItem key={db} value={db}>
                      {db}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Type</Label>
              <Select value={functionType} onValueChange={setFunctionType}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="sql">SQL</SelectItem>
                  <SelectItem value="java">Java</SelectItem>
                  <SelectItem value="python">Python</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Return Type</Label>
              <Input
                value={returnType}
                onChange={(e) => setReturnType(e.target.value)}
                placeholder="VARCHAR"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>Arguments</Label>
            <Input
              value={args}
              onChange={(e) => setArgs(e.target.value)}
              placeholder="x INTEGER, y VARCHAR"
            />
          </div>
          <div className="space-y-1.5">
            <Label>Body</Label>
            <Textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="SELECT x + 1"
              rows={4}
              className="font-mono text-xs"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!canSubmit}>
            {mutation.isPending ? 'Creating…' : 'Create Function'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── User-Defined Functions Tab ───────────────────────────────────────────────

function UDFTab() {
  const queryClient = useQueryClient()
  const [dbFilter, setDbFilter] = useState('all')
  const [createOpen, setCreateOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['functions', 'udf'],
    queryFn: () => api.get<UDFResponse>('/functions/udf'),
  })

  const databases = data?.databases ?? []
  const filtered = useMemo(() => {
    const fns = data?.functions ?? []
    return dbFilter === 'all' ? fns : fns.filter((fn) => fn.database === dbFilter)
  }, [data?.functions, dbFilter])

  const deleteMutation = useMutation({
    mutationFn: (fn: UserDefinedFunction) =>
      api.delete(`/functions/udf/${fn.database}/${fn.name}`),
    onSuccess: (_res, fn) => {
      queryClient.invalidateQueries({ queryKey: ['functions', 'udf'] })
      toast.success(`Function "${fn.name}" deleted`)
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to delete function')
    },
  })

  return (
    <div className="space-y-3">
      <SimpleTableToolbar>
        <Select value={dbFilter} onValueChange={setDbFilter}>
          <SelectTrigger className="w-40 h-8">
            <SelectValue placeholder="All databases" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All databases</SelectItem>
            {databases.map((db) => (
              <SelectItem key={db} value={db}>
                {db}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex-1" />
        <Button size="sm" className="h-8 gap-1.5" onClick={() => setCreateOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          Create UDF
        </Button>
      </SimpleTableToolbar>

      <SimpleTableViewport>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="px-3 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">Database</th>
              <th className="px-3 py-2 font-medium">Type</th>
              <th className="px-3 py-2 font-medium">Args</th>
              <th className="px-3 py-2 font-medium">Return Type</th>
              <th className="w-16 px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">
                  Loading functions…
                </td>
              </tr>
            )}
            {!isLoading && filtered.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">
                  No user-defined functions found.
                </td>
              </tr>
            )}
            {filtered.map((fn) => (
              <tr key={`${fn.database}.${fn.name}`} className="border-b hover:bg-muted/50 transition-colors">
                <td className="px-3 py-2 font-mono font-medium">{fn.name}</td>
                <td className="px-3 py-2 text-muted-foreground">{fn.database}</td>
                <td className="px-3 py-2">
                  <Badge variant="outline" className="text-xs font-mono">
                    {fn.function_type}
                  </Badge>
                </td>
                <td className="px-3 py-2 font-mono text-muted-foreground text-xs">
                  {fn.args || '—'}
                </td>
                <td className="px-3 py-2 font-mono text-muted-foreground">{fn.return_type}</td>
                <td className="px-3 py-2 text-right">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                    onClick={() => deleteMutation.mutate(fn)}
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </SimpleTableViewport>

      <p className="text-xs text-muted-foreground px-1">
        {filtered.length} function{filtered.length !== 1 ? 's' : ''}
      </p>

      <CreateUDFDialog open={createOpen} onOpenChange={setCreateOpen} databases={databases} />
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function FunctionsPage() {
  const [tab, setTab] = useState<Tab>('builtin')

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <Code2 className="h-6 w-6 text-muted-foreground" />
          Functions
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Browse built-in functions and manage user-defined functions.
        </p>
      </div>

      <div className="flex gap-1 border-b">
        {(['builtin', 'udf'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
              tab === t
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t === 'builtin' ? 'Built-in Functions' : 'User-Defined Functions'}
          </button>
        ))}
      </div>

      {tab === 'builtin' ? <BuiltinFunctionsTab /> : <UDFTab />}
    </div>
  )
}
