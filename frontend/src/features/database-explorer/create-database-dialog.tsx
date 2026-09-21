import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { ApiError, api } from '@/lib/api-client'
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

// StarRocks identifiers are ASCII letters/digits/_/$ and may not start with a
// digit. Kept in lockstep with the backend's `is_identifier` allow-list so the
// dialog refuses an invalid name before it ever reaches the API.
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_$]*$/

const emptyForm = { name: '' }

export function CreateDatabaseDialog({
  catalog,
  open,
  onOpenChange,
  onCreated,
}: {
  /** Internal catalog the database is created in, e.g. `default_catalog`. */
  catalog: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated?: (name: string) => void
}) {
  const [form, setForm] = useState({ ...emptyForm })
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) {
      setForm({ ...emptyForm })
      setSubmitting(false)
    }
  }, [open])

  const name = form.name.trim()
  const nameInvalid = name.length > 0 && !IDENTIFIER.test(name)
  const createDisabled = !name || nameInvalid || submitting

  const handleCreate = async () => {
    if (createDisabled) return
    setSubmitting(true)
    try {
      await api.post(`/explorer/catalogs/${catalog}/databases`, { name })
      toast.success(`Database "${name}" created`)
      onOpenChange(false)
      onCreated?.(name)
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : 'Failed to create database'
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-md'>
        <DialogHeader>
          <DialogTitle>Create Database</DialogTitle>
          <DialogDescription>
            Create a new database in <span className='font-semibold'>{catalog}</span>.
            In StarRocks a schema is a database, so this creates both.
          </DialogDescription>
        </DialogHeader>
        <div className='space-y-4 py-2'>
          <div className='space-y-2'>
            <Label htmlFor='database-name'>Database Name</Label>
            <Input
              id='database-name'
              placeholder='e.g. analytics, raw_data'
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreate()
              }}
              aria-invalid={nameInvalid}
              autoFocus
            />
            <p className='text-xs text-muted-foreground'>
              {nameInvalid
                ? 'Only letters, digits, underscores and $. Must not start with a digit.'
                : 'Only lowercase letters, numbers, and underscores are recommended.'}
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={createDisabled}>
            {submitting ? (
              <>
                <Loader2 className='mr-2 h-4 w-4 animate-spin' />
                Creating...
              </>
            ) : (
              'Create Database'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
