import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bot,
  CheckCircle2,
  Cpu,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  SimpleTablePagination,
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
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'

// ── Types ──────────────────────────────────────────────────────

type ProviderType = 'openai' | 'anthropic' | 'openai_compatible'
type ModelType = 'llm' | 'embedding'

type AIProvider = {
  id: string
  name: string
  type: ProviderType
  endpoint: string
  api_key: string | null
  default_params: Record<string, unknown> | null
  is_active: boolean
  created_at: string | null
  created_by: string | null
}

type AIModel = {
  id: string
  provider_id: string
  name: string
  display_name: string | null
  type: ModelType
  max_tokens: number | null
  default_params: Record<string, unknown> | null
  is_active: boolean
  created_at: string | null
  created_by: string | null
}

type ProviderWithModels = AIProvider & {
  models: AIModel[]
  model_count: number
}

// ── Constants ───────────────────────────────────────────────────

const PROVIDER_TYPES: { value: ProviderType; label: string }[] = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai_compatible', label: 'OpenAI Compatible' },
]

const MODEL_TYPES: { value: ModelType; label: string }[] = [
  { value: 'llm', label: 'LLM' },
  { value: 'embedding', label: 'Embedding' },
]

const PROVIDER_TYPE_COLORS: Record<ProviderType, string> = {
  openai: 'text-emerald-500',
  anthropic: 'text-orange-500',
  openai_compatible: 'text-blue-500',
}

const emptyProviderForm = {
  name: '',
  type: 'openai' as ProviderType,
  endpoint: '',
  api_key: '',
  default_params: '',
}

const emptyModelForm = {
  name: '',
  display_name: '',
  type: 'llm' as ModelType,
  max_tokens: '4096',
  default_params: '',
}

// ── Component ───────────────────────────────────────────────────

