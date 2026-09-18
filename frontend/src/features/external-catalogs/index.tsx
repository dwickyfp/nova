import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ExternalLink,
  Loader2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { Header } from '@/components/layout/header'
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
import {
  createExternalCatalog,
  deleteExternalCatalog,
  fetchExternalCatalogs,
  type CatalogType,
  type ExternalCatalog,
  type MetastoreType,
} from './api'

const CATALOG_TYPES: { value: CatalogType; label: string }[] = [
  { value: 'iceberg', label: 'Iceberg' },
  { value: 'hive', label: 'Hive' },
]

const METASTORE_TYPES: { value: MetastoreType; label: string }[] = [
  { value: 'hms', label: 'Hive Metastore' },
  { value: 'rest', label: 'Iceberg REST Catalog' },
]

const emptyForm = {
  name: '',
  type: 'iceberg' as CatalogType,
  metastore_type: 'hms' as MetastoreType,
  metastore_uri: '',
  storage_connection: 'production',
  comment: '',
}

export function ExternalCatalogsPage() {
  const [catalogs, setCatalogs] = useState<ExternalCatalog[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState({ ...emptyForm })
  const [submitting, setSubmitting] = useState(false)
  const [dropTarget, setDropTarget] = useState<ExternalCatalog | null>(null)
  const [dropping, setDropping] = useState(false)
  const [selected, setSelected] = useState<ExternalCatalog | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetchExternalCatalogs()
      setCatalogs(res.catalogs)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to load external catalogs'
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const createDisabled = useMemo(
    () => !form.name.trim() || !form.metastore_uri.trim(),
    [form.name, form.metastore_uri]
  )

  const handleCreate = async () => {
    if (createDisabled) {
      toast.error('Name and metastore URI are required')
      return
    }
    setSubmitting(true)
    try {
      await createExternalCatalog({
        name: form.name.trim(),
        type: form.type,
        metastore_type: form.metastore_type,
        metastore_uri: form.metastore_uri.trim(),
        storage_connection: form.storage_connection.trim() || 'production',
        comment: form.comment.trim() || undefined,
      })
      toast.success(`Catalog "${form.name}" created`)
      setDialogOpen(false)
      setForm({ ...emptyForm })
      await load()
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to create catalog'
      )
    } finally {
      setSubmitting(false)
    }
  }

  const handleDrop = async () => {
    if (!dropTarget) return
    setDropping(true)
    try {
      await deleteExternalCatalog(dropTarget.name)
      toast.success(`Catalog "${dropTarget.name}" dropped`)
      setDropTarget(null)
      if (selected?.name === dropTarget.name) setSelected(null)
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to drop catalog')
    } finally {
      setDropping(false)
    }
  }

  return (
    <div className='flex h-full min-h-0 flex-col'>
      <Header fixed>
        <div className='flex min-w-0 flex-1 items-center gap-3'>
          <div className='min-w-0'>
            <h1 className='truncate text-lg font-semibold'>External Catalogs</h1>
            <p className='text-sm text-muted-foreground'>
              Iceberg and Hive catalogs backed by a storage connection
            </p>
          </div>
        </div>
        <div className='ml-auto flex items-center gap-2'>
          <Button
            variant='outline'
            size='icon'
            className='h-8 w-8'
            onClick={load}
            disabled={loading}
            aria-label='Refresh'
          >
            <RefreshCw className={loading ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
          </Button>
          <Button
            size='sm'
            className='h-8'
            onClick={() => {
              setForm({ ...emptyForm })
              setDialogOpen(true)
            }}
          >
            <Plus className='mr-1.5 h-3.5 w-3.5' />
            Add Catalog
          </Button>
        </div>
      </Header>

      <div className='min-h-0 flex-1 overflow-auto p-4'>
        <div className='rounded-lg border border-border bg-background'>
          <table className='w-full text-sm'>
            <thead className='bg-muted'>
              <tr className='text-left text-xs text-muted-foreground'>
                <th className='px-4 py-2 font-medium'>Name</th>
                <th className='px-4 py-2 font-medium'>Type</th>
                <th className='px-4 py-2 font-medium'>Metastore</th>
                <th className='px-4 py-2 font-medium'>Storage</th>
                <th className='w-10 px-4 py-2' />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} className='px-4 py-10 text-center'>
                    <Loader2 className='mx-auto h-4 w-4 animate-spin text-muted-foreground' />
                  </td>
                </tr>
              ) : catalogs.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className='px-4 py-10 text-center text-sm text-muted-foreground'
                  >
                    No external catalogs yet.
                  </td>
                </tr>
              ) : (
                catalogs.map((catalog) => (
                  <tr
                    key={catalog.name}
                    className='cursor-pointer border-t border-border hover:bg-muted/50'
                    onClick={() => setSelected(catalog)}
                  >
                    <td className='px-4 py-2 font-medium'>{catalog.name}</td>
                    <td className='px-4 py-2'>
                      <Badge variant='secondary' className='capitalize'>
                        {catalog.type || 'unknown'}
                      </Badge>
                    </td>
                    <td className='px-4 py-2 text-muted-foreground'>
                      {catalog.metastore_uri || '—'}
                    </td>
                    <td className='px-4 py-2 text-muted-foreground'>
                      {catalog.storage_connection || '—'}
                    </td>
                    <td className='px-4 py-2 text-right'>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant='ghost'
                            size='icon'
                            className='h-7 w-7'
                            onClick={(e) => e.stopPropagation()}
                          >
                            <MoreHorizontal className='h-3.5 w-3.5' />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align='end'>
                          <DropdownMenuItem
                            onClick={(e) => {
                              e.stopPropagation()
                              setDropTarget(catalog)
                            }}
                          >
                            <Trash2 className='mr-2 h-3.5 w-3.5' />
                            Drop
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {selected && (
          <div className='mt-4 rounded-lg border border-border bg-background p-4'>
            <div className='mb-2 flex items-center gap-2 text-sm font-medium'>
              <ExternalLink className='h-3.5 w-3.5' />
              {selected.name}
            </div>
            <p className='mb-2 text-xs text-muted-foreground'>
              Credentials are never returned here. The statement below is the
              engine&apos;s DDL with every secret value redacted.
            </p>
            <pre className='max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs'>
              {selected.create_statement || 'No DDL available'}
            </pre>
          </div>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className='sm:max-w-lg'>
          <DialogHeader>
            <DialogTitle>Create External Catalog</DialogTitle>
            <DialogDescription>
              Storage credentials are resolved from the named connection and are
              never sent from this form.
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-3 py-2'>
            <div className='space-y-1.5'>
              <Label htmlFor='catalog-name'>Name</Label>
              <Input
                id='catalog-name'
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder='iceberg_lake'
              />
            </div>
            <div className='grid grid-cols-2 gap-3'>
              <div className='space-y-1.5'>
                <Label>Type</Label>
                <Select
                  value={form.type}
                  onValueChange={(value) =>
                    setForm({ ...form, type: value as CatalogType })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATALOG_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className='space-y-1.5'>
                <Label>Metastore</Label>
                <Select
                  value={form.metastore_type}
                  onValueChange={(value) =>
                    setForm({
                      ...form,
                      metastore_type: value as MetastoreType,
                    })
                  }
                  disabled={form.type === 'hive'}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {METASTORE_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='catalog-uri'>
                {form.type === 'hive' || form.metastore_type === 'hms'
                  ? 'Hive Metastore URI'
                  : 'Iceberg REST URI'}
              </Label>
              <Input
                id='catalog-uri'
                value={form.metastore_uri}
                onChange={(e) =>
                  setForm({ ...form, metastore_uri: e.target.value })
                }
                placeholder='thrift://hms:9083'
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='catalog-storage'>Storage connection</Label>
              <Input
                id='catalog-storage'
                value={form.storage_connection}
                onChange={(e) =>
                  setForm({ ...form, storage_connection: e.target.value })
                }
                placeholder='production'
              />
              <p className='text-xs text-muted-foreground'>
                A name from the Nova storage configuration. The secret stays on
                the server.
              </p>
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='catalog-comment'>Comment</Label>
              <Input
                id='catalog-comment'
                value={form.comment}
                onChange={(e) => setForm({ ...form, comment: e.target.value })}
                placeholder='Optional'
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={submitting || createDisabled}>
              {submitting ? (
                <>
                  <Loader2 className='mr-2 h-4 w-4 animate-spin' />
                  Creating...
                </>
              ) : (
                'Create'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={dropTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDropTarget(null)
        }}
        title='Drop external catalog'
        desc={
          <span>
            Drop <span className='font-semibold'>{dropTarget?.name}</span>? Tables
            in this catalog become unreachable through Nova. The underlying data
            is not deleted.
          </span>
        }
        destructive
        isLoading={dropping}
        confirmText='Drop'
        handleConfirm={handleDrop}
      />
    </div>
  )
}
