import { useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/confirm-dialog'
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

export function CreateCatalogDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated?: (catalog: ExternalCatalog) => void
}) {
  const [form, setForm] = useState({ ...emptyForm })
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) setForm({ ...emptyForm })
  }, [open])

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
      const created = await createExternalCatalog({
        name: form.name.trim(),
        type: form.type,
        metastore_type: form.metastore_type,
        metastore_uri: form.metastore_uri.trim(),
        storage_connection: form.storage_connection.trim() || 'production',
        comment: form.comment.trim() || undefined,
      })
      toast.success(`Catalog "${created.name}" created`)
      onOpenChange(false)
      onCreated?.(created)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to create catalog'
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
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
          <Button variant='outline' onClick={() => onOpenChange(false)}>
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
  )
}

export function DropCatalogDialog({
  catalog,
  onOpenChange,
  onDropped,
}: {
  catalog: ExternalCatalog | { name: string } | null
  onOpenChange: (open: boolean) => void
  onDropped?: (name: string) => void
}) {
  const [dropping, setDropping] = useState(false)

  const handleDrop = async () => {
    if (!catalog) return
    setDropping(true)
    try {
      await deleteExternalCatalog(catalog.name)
      toast.success(`Catalog "${catalog.name}" dropped`)
      onDropped?.(catalog.name)
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to drop catalog')
    } finally {
      setDropping(false)
    }
  }

  return (
    <ConfirmDialog
      open={catalog !== null}
      onOpenChange={onOpenChange}
      title='Drop external catalog'
      desc={
        <span>
          Drop <span className='font-semibold'>{catalog?.name}</span>? Tables in
          this catalog become unreachable through Nova. The underlying data is
          not deleted.
        </span>
      }
      destructive
      isLoading={dropping}
      confirmText='Drop'
      handleConfirm={handleDrop}
    />
  )
}