export function ProvidersTab() {
  const [providers, setProviders] = useState<ProviderWithModels[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null)

  // Dialog state
  const [providerDialogOpen, setProviderDialogOpen] = useState(false)
  const [providerEditMode, setProviderEditMode] = useState<'create' | 'edit'>(
    'create'
  )
  const [editingProviderId, setEditingProviderId] = useState<string | null>(
    null
  )

  const [modelDialogOpen, setModelDialogOpen] = useState(false)
  const [modelEditMode, setModelEditMode] = useState<'create' | 'edit'>(
    'create'
  )
  const [editingModelId, setEditingModelId] = useState<string | null>(null)
  const [modelDialogProvider, setModelDialogProvider] =
    useState<ProviderWithModels | null>(null)

  const [deleteDialog, setDeleteDialog] = useState<
    | { kind: 'provider'; provider: ProviderWithModels }
    | { kind: 'model'; provider: ProviderWithModels; model: AIModel }
    | null
  >(null)

  // Form state
  const [providerForm, setProviderForm] = useState({ ...emptyProviderForm })
  const [modelForm, setModelForm] = useState({ ...emptyModelForm })
  const [submitting, setSubmitting] = useState(false)

  // Test connection state
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{
    success: boolean
    message: string
    models: string[]
  } | null>(null)

  // ── Data fetching ─────────────────────────────────────────────

  const fetchAllModels = useCallback(async () => {
    setLoading(true)
    try {
      const providerRes = await api.get<{
        providers: AIProvider[]
        count: number
      }>('/ai/providers')

      const allProviders: ProviderWithModels[] = []
      for (const p of providerRes.providers) {
        const modelRes = await api.get<{ models: AIModel[]; count: number }>(
          `/ai/providers/${p.id}/models`
        )
        allProviders.push({
          ...p,
          models: modelRes.models,
          model_count: modelRes.models.length,
        })
      }

      setProviders(allProviders)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to load AI providers'
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAllModels()
  }, [fetchAllModels])

  // ── Filtering + Pagination ────────────────────────────────────

  const filtered = useMemo(() => {
    let result = providers
    if (typeFilter) {
      const selectedType = PROVIDER_TYPES.find(
        (type) => type.label === typeFilter
      )?.value
      result = result.filter((provider) => provider.type === selectedType)
    }
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.endpoint.toLowerCase().includes(q) ||
          p.models.some((m) => m.name.toLowerCase().includes(q))
      )
    }
    return result
  }, [providers, search, typeFilter])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const currentPage = Math.min(page, totalPages)
  const paginated = filtered.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  )

  // ── Provider Actions ──────────────────────────────────────────

  const openCreateProviderDialog = () => {
    setProviderForm({ ...emptyProviderForm })
    setTestResult(null)
    setProviderEditMode('create')
    setEditingProviderId(null)
    setProviderDialogOpen(true)
  }

  const openEditProviderDialog = (provider: ProviderWithModels) => {
    setProviderForm({
      name: provider.name,
      type: provider.type,
      endpoint: provider.endpoint,
      api_key: provider.api_key ?? '',
      default_params: provider.default_params
        ? JSON.stringify(provider.default_params)
        : '',
    })
    setTestResult(null)
    setProviderEditMode('edit')
    setEditingProviderId(provider.id)
    setProviderDialogOpen(true)
  }

  const handleSaveProvider = async () => {
    if (!providerForm.name.trim() || !providerForm.endpoint.trim()) {
      toast.error('Name and endpoint are required')
      return
    }
    setSubmitting(true)
    try {
      const body: Record<string, unknown> = {
        name: providerForm.name.trim(),
        type: providerForm.type,
        endpoint: providerForm.endpoint.trim(),
      }
      if (providerForm.api_key.trim()) {
        body.api_key = providerForm.api_key.trim()
      }
      if (providerForm.default_params.trim()) {
        body.default_params = JSON.parse(providerForm.default_params)
      }

      if (providerEditMode === 'edit' && editingProviderId) {
        await api.put(`/ai/providers/${editingProviderId}`, body)
        toast.success(`Provider '${providerForm.name}' updated`)
      } else {
        await api.post('/ai/providers', body)
        toast.success(`Provider '${providerForm.name}' created`)
      }

      setProviderDialogOpen(false)
      await fetchAllModels()
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to save provider'
      )
    } finally {
      setSubmitting(false)
    }
  }

  // ── Model Actions ─────────────────────────────────────────────

  const openCreateModelDialog = (provider: ProviderWithModels) => {
    setModelDialogProvider(provider)
    setModelForm({ ...emptyModelForm })
    setModelEditMode('create')
    setEditingModelId(null)
    setModelDialogOpen(true)
  }

  const openEditModelDialog = (
    provider: ProviderWithModels,
    model: AIModel
  ) => {
    setModelDialogProvider(provider)
    setModelForm({
      name: model.name,
      display_name: model.display_name ?? '',
      type: model.type,
      max_tokens: model.max_tokens ? String(model.max_tokens) : '',
      default_params: model.default_params
        ? JSON.stringify(model.default_params)
        : '',
    })
    setModelEditMode('edit')
    setEditingModelId(model.id)
    setModelDialogOpen(true)
  }

  const handleSaveModel = async () => {
    if (!modelDialogProvider) return
    if (!modelForm.name.trim()) {
      toast.error('Model name is required')
      return
    }
    setSubmitting(true)
    try {
      const body: Record<string, unknown> = {
        name: modelForm.name.trim(),
        type: modelForm.type,
      }
      if (modelForm.display_name.trim()) {
        body.display_name = modelForm.display_name.trim()
      }
      if (modelForm.max_tokens) {
        body.max_tokens = parseInt(modelForm.max_tokens, 10)
      }
      if (modelForm.default_params.trim()) {
        body.default_params = JSON.parse(modelForm.default_params)
      }

      if (modelEditMode === 'edit' && editingModelId) {
        await api.put(`/ai/models/${editingModelId}`, body)
        toast.success(`Model '${modelForm.name}' updated`)
      } else {
        body.provider_id = modelDialogProvider.id
        await api.post(`/ai/providers/${modelDialogProvider.id}/models`, body)
        toast.success(`Model '${modelForm.name}' created`)
      }

      setModelDialogOpen(false)
      setModelDialogProvider(null)
      await fetchAllModels()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save model')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Test Connection ───────────────────────────────────────────

  const handleTestConnection = async () => {
    if (!providerForm.endpoint.trim()) {
      toast.error('Endpoint is required to test connection')
      return
    }
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.post<{
        success: boolean
        message: string
        models: string[]
      }>('/ai/test-connection', {
        type: providerForm.type,
        endpoint: providerForm.endpoint.trim(),
        api_key: providerForm.api_key.trim() || null,
      })
      setTestResult(result)
      if (result.success) {
        toast.success(result.message)
      } else {
        toast.error(result.message)
      }
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : 'Failed to test connection'
      setTestResult({ success: false, message: msg, models: [] })
      toast.error(msg)
    } finally {
      setTesting(false)
    }
  }

  // ── Delete ────────────────────────────────────────────────────

  const handleDelete = async () => {
    if (!deleteDialog) return
    setSubmitting(true)
    try {
      if (deleteDialog.kind === 'provider') {
        await api.delete(`/ai/providers/${deleteDialog.provider.id}`)
        toast.success(`Provider '${deleteDialog.provider.name}' deleted`)
      } else {
        await api.delete(`/ai/models/${deleteDialog.model.id}`)
        toast.success(`Model '${deleteDialog.model.name}' deleted`)
      }
      setDeleteDialog(null)
      await fetchAllModels()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Render ────────────────────────────────────────────────────

  const typeOptions = useMemo(() => PROVIDER_TYPES.map((t) => t.label), [])

  return (
    <div className='space-y-4'>
      {/* Filters */}
      <SimpleTableToolbar
        search={search}
        onSearchChange={(value) => {
          setSearch(value)
          setPage(1)
        }}
        searchPlaceholder='Search providers or models...'
        resultLabel={`${filtered.length} provider${filtered.length !== 1 ? 's' : ''}`}
        filters={[
          {
            label: 'Type',
            value: typeFilter,
            options: typeOptions,
            onChange: (value) => {
              setTypeFilter(value)
              setPage(1)
            },
            icon: <Cpu size={14} />,
          },
        ]}
        actions={
          <Button size='sm' className='gap-1.5' onClick={openCreateProviderDialog}>
            <Plus className='size-3.5' />
            Add Provider
          </Button>
        }
      />

      {/* Table */}
      <SimpleTableViewport>
        <table className='w-full text-sm'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Provider
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Type
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Endpoint
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                API Key
              </th>
              <th className='px-4 py-3 text-center text-xs font-medium text-muted-foreground'>
                Models
              </th>
              <th className='px-4 py-3 text-center text-xs font-medium text-muted-foreground'>
                Status
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={7} className='px-4 py-12 text-center'>
                  <Loader2 className='mx-auto size-5 animate-spin text-muted-foreground' />
                </td>
              </tr>
            )}

            {!loading && paginated.length === 0 && (
              <tr>
                <td colSpan={7} className='px-4 py-12 text-center'>
                  <Bot className='mx-auto mb-3 size-8 text-muted-foreground' />
                  <p className='text-sm text-muted-foreground'>
                    No AI providers registered yet.
                  </p>
                  <Button
                    variant='outline'
                    size='sm'
                    className='mt-3 gap-1.5'
                    onClick={openCreateProviderDialog}
                  >
                    <Plus className='size-3.5' />
                    Add your first provider
                  </Button>
                </td>
              </tr>
            )}

            {!loading &&
              paginated.map((provider) => (
                <Fragment key={provider.id}>
                  <tr
                    className='cursor-pointer border-b border-border transition-colors hover:bg-muted/50'
                    onClick={() =>
                      setExpandedProvider(
                        expandedProvider === provider.id ? null : provider.id
                      )
                    }
                  >
                    <td className='px-4 py-3'>
                      <div className='flex items-center gap-2'>
                        <Bot
                          className={cn(
                            'size-4 transition-transform',
                            PROVIDER_TYPE_COLORS[provider.type],
                            expandedProvider === provider.id && 'rotate-90'
                          )}
                        />
                        <span className='font-medium'>{provider.name}</span>
                      </div>
                    </td>
                    <td className='px-4 py-3'>
                      <Badge variant='secondary' className='font-normal'>
                        {PROVIDER_TYPES.find((t) => t.value === provider.type)
                          ?.label ?? provider.type}
                      </Badge>
                    </td>
                    <td className='px-4 py-3'>
                      <span className='font-mono text-xs text-muted-foreground'>
                        {provider.endpoint}
                      </span>
                    </td>
                    <td className='px-4 py-3'>
                      {provider.api_key ? (
                        <span className='font-mono text-xs text-muted-foreground'>
                          ••••{provider.api_key.slice(-4)}
                        </span>
                      ) : (
                        <span className='text-xs text-muted-foreground'>
                          —
                        </span>
                      )}
                    </td>
                    <td className='px-4 py-3 text-center'>
                      <span className='font-medium'>
                        {provider.model_count}
                      </span>
                    </td>
                    <td className='px-4 py-3 text-center'>
                      <Badge
                        variant={provider.is_active ? 'default' : 'secondary'}
                        className='font-normal'
                      >
                        {provider.is_active ? 'Active' : 'Inactive'}
                      </Badge>
                    </td>
                    <td className='px-4 py-3'>
                      <div
                        className='flex items-center justify-end'
                        onClick={(e) => e.stopPropagation()}
                      >
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
                              onClick={() => openEditProviderDialog(provider)}
                            >
                              <Pencil className='mr-2 size-3.5' />
                              Edit Provider
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={() => openCreateModelDialog(provider)}
                            >
                              <Plus className='mr-2 size-3.5' />
                              Add Model
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className='text-destructive focus:text-destructive'
                              onClick={() =>
                                setDeleteDialog({
                                  kind: 'provider',
                                  provider,
                                })
                              }
                            >
                              <Trash2 className='mr-2 size-3.5' />
                              Delete Provider
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>

                  {/* Expanded models */}
                  {expandedProvider === provider.id && (
                    <tr key={`${provider.id}-models`} className='bg-muted/30'>
                      <td colSpan={7} className='px-4 py-3'>
                        <div className='ml-6'>
                          <div className='mb-2 flex items-center gap-2'>
                            <Cpu className='size-3.5 text-muted-foreground' />
                            <span className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
                              Models ({provider.models.length})
                            </span>
                          </div>
                          {provider.models.length === 0 ? (
                            <p className='py-2 text-xs text-muted-foreground'>
                              No models registered.
                            </p>
                          ) : (
                            <table className='w-full text-sm'>
                              <thead>
                                <tr className='border-b border-border'>
                                  <th className='py-2 pr-4 text-left text-xs font-medium text-muted-foreground'>
                                    Name
                                  </th>
                                  <th className='py-2 pr-4 text-left text-xs font-medium text-muted-foreground'>
                                    Display Name
                                  </th>
                                  <th className='py-2 pr-4 text-left text-xs font-medium text-muted-foreground'>
                                    Type
                                  </th>
                                  <th className='py-2 pr-4 text-right text-xs font-medium text-muted-foreground'>
                                    Max Tokens
                                  </th>
                                  <th className='py-2 pr-4 text-center text-xs font-medium text-muted-foreground'>
                                    Status
                                  </th>
                                  <th className='py-2 text-right text-xs font-medium text-muted-foreground'>
                                    Actions
                                  </th>
                                </tr>
                              </thead>
                              <tbody>
                                {provider.models.map((model) => (
                                  <tr
                                    key={model.id}
                                    className='border-b border-border/50 last:border-0'
                                  >
                                    <td className='py-2 pr-4 font-mono text-xs'>
                                      {model.name}
                                    </td>
                                    <td className='py-2 pr-4 text-xs text-muted-foreground'>
                                      {model.display_name ?? '—'}
                                    </td>
                                    <td className='py-2 pr-4'>
                                      <Badge
                                        variant='outline'
                                        className='font-normal'
                                      >
                                        {MODEL_TYPES.find(
                                          (t) => t.value === model.type
                                        )?.label ?? model.type}
                                      </Badge>
                                    </td>
                                    <td className='py-2 pr-4 text-right font-mono text-xs'>
                                      {model.max_tokens?.toLocaleString() ??
                                        '—'}
                                    </td>
                                    <td className='py-2 pr-4 text-center'>
                                      <Badge
                                        variant={
                                          model.is_active
                                            ? 'default'
                                            : 'secondary'
                                        }
                                        className='font-normal'
                                      >
                                        {model.is_active
                                          ? 'Active'
                                          : 'Inactive'}
                                      </Badge>
                                    </td>
                                    <td className='py-2 text-right'>
                                      <DropdownMenu>
                                        <DropdownMenuTrigger asChild>
                                          <Button
                                            variant='ghost'
                                            size='sm'
                                            className='h-6 w-6 p-0'
                                          >
                                            <MoreHorizontal className='size-3.5' />
                                          </Button>
                                        </DropdownMenuTrigger>
                                        <DropdownMenuContent align='end'>
                                          <DropdownMenuItem
                                            onClick={() =>
                                              openEditModelDialog(
                                                provider,
                                                model
                                              )
                                            }
                                          >
                                            <Pencil className='mr-2 size-3' />
                                            Edit Model
                                          </DropdownMenuItem>
                                          <DropdownMenuItem
                                            className='text-destructive focus:text-destructive'
                                            onClick={() =>
                                              setDeleteDialog({
                                                kind: 'model',
                                                provider,
                                                model,
                                              })
                                            }
                                          >
                                            <Trash2 className='mr-2 size-3' />
                                            Delete Model
                                          </DropdownMenuItem>
                                        </DropdownMenuContent>
                                      </DropdownMenu>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
          </tbody>
        </table>
      </SimpleTableViewport>

      {/* Pagination */}
      <SimpleTablePagination
        page={currentPage}
        pageSize={pageSize}
        total={filtered.length}
        onPageChange={setPage}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
        }}
      />

      {/* Provider Dialog (Create + Edit) */}
      <Dialog open={providerDialogOpen} onOpenChange={setProviderDialogOpen}>
        <DialogContent className='max-w-lg'>
          <DialogHeader>
            <DialogTitle>
              {providerEditMode === 'edit'
                ? 'Edit Provider'
                : 'Add AI Provider'}
            </DialogTitle>
            <DialogDescription>
              {providerEditMode === 'edit'
                ? 'Update provider connection settings.'
                : 'Register a new LLM provider connection. API key should be set as an environment variable in the backend .env file.'}
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4 py-2'>
            <div className='space-y-2'>
              <Label htmlFor='provider-name'>Name</Label>
              <Input
                id='provider-name'
                placeholder='e.g. OpenAI, My vLLM'
                value={providerForm.name}
                onChange={(e) =>
                  setProviderForm((f) => ({ ...f, name: e.target.value }))
                }
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='provider-type'>Type</Label>
              <Select
                value={providerForm.type}
                onValueChange={(v) =>
                  setProviderForm((f) => ({
                    ...f,
                    type: v as ProviderType,
                  }))
                }
              >
                <SelectTrigger id='provider-type'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROVIDER_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='provider-endpoint'>Endpoint</Label>
              <Input
                id='provider-endpoint'
                placeholder='https://api.openai.com/v1'
                value={providerForm.endpoint}
                onChange={(e) =>
                  setProviderForm((f) => ({
                    ...f,
                    endpoint: e.target.value,
                  }))
                }
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='provider-apikey'>API Key</Label>
              <Input
                id='provider-apikey'
                type='password'
                placeholder='sk-...'
                value={providerForm.api_key}
                onChange={(e) =>
                  setProviderForm((f) => ({
                    ...f,
                    api_key: e.target.value,
                  }))
                }
              />
              <p className='text-xs text-muted-foreground'>
                The API key is stored in the Nova system database and used for
                LLM API calls.
              </p>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='provider-params'>
                Default Params (JSON, optional)
              </Label>
              <Input
                id='provider-params'
                placeholder='{"temperature": 0.7}'
                value={providerForm.default_params}
                onChange={(e) =>
                  setProviderForm((f) => ({
                    ...f,
                    default_params: e.target.value,
                  }))
                }
              />
            </div>
          </div>

          {/* Test Connection */}
          <div className='space-y-2'>
            <Button
              variant='outline'
              size='sm'
              className='gap-1.5'
              onClick={handleTestConnection}
              disabled={testing || !providerForm.endpoint.trim()}
            >
              {testing ? (
                <Loader2 className='size-3.5 animate-spin' />
              ) : (
                <Plug className='size-3.5' />
              )}
              {testing ? 'Testing...' : 'Test Connection'}
            </Button>

            {testResult && (
              <div
                className={cn(
                  'flex items-start gap-2 rounded-md border px-3 py-2 text-sm',
                  testResult.success
                    ? 'border-emerald-500/30 bg-emerald-500/5'
                    : 'border-destructive/30 bg-destructive/5'
                )}
              >
                {testResult.success ? (
                  <CheckCircle2 className='mt-0.5 size-4 shrink-0 text-emerald-500' />
                ) : (
                  <XCircle className='mt-0.5 size-4 shrink-0 text-destructive' />
                )}
                <div className='min-w-0'>
                  <p
                    className={cn(
                      'font-medium',
                      testResult.success
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : 'text-destructive'
                    )}
                  >
                    {testResult.message}
                  </p>
                  {testResult.success && testResult.models.length > 0 && (
                    <div className='mt-1.5 flex flex-wrap gap-1'>
                      {testResult.models.slice(0, 15).map((m) => (
                        <Badge
                          key={m}
                          variant='secondary'
                          className='font-mono text-[10px] font-normal'
                        >
                          {m}
                        </Badge>
                      ))}
                      {testResult.models.length > 15 && (
                        <span className='text-xs text-muted-foreground'>
                          +{testResult.models.length - 15} more
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => setProviderDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button onClick={handleSaveProvider} disabled={submitting}>
              {submitting && (
                <Loader2 className='mr-2 size-3.5 animate-spin' />
              )}
              {providerEditMode === 'edit'
                ? 'Save Changes'
                : 'Create Provider'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Model Dialog (Create + Edit) */}
      <Dialog open={modelDialogOpen} onOpenChange={setModelDialogOpen}>
        <DialogContent className='max-w-lg'>
          <DialogHeader>
            <DialogTitle>
              {modelEditMode === 'edit' ? 'Edit Model' : 'Add Model'}
              {modelDialogProvider && (
                <span className='ml-1.5 text-muted-foreground'>
                  {modelEditMode === 'edit'
                    ? `· ${modelDialogProvider.name}`
                    : `to ${modelDialogProvider.name}`}
                </span>
              )}
            </DialogTitle>
            <DialogDescription>
              {modelEditMode === 'edit'
                ? 'Update model configuration.'
                : 'Register a model under this provider. Models can be LLMs or embedding models.'}
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4 py-2'>
            <div className='space-y-2'>
              <Label htmlFor='model-name'>Model Name</Label>
              <Input
                id='model-name'
                placeholder='e.g. gpt-4o, claude-sonnet-4'
                value={modelForm.name}
                onChange={(e) =>
                  setModelForm((f) => ({ ...f, name: e.target.value }))
                }
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='model-display'>Display Name (optional)</Label>
              <Input
                id='model-display'
                placeholder='e.g. GPT-4o (128K context)'
                value={modelForm.display_name}
                onChange={(e) =>
                  setModelForm((f) => ({
                    ...f,
                    display_name: e.target.value,
                  }))
                }
              />
            </div>
            <div className='grid grid-cols-2 gap-4'>
              <div className='space-y-2'>
                <Label htmlFor='model-type'>Type</Label>
                <Select
                  value={modelForm.type}
                  onValueChange={(v) =>
                    setModelForm((f) => ({ ...f, type: v as ModelType }))
                  }
                >
                  <SelectTrigger id='model-type'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MODEL_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className='space-y-2'>
                <Label htmlFor='model-tokens'>Max Tokens</Label>
                <Input
                  id='model-tokens'
                  type='number'
                  placeholder='4096'
                  value={modelForm.max_tokens}
                  onChange={(e) =>
                    setModelForm((f) => ({
                      ...f,
                      max_tokens: e.target.value,
                    }))
                  }
                />
              </div>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='model-params'>
                Default Params (JSON, optional)
              </Label>
              <Input
                id='model-params'
                placeholder='{"temperature": 0.7}'
                value={modelForm.default_params}
                onChange={(e) =>
                  setModelForm((f) => ({
                    ...f,
                    default_params: e.target.value,
                  }))
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => setModelDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button onClick={handleSaveModel} disabled={submitting}>
              {submitting && (
                <Loader2 className='mr-2 size-3.5 animate-spin' />
              )}
              {modelEditMode === 'edit' ? 'Save Changes' : 'Create Model'}
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
              {deleteDialog?.kind === 'provider'
                ? `Delete provider "${deleteDialog.provider.name}"? This will also delete all ${deleteDialog.provider.model_count} model(s) under it.`
                : `Delete model "${deleteDialog?.model.name}" from "${deleteDialog?.provider.name}"?`}
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
