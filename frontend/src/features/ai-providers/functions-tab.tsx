import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  BadgeCheck,
  CheckCircle2,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Wand2,
  XCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'

// ── Types ──────────────────────────────────────────────────────

type FunctionType =
  | 'complete'
  | 'sentiment'
  | 'classify'
  | 'summarize'
  | 'extract'
  | 'translate'
  | 'filter'
  | 'embed'

type Alias = {
  id: string
  alias_name: string
  function_type: string
  provider_id: string
  provider_name: string | null
  model_id: string | null
  model_name: string | null
  system_prompt: string | null
  default_params: Record<string, unknown> | null
  is_default: boolean
  is_active: boolean
  created_at: string | null
  updated_at: string | null
  created_by: string | null
}

type UDFStatusItem = {
  function_name: string
  function_type: string
  alias_name: string | null
  provider_name: string | null
  model_name: string | null
  registered: boolean
  error: string | null
}

type ProviderOption = {
  id: string
  name: string
}

type ModelOption = {
  id: string
  name: string
  display_name: string | null
}

// ── Constants ───────────────────────────────────────────────────

const FUNCTION_TYPES: { value: FunctionType; label: string; udfName: string }[] =
  [
    { value: 'complete', label: 'Complete', udfName: 'AI_COMPLETE' },
    { value: 'sentiment', label: 'Sentiment', udfName: 'AI_SENTIMENT' },
    { value: 'classify', label: 'Classify', udfName: 'AI_CLASSIFY' },
    { value: 'summarize', label: 'Summarize', udfName: 'AI_SUMMARIZE' },
    { value: 'extract', label: 'Extract', udfName: 'AI_EXTRACT' },
    { value: 'translate', label: 'Translate', udfName: 'AI_TRANSLATE' },
    { value: 'filter', label: 'Filter', udfName: 'AI_FILTER' },
    { value: 'embed', label: 'Embed', udfName: 'AI_EMBED' },
  ]

const FUNCTION_TYPE_COLORS: Record<string, string> = {
  complete: 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20',
  sentiment:
    'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20',
  classify:
    'bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20',
  summarize:
    'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20',
  extract:
    'bg-pink-500/10 text-pink-600 dark:text-pink-400 border-pink-500/20',
  translate:
    'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20',
  filter: 'bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20',
  embed: 'bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-500/20',
}

const emptyAliasForm = {
  alias_name: '',
  function_type: 'complete' as FunctionType,
  provider_id: '',
  model_id: '',
  system_prompt: '',
  default_params: '',
  is_default: true,
}

// ── Helpers ─────────────────────────────────────────────────────

function getUDFName(functionType: string): string {
  return (
    FUNCTION_TYPES.find((ft) => ft.value === functionType)?.udfName ??
    `AI_${functionType.toUpperCase()}`
  )
}

function getFunctionTypeLabel(functionType: string): string {
  return (
    FUNCTION_TYPES.find((ft) => ft.value === functionType)?.label ??
    functionType
  )
}

// ── Component ───────────────────────────────────────────────────

