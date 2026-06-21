import { useCallback, useEffect, useState } from 'react'
import { Loader2, MoreHorizontal, Plus, Tags, Trash2 } from 'lucide-react'
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
import { api } from '@/lib/api-client'

type Alias = {
  alias_name: string
  model_id: string
  model_name: string | null
  version: number
  created_at: string | null
}

type ModelInfo = {
  model_id: string
  model_name: string
  latest_version: number | null
}

export function AliasesTab() {
  const [aliases, setAliases] = useState<Alias[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')

  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteDialog, setDeleteDialog] = useState<Alias | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const [form, setForm] = useState({
    alias_name: '',
    model_id: '',
    version: '1',
  })

  const fetchAliases = useCallback(async () => {
    try {
      const res = await api.get<{ aliases: Alias[]; count: number }>(
        '/ml/aliases'
      )
      setAliases(res.aliases)
    } catch {
      toast.error('Failed to load aliases')
    }
  }, [])

  const fetchModels = useCallback(async () => {
    try {
      const res = await api.get<{ models: ModelInfo[]; count: number }>(
        '/ml/models'
      )
      setModels(res.models)
    } catch {
      // silent
    }
  }, [])

  useEffect(() => {
    Promise.all([fetchAliases(), fetchModels()]).finally(() =>
      setLoading(false)
    )
  }, [fetchAliases, fetchModels])

  const filtered = aliases.filter(
    (a) =>
      a.alias_name.toLowerCase().includes(search.toLowerCase()) ||
      (a.model_name ?? '').toLowerCase().includes(search.toLowerCase())
  )

  const openCreateDialog = () => {
    setForm({ alias_name: '', model_id: '', version: '1' })
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    if (!form.alias_name.trim()) {
      toast.error('Alias name is required')
      return
    }
    if (!form.model_id) {
      toast.error('Please select a model')
      return
    }
    setSubmitting(true)
    try {
      await api.post('/ml/aliases', {
        alias_name: form.alias_name.trim(),
        model_id: form.model_id,
        version: parseInt(form.version, 10),
      })
      toast.success('Alias saved successfully')
      setDialogOpen(false)
      fetchAliases()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save alias')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteDialog) return
    setSubmitting(true)
    try {
      await api.delete(`/ml/aliases/${deleteDialog.alias_name}`)
      toast.success('Alias deleted')
      setDeleteDialog(null)
      fetchAliases()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete alias')
    } finally {
      setSubmitting(false)
    }
  }

  // Get available versions for selected model
  const selectedModel = models.find((m) => m.model_id === form.model_id)
  const availableVersions = selectedModel?.latest_version
    ? Array.from({ length: selectedModel.latest_version }, (_, i) => i + 1)
    : [1]

  if (loading) {
    return (
      <div className='flex items-center justify-center py-12'>
        <Loader2 className='size-5 animate-spin text-muted-foreground' />
      </div>
    )
  }

  return (
    <div className='space-y-4'>
      <SimpleTableToolbar>
        <Input
          placeholder='Search aliases...'
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className='h-8 max-w-xs'
        />
        <div className='ml-auto'>
          <Button size='sm' onClick={openCreateDialog}>
            <Plus className='mr-1.5 size-3.5' />
            Add Alias
          </Button>
        </div>
      </SimpleTableToolbar>

      {filtered.length === 0 ? (
        <div className='rounded-lg bg-card p-8 text-center'>
          <Tags className='mx-auto mb-3 size-8 text-muted-foreground' />
          <p className='text-sm text-muted-foreground'>
            {search
              ? 'No aliases match your search.'
              : 'No model aliases yet. Create one to use for predictions.'}
          </p>
        </div>
      ) : (
        <SimpleTableViewport>
          <table>
            <thead>
              <tr>
                <th className='text-left'>Alias Name</th>
                <th className='text-left'>Model</th>
                <th className='text-left'>Version</th>
                <th className='text-left'>Created</th>
                <th className='w-10' />
              </tr>
            </thead>
            <tbody>
              {filtered.map((alias) => (
                <tr key={alias.alias_name}>
                  <td className='font-medium'>{alias.alias_name}</td>
                  <td className='text-muted-foreground'>
                    {alias.model_name ?? '—'}
                  </td>
                  <td>
                    <Badge variant='secondary'>v{alias.version}</Badge>
                  </td>
                  <td className='text-xs text-muted-foreground'>
                    {alias.created_at ?? '—'}
                  </td>
                  <td>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant='ghost' size='icon' className='size-7'>
                          <MoreHorizontal className='size-3.5' />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align='end'>
                        <DropdownMenuItem
                          className='text-destructive'
                          onClick={() => setDeleteDialog(alias)}
                        >
                          <Trash2 className='mr-2 size-3.5' />
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </SimpleTableViewport>
      )}

      {/* Create Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className='max-w-md'>
          <DialogHeader>
            <DialogTitle>Create Model Alias</DialogTitle>
            <DialogDescription>
              Create an alias to reference a specific model version for
              predictions.
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4 py-2'>
            <div>
              <Label className='mb-1.5 block text-xs'>Alias Name</Label>
              <Input
                placeholder='e.g. status_predictor'
                value={form.alias_name}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, alias_name: e.target.value }))
                }
              />
            </div>
            <div>
              <Label className='mb-1.5 block text-xs'>Model</Label>
              <Select
                value={form.model_id}
                onValueChange={(v) =>
                  setForm((prev) => ({ ...prev, model_id: v, version: '1' }))
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder='Select model' />
                </SelectTrigger>
                <SelectContent>
                  {models.map((m) => (
                    <SelectItem key={m.model_id} value={m.model_id}>
                      {m.model_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className='mb-1.5 block text-xs'>Version</Label>
              <Select
                value={form.version}
                onValueChange={(v) =>
                  setForm((prev) => ({ ...prev, version: v }))
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder='Select version' />
                </SelectTrigger>
                <SelectContent>
                  {availableVersions.map((v) => (
                    <SelectItem key={v} value={String(v)}>
                      Version {v}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={submitting}>
              {submitting && (
                <Loader2 className='mr-2 size-3.5 animate-spin' />
              )}
              Save Alias
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Dialog */}
      <Dialog
        open={!!deleteDialog}
        onOpenChange={(open) => !open && setDeleteDialog(null)}
      >
        <DialogContent className='max-w-md'>
          <DialogHeader>
            <DialogTitle>Confirm Deletion</DialogTitle>
            <DialogDescription>
              Delete alias &ldquo;{deleteDialog?.alias_name}&rdquo;? The model
              itself will not be affected.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => setDeleteDialog(null)}
            >
              Cancel
            </Button>
            <Button variant='destructive' onClick={handleDelete} disabled={submitting}>
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