export function FunctionsTab() {
  const [aliases, setAliases] = useState<Alias[]>([])
  const [udfStatuses, setUdfStatuses] = useState<UDFStatusItem[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  // Provider/model options for the form
  const [providerOptions, setProviderOptions] = useState<ProviderOption[]>([])
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])

  // Dialog state
  const [aliasDialogOpen, setAliasDialogOpen] = useState(false)
  const [aliasEditMode, setAliasEditMode] = useState<'create' | 'edit'>(
    'create'
  )
  const [editingAliasId, setEditingAliasId] = useState<string | null>(null)

  const [deleteDialog, setDeleteDialog] = useState<Alias | null>(null)

  // Form state
  const [aliasForm, setAliasForm] = useState({ ...emptyAliasForm })
  const [submitting, setSubmitting] = useState(false)

  // Register UDFs state
  const [registering, setRegistering] = useState(false)
  const [showUdfStatus, setShowUdfStatus] = useState(false)

  // ── Data fetching ─────────────────────────────────────────────

  const fetchAliases = useCallback(async () => {
    try {
      const res = await api.get<{ aliases: Alias[]; count: number }>(
        '/ai/aliases'
      )
      setAliases(res.aliases)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to load function aliases'
      )
    }
  }, [])

  const fetchUDFStatus = useCallback(async () => {
    try {
      const res = await api.get<{
        functions: UDFStatusItem[]
        total: number
        registered: number
        failed: number
      }>('/ai/aliases/udf-status')
      setUdfStatuses(res.functions)
    } catch {
      // UDF status is non-critical; silently fail
    }
  }, [])

  const fetchProviderOptions = useCallback(async () => {
    try {
      const res = await api.get<{
        providers: ProviderOption[]
        count: number
      }>('/ai/providers')
      setProviderOptions(res.providers)
    } catch {
      // Non-critical
    }
  }, [])

  const fetchModelOptions = useCallback(
    async (providerId: string) => {
      if (!providerId) {
        setModelOptions([])
        return
      }
      try {
        const res = await api.get<{
          models: ModelOption[]
          count: number
        }>(`/ai/providers/${providerId}/models`)
        setModelOptions(res.models)
      } catch {
        setModelOptions([])
      }
    },
    []
  )

  const fetchAll = useCallback(async () => {
    setLoading(true)
    await Promise.all([fetchAliases(), fetchUDFStatus(), fetchProviderOptions()])
    setLoading(false)
  }, [fetchAliases, fetchUDFStatus, fetchProviderOptions])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  // When provider changes in the form, fetch models
  useEffect(() => {
    if (aliasDialogOpen && aliasForm.provider_id) {
      fetchModelOptions(aliasForm.provider_id)
    }
  }, [aliasDialogOpen, aliasForm.provider_id, fetchModelOptions])

  // ── Build UDF status map ──────────────────────────────────────

  const udfStatusMap = useMemo(() => {
    const map = new Map<string, UDFStatusItem>()
    for (const s of udfStatuses) {
      map.set(s.function_type, s)
    }
    return map
  }, [udfStatuses])

  // ── Filtering ─────────────────────────────────────────────────

  const filtered = useMemo(() => {
    let result = aliases
    if (typeFilter) {
      result = result.filter((a) => a.function_type === typeFilter)
    }
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(
        (a) =>
          a.alias_name.toLowerCase().includes(q) ||
          a.function_type.toLowerCase().includes(q) ||
          (a.provider_name ?? '').toLowerCase().includes(q) ||
          (a.model_name ?? '').toLowerCase().includes(q) ||
          getUDFName(a.function_type).toLowerCase().includes(q)
      )
    }
    return result
  }, [aliases, search, typeFilter])

  // ── Alias Dialog ──────────────────────────────────────────────

  const openCreateAliasDialog = () => {
    setAliasForm({ ...emptyAliasForm })
    setAliasEditMode('create')
    setEditingAliasId(null)
    setModelOptions([])
    setAliasDialogOpen(true)
  }

  const openEditAliasDialog = (alias: Alias) => {
    setAliasForm({
      alias_name: alias.alias_name,
      function_type: alias.function_type as FunctionType,
      provider_id: alias.provider_id,
      model_id: alias.model_id ?? '',
      system_prompt: alias.system_prompt ?? '',
      default_params: alias.default_params
        ? JSON.stringify(alias.default_params)
        : '',
      is_default: alias.is_default,
    })
    setAliasEditMode('edit')
    setEditingAliasId(alias.id)
    setAliasDialogOpen(true)
  }

  const handleSaveAlias = async () => {
    if (!aliasForm.alias_name.trim()) {
      toast.error('Alias name is required')
      return
    }
    if (!aliasForm.provider_id) {
      toast.error('Provider is required')
      return
    }
    setSubmitting(true)
    try {
      const body: Record<string, unknown> = {
        alias_name: aliasForm.alias_name.trim(),
        function_type: aliasForm.function_type,
        provider_id: aliasForm.provider_id,
        is_default: aliasForm.is_default,
      }
      if (aliasForm.model_id) {
        body.model_id = aliasForm.model_id
      }
      if (aliasForm.system_prompt.trim()) {
        body.system_prompt = aliasForm.system_prompt.trim()
      }
      if (aliasForm.default_params.trim()) {
        body.default_params = JSON.parse(aliasForm.default_params)
      }

      if (aliasEditMode === 'edit' && editingAliasId) {
        await api.put(`/ai/aliases/${editingAliasId}`, body)
        toast.success(`Alias '${aliasForm.alias_name}' updated`)
      } else {
        await api.post('/ai/aliases', body)
        toast.success(`Alias '${aliasForm.alias_name}' created`)
      }

      setAliasDialogOpen(false)
      await Promise.all([fetchAliases(), fetchUDFStatus()])
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save alias')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Delete ────────────────────────────────────────────────────

  const handleDelete = async () => {
    if (!deleteDialog) return
    setSubmitting(true)
    try {
      await api.delete(`/ai/aliases/${deleteDialog.id}`)
      toast.success(`Alias '${deleteDialog.alias_name}' deleted`)
      setDeleteDialog(null)
      await Promise.all([fetchAliases(), fetchUDFStatus()])
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete alias')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Register UDFs ─────────────────────────────────────────────

  const handleRegisterUDFs = async () => {
    setRegistering(true)
    try {
      const result = await api.post<{
        success: boolean
        registered: number
        failed: number
        details: UDFStatusItem[]
      }>('/ai/aliases/register-udfs')
      if (result.success) {
        toast.success(
          `Registered ${result.registered} UDF(s) in StarRocks`
        )
      } else {
        toast.warning(
          `Registered ${result.registered}, failed ${result.failed} UDF(s)`
        )
      }
      await fetchUDFStatus()
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to register UDFs'
      )
    } finally {
      setRegistering(false)
    }
  }

  // ── Summary stats ─────────────────────────────────────────────

  const registeredCount = udfStatuses.filter((s) => s.registered).length
  const totalUDFCount = udfStatuses.length
  const failedCount = udfStatuses.filter((s) => !s.registered).length

  // ── Render ────────────────────────────────────────────────────

  const typeFilterOptions = useMemo(
    () => FUNCTION_TYPES.map((ft) => ft.label),
    []
  )

  return (
    <div className='space-y-4'>
      {/* UDF Status Summary Bar */}
      <div className='flex items-center gap-3 rounded-lg border bg-card p-3'>
        <div className='flex items-center gap-2'>
          <Wand2 className='size-4 text-muted-foreground' />
          <span className='text-sm font-medium'>UDF Registration</span>
        </div>
        <div className='flex items-center gap-2'>
          <Badge variant='secondary' className='gap-1 font-mono text-xs'>
            <BadgeCheck className='size-3 text-emerald-500' />
            {registeredCount}/{totalUDFCount} registered
          </Badge>
          {failedCount > 0 && (
            <Badge
              variant='destructive'
              className='gap-1 font-mono text-xs'
            >
              <AlertCircle className='size-3' />
              {failedCount} failed
            </Badge>
          )}
        </div>
        <div className='ml-auto flex items-center gap-2'>
          <Button
            variant='outline'
            size='sm'
            className='h-7 gap-1.5 text-xs'
            onClick={() => setShowUdfStatus(!showUdfStatus)}
          >
            {showUdfStatus ? 'Hide Details' : 'Show Details'}
          </Button>
          <Button
            variant='outline'
            size='sm'
            className='h-7 gap-1.5 text-xs'
            onClick={handleRegisterUDFs}
            disabled={registering}
          >
            {registering ? (
              <Loader2 className='size-3 animate-spin' />
            ) : (
              <RefreshCw className='size-3' />
            )}
            Register All UDFs
          </Button>
        </div>
      </div>

      {/* UDF Status Details (expandable) */}
      {showUdfStatus && udfStatuses.length > 0 && (
        <div className='rounded-lg border bg-card'>
          <div className='border-b px-4 py-2'>
            <span className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
              StarRocks UDF Status
            </span>
          </div>
          <div className='divide-y'>
            {udfStatuses.map((status) => (
              <div
                key={status.function_name}
                className='flex items-center gap-3 px-4 py-2.5 text-sm'
              >
                {status.registered ? (
                  <CheckCircle2 className='size-3.5 shrink-0 text-emerald-500' />
                ) : (
                  <XCircle className='size-3.5 shrink-0 text-destructive' />
                )}
                <span className='font-mono text-xs font-medium'>
                  {status.function_name}
                </span>
                <Badge
                  variant='outline'
                  className={cn(
                    'text-[10px] font-normal',
                    FUNCTION_TYPE_COLORS[status.function_type]
                  )}
                >
                  {getFunctionTypeLabel(status.function_type)}
                </Badge>
                {status.provider_name && (
                  <span className='text-xs text-muted-foreground'>
                    {status.provider_name}
                    {status.model_name ? ` → ${status.model_name}` : ''}
                  </span>
                )}
                {!status.registered && status.error && (
                  <span className='ml-auto truncate text-xs text-destructive'>
                    {status.error}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filters */}
      <SimpleTableToolbar
        search={search}
        onSearchChange={(value) => {
          setSearch(value)
        }}
        searchPlaceholder='Search functions, aliases, providers...'
        resultLabel={`${filtered.length} alias${filtered.length !== 1 ? 'es' : ''}`}
        filters={[
          {
            label: 'Function Type',
            value:
              typeFilter &&
              FUNCTION_TYPES.find((ft) => ft.value === typeFilter)?.label
                ? FUNCTION_TYPES.find((ft) => ft.value === typeFilter)!.label
                : '',
            options: typeFilterOptions,
            onChange: (label) => {
              const ft = FUNCTION_TYPES.find((f) => f.label === label)
              setTypeFilter(ft?.value ?? '')
            },
            icon: <Wand2 size={14} />,
          },
        ]}
      />

      {/* Toolbar action */}
      <div className='flex justify-end'>
        <Button size='sm' className='gap-1.5' onClick={openCreateAliasDialog}>
          <Plus className='size-3.5' />
          Add Function Alias
        </Button>
      </div>

      {/* Table */}
      <SimpleTableViewport>
        <table className='w-full text-sm'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                SQL Function
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Type
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Alias Name
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Provider
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Model
              </th>
              <th className='px-4 py-3 text-center text-xs font-medium text-muted-foreground'>
                Default
              </th>
              <th className='px-4 py-3 text-center text-xs font-medium text-muted-foreground'>
                UDF Status
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={8} className='px-4 py-12 text-center'>
                  <Loader2 className='mx-auto size-5 animate-spin text-muted-foreground' />
                </td>
              </tr>
            )}

            {!loading && filtered.length === 0 && (
              <tr>
                <td colSpan={8} className='px-4 py-12 text-center'>
                  <Wand2 className='mx-auto mb-3 size-8 text-muted-foreground' />
                  <p className='text-sm text-muted-foreground'>
                    No function aliases configured yet.
                  </p>
                  <p className='mt-1 text-xs text-muted-foreground'>
                    Create aliases to map SQL functions like AI_COMPLETE,
                    AI_SENTIMENT to specific LLM providers and models.
                  </p>
                  <Button
                    variant='outline'
                    size='sm'
                    className='mt-3 gap-1.5'
                    onClick={openCreateAliasDialog}
                  >
                    <Plus className='size-3.5' />
                    Create your first alias
                  </Button>
                </td>
              </tr>
            )}

            {!loading &&
              filtered.map((alias) => {
                const udfStatus = udfStatusMap.get(alias.function_type)
                const isRegistered = udfStatus?.registered ?? false
                return (
                  <tr
                    key={alias.id}
                    className={cn(
                      'border-b border-border transition-colors hover:bg-muted/50',
                      !alias.is_active && 'opacity-50'
                    )}
                  >
                    <td className='px-4 py-3'>
                      <span className='font-mono text-xs font-semibold'>
                        {getUDFName(alias.function_type)}
                      </span>
                    </td>
                    <td className='px-4 py-3'>
                      <Badge
                        variant='outline'
                        className={cn(
                          'text-[10px] font-normal',
                          FUNCTION_TYPE_COLORS[alias.function_type]
                        )}
                      >
                        {getFunctionTypeLabel(alias.function_type)}
                      </Badge>
                    </td>
                    <td className='px-4 py-3'>
                      <span className='font-medium'>{alias.alias_name}</span>
                    </td>
                    <td className='px-4 py-3'>
                      <span className='text-xs text-muted-foreground'>
                        {alias.provider_name ?? '—'}
                      </span>
                    </td>
                    <td className='px-4 py-3'>
                      <span className='font-mono text-xs text-muted-foreground'>
                        {alias.model_name ?? '—'}
                      </span>
                    </td>
                    <td className='px-4 py-3 text-center'>
                      {alias.is_default ? (
                        <Badge variant='default' className='text-[10px]'>
                          Default
                        </Badge>
                      ) : (
                        <span className='text-xs text-muted-foreground'>—</span>
                      )}
                    </td>
                    <td className='px-4 py-3 text-center'>
                      {alias.is_default ? (
                        isRegistered ? (
                          <Badge
                            variant='secondary'
                            className='gap-1 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                          >
                            <CheckCircle2 className='size-3' />
                            Registered
                          </Badge>
                        ) : (
                          <Badge
                            variant='secondary'
                            className='gap-1 bg-destructive/10 text-destructive'
                            title={udfStatus?.error ?? 'Not registered'}
                          >
                            <XCircle className='size-3' />
                            Failed
                          </Badge>
                        )
                      ) : (
                        <span className='text-xs text-muted-foreground'>—</span>
                      )}
                    </td>
                    <td className='px-4 py-3'>
                      <div className='flex items-center justify-end'>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant='ghost'
                              size='sm'
                              className='h-7 w-7 p-0'
                            >
                              <MoreHorizontal className='size-4' />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align='end'>
                            <DropdownMenuItem
                              onClick={() => openEditAliasDialog(alias)}
                            >
                              <Pencil className='mr-2 size-3.5' />
                              Edit Alias
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className='text-destructive focus:text-destructive'
                              onClick={() => setDeleteDialog(alias)}
                            >
                              <Trash2 className='mr-2 size-3.5' />
                              Delete Alias
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      </SimpleTableViewport>

      {/* Alias Dialog (Create + Edit) */}
      <Dialog open={aliasDialogOpen} onOpenChange={setAliasDialogOpen}>
        <DialogContent className='max-w-lg'>
          <DialogHeader>
            <DialogTitle>
              {aliasEditMode === 'edit'
                ? 'Edit Function Alias'
                : 'Create Function Alias'}
            </DialogTitle>
            <DialogDescription>
              {aliasEditMode === 'edit'
                ? 'Update the alias mapping from a SQL function to a provider and model.'
                : 'Map an LLM function type (e.g. Complete, Sentiment) to a specific provider and model. This registers a SQL UDF in StarRocks.'}
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4 py-2'>
            <div className='grid grid-cols-2 gap-4'>
              <div className='space-y-2'>
                <Label htmlFor='alias-name'>Alias Name</Label>
                <Input
                  id='alias-name'
                  placeholder='e.g. default_complete'
                  value={aliasForm.alias_name}
                  onChange={(e) =>
                    setAliasForm((f) => ({
                      ...f,
                      alias_name: e.target.value,
                    }))
                  }
                />
              </div>
              <div className='space-y-2'>
                <Label htmlFor='alias-type'>Function Type</Label>
                <Select
                  value={aliasForm.function_type}
                  onValueChange={(v) =>
                    setAliasForm((f) => ({
                      ...f,
                      function_type: v as FunctionType,
                    }))
                  }
                >
                  <SelectTrigger id='alias-type'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FUNCTION_TYPES.map((ft) => (
                      <SelectItem key={ft.value} value={ft.value}>
                        <span className='flex items-center gap-2'>
                          {ft.label}
                          <span className='font-mono text-xs text-muted-foreground'>
                            ({ft.udfName})
                          </span>
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className='grid grid-cols-2 gap-4'>
              <div className='space-y-2'>
                <Label htmlFor='alias-provider'>Provider</Label>
                <Select
                  value={aliasForm.provider_id}
                  onValueChange={(v) =>
                    setAliasForm((f) => ({
                      ...f,
                      provider_id: v,
                      model_id: '',
                    }))
                  }
                >
                  <SelectTrigger id='alias-provider'>
                    <SelectValue placeholder='Select provider' />
                  </SelectTrigger>
                  <SelectContent>
                    {providerOptions.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className='space-y-2'>
                <Label htmlFor='alias-model'>Model (optional)</Label>
                <Select
                  value={aliasForm.model_id || '_none'}
                  onValueChange={(v) =>
                    setAliasForm((f) => ({
                      ...f,
                      model_id: v === '_none' ? '' : v,
                    }))
                  }
                >
                  <SelectTrigger id='alias-model'>
                    <SelectValue placeholder='Select model' />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='_none'>None (use provider default)</SelectItem>
                    {modelOptions.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        {m.display_name ?? m.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className='space-y-2'>
              <Label htmlFor='alias-prompt'>System Prompt (optional)</Label>
              <Textarea
                id='alias-prompt'
                placeholder='Custom system prompt to override the default...'
                value={aliasForm.system_prompt}
                onChange={(e) =>
                  setAliasForm((f) => ({
                    ...f,
                    system_prompt: e.target.value,
                  }))
                }
                rows={3}
                className='text-xs'
              />
            </div>

            <div className='space-y-2'>
              <Label htmlFor='alias-params'>
                Default Params (JSON, optional)
              </Label>
              <Input
                id='alias-params'
                placeholder='{"temperature": 0.7}'
                value={aliasForm.default_params}
                onChange={(e) =>
                  setAliasForm((f) => ({
                    ...f,
                    default_params: e.target.value,
                  }))
                }
              />
            </div>

            <div className='flex items-center gap-2'>
              <input
                type='checkbox'
                id='alias-default'
                checked={aliasForm.is_default}
                onChange={(e) =>
                  setAliasForm((f) => ({
                    ...f,
                    is_default: e.target.checked,
                  }))
                }
                className='size-4 rounded border-border'
              />
              <Label htmlFor='alias-default' className='cursor-pointer text-sm'>
                Set as default alias for this function type
              </Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setAliasDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSaveAlias} disabled={submitting}>
              {submitting && (
                <Loader2 className='mr-2 size-3.5 animate-spin' />
              )}
              {aliasEditMode === 'edit' ? 'Save Changes' : 'Create Alias'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog
        open={!!deleteDialog}
        onOpenChange={(open) => !open && setDeleteDialog(null)}
      >
        <DialogContent className='max-w-md'>
          <DialogHeader>
            <DialogTitle>Confirm Deletion</DialogTitle>
            <DialogDescription>
              Delete function alias "{deleteDialog?.alias_name}"? This will
              remove the alias mapping. If it was the default alias for{' '}
              <span className='font-mono'>
                {deleteDialog && getUDFName(deleteDialog.function_type)}
              </span>
              , the UDF may need to be re-registered.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='outline' onClick={() => setDeleteDialog(null)}>
              Cancel
            </Button>
            <Button
              variant='destructive'
              onClick={handleDelete}
              disabled={submitting}
            >
              {submitting && (
                <Loader2 className='mr-2 size-3.5 animate-spin' />
              )}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
