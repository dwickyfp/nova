import { useEffect, useState } from 'react'
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
  CONNECTOR_TYPES,
  createConnection,
  createForeignCatalog,
  deleteConnection,
  setConnectionCredentials,
  type ConnectionSummary,
  type ConnectorType,
} from './api'

const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/

function errorMessage(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback
}

type CredentialMode = 'enter' | 'reference'

const emptyConnection = {
  name: '',
  connector_type: 'postgres' as ConnectorType,
  endpoint: '',
  mode: 'enter' as CredentialMode,
  username: '',
  password: '',
  secret_ref: '',
  comment: '',
}

/**
 * Register a source. The endpoint and credentials are entered here once and
 * never shown again: the server returns only the connection's name and type.
 */
export function AddConnectionDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated?: (connection: ConnectionSummary) => void
}) {
  const [form, setForm] = useState({ ...emptyConnection })
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) setForm({ ...emptyConnection })
  }, [open])

  const example = CONNECTOR_TYPES.find((t) => t.value === form.connector_type)?.example
  const nameValid = NAME_PATTERN.test(form.name.trim())
  const credentialsReady =
    form.mode === 'enter'
      ? form.username.trim() !== '' && form.password !== ''
      : form.secret_ref.trim() !== ''
  const ready = nameValid && form.endpoint.trim() !== '' && credentialsReady

  const handleCreate = async () => {
    if (!ready) return
    setSubmitting(true)
    try {
      const base = {
        name: form.name.trim(),
        connector_type: form.connector_type,
        endpoint: form.endpoint.trim(),
        comment: form.comment.trim() || undefined,
      }
      const created = await createConnection(
        form.mode === 'enter'
          ? {
              ...base,
              credentials: { username: form.username.trim(), password: form.password },
            }
          : { ...base, secret_ref: form.secret_ref.trim() }
      )
      toast.success(`Connection "${created.name}" added`)
      onOpenChange(false)
      onCreated?.(created)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not add the connection'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader>
          <DialogTitle>Add connection</DialogTitle>
          <DialogDescription>
            The address and credentials are stored on the server and are not
            shown again. People pick this connection by its name.
          </DialogDescription>
        </DialogHeader>
        <div className='space-y-3 py-2'>
          <div className='grid gap-3 sm:grid-cols-2'>
            <div className='space-y-1.5'>
              <Label htmlFor='connection-name'>Name</Label>
              <Input
                id='connection-name'
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder='sales_postgres'
                aria-invalid={form.name !== '' && !nameValid}
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='connection-type'>Type</Label>
              <Select
                value={form.connector_type}
                onValueChange={(value) =>
                  setForm({ ...form, connector_type: value as ConnectorType })
                }
              >
                <SelectTrigger id='connection-type'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CONNECTOR_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          {form.name !== '' && !nameValid ? (
            <p className='text-xs text-destructive'>
              Use letters, digits, and underscores, starting with a letter.
            </p>
          ) : null}
          <div className='space-y-1.5'>
            <Label htmlFor='connection-endpoint'>Address</Label>
            <Input
              id='connection-endpoint'
              value={form.endpoint}
              onChange={(e) => setForm({ ...form, endpoint: e.target.value })}
              placeholder={example}
              autoComplete='off'
            />
            <p className='text-xs text-muted-foreground'>
              Host, port, and database. Do not include a username or password.
            </p>
          </div>
          <div className='space-y-1.5'>
            <Label htmlFor='connection-credential-mode'>Credentials</Label>
            <Select
              value={form.mode}
              onValueChange={(value) => setForm({ ...form, mode: value as CredentialMode })}
            >
              <SelectTrigger id='connection-credential-mode'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='enter'>Enter a username and password</SelectItem>
                <SelectItem value='reference'>Use a secret store reference</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {form.mode === 'enter' ? (
            <div className='grid gap-3 sm:grid-cols-2'>
              <div className='space-y-1.5'>
                <Label htmlFor='connection-username'>Username</Label>
                <Input
                  id='connection-username'
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                  autoComplete='off'
                />
              </div>
              <div className='space-y-1.5'>
                <Label htmlFor='connection-password'>Password</Label>
                <Input
                  id='connection-password'
                  type='password'
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  autoComplete='new-password'
                />
              </div>
            </div>
          ) : (
            <div className='space-y-1.5'>
              <Label htmlFor='connection-secret-ref'>Secret reference</Label>
              <Input
                id='connection-secret-ref'
                value={form.secret_ref}
                onChange={(e) => setForm({ ...form, secret_ref: e.target.value })}
                placeholder='aws://nova/sales-postgres'
                autoComplete='off'
              />
              <p className='text-xs text-muted-foreground'>
                The name of a secret holding a username and password, not the
                password itself.
              </p>
            </div>
          )}
          <p className='text-xs text-muted-foreground'>
            Use an account that can only read. Access to each table is still
            decided by the access policies of the person querying it.
          </p>
          <div className='space-y-1.5'>
            <Label htmlFor='connection-comment'>Description (optional)</Label>
            <Input
              id='connection-comment'
              value={form.comment}
              onChange={(e) => setForm({ ...form, comment: e.target.value })}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={!ready || submitting}>
            {submitting ? <Loader2 className='mr-2 h-4 w-4 animate-spin' /> : null}
            Add connection
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Replace a connection's credentials. Write-only: nothing is read back. */
export function CredentialsDialog({
  connection,
  onOpenChange,
  onSaved,
}: {
  connection: ConnectionSummary | null
  onOpenChange: (open: boolean) => void
  onSaved?: () => void
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setUsername('')
    setPassword('')
  }, [connection])

  const handleSave = async () => {
    if (!connection || !username.trim() || !password) return
    setSubmitting(true)
    try {
      await setConnectionCredentials(connection.name, {
        username: username.trim(),
        password,
      })
      toast.success(`Credentials for "${connection.name}" saved`)
      onOpenChange(false)
      onSaved?.()
    } catch (err) {
      toast.error(errorMessage(err, 'Could not save the credentials'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={!!connection} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-md'>
        <DialogHeader>
          <DialogTitle>Enter credentials</DialogTitle>
          <DialogDescription>
            New credentials for {connection?.name} replace the current ones.
            They are stored encrypted and never shown again.
          </DialogDescription>
        </DialogHeader>
        <div className='space-y-3 py-2'>
          <div className='space-y-1.5'>
            <Label htmlFor='credentials-username'>Username</Label>
            <Input
              id='credentials-username'
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete='off'
            />
          </div>
          <div className='space-y-1.5'>
            <Label htmlFor='credentials-password'>Password</Label>
            <Input
              id='credentials-password'
              type='password'
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete='new-password'
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!username.trim() || !password || submitting}>
            {submitting ? <Loader2 className='mr-2 h-4 w-4 animate-spin' /> : null}
            Save credentials
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Create a queryable catalog from a connection, picked by name. */
export function CreateForeignCatalogDialog({
  open,
  onOpenChange,
  connections,
  initialConnection,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  connections: ConnectionSummary[]
  initialConnection?: string
  onCreated?: (name: string) => void
}) {
  const usable = connections.filter((c) => c.has_credentials)
  const [name, setName] = useState('')
  const [connection, setConnection] = useState('')
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (open) {
      setName('')
      setComment('')
      setConnection(initialConnection ?? '')
    }
  }, [open, initialConnection])

  const nameValid = NAME_PATTERN.test(name.trim())
  const ready = nameValid && connection !== ''

  const handleCreate = async () => {
    if (!ready) return
    setSubmitting(true)
    try {
      const created = await createForeignCatalog({
        name: name.trim(),
        connection,
        comment: comment.trim() || undefined,
      })
      toast.success(`Catalog "${created.name}" created`)
      onOpenChange(false)
      onCreated?.(created.name)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not create the catalog'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-md'>
        <DialogHeader>
          <DialogTitle>Create catalog from a connection</DialogTitle>
          <DialogDescription>
            Its schemas and tables become queryable as catalog.schema.table.
            Nobody can read them until an access policy grants it.
          </DialogDescription>
        </DialogHeader>
        <div className='space-y-3 py-2'>
          <div className='space-y-1.5'>
            <Label htmlFor='foreign-catalog-connection'>Connection</Label>
            <Select value={connection} onValueChange={setConnection}>
              <SelectTrigger id='foreign-catalog-connection'>
                <SelectValue placeholder='Choose a connection' />
              </SelectTrigger>
              <SelectContent>
                {usable.map((c) => (
                  <SelectItem key={c.name} value={c.name}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {usable.length === 0 ? (
              <p className='text-xs text-muted-foreground'>
                No connection with credentials yet. Add one on the Connections page.
              </p>
            ) : null}
          </div>
          <div className='space-y-1.5'>
            <Label htmlFor='foreign-catalog-name'>Catalog name</Label>
            <Input
              id='foreign-catalog-name'
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder='sales_pg'
              aria-invalid={name !== '' && !nameValid}
            />
          </div>
          <div className='space-y-1.5'>
            <Label htmlFor='foreign-catalog-comment'>Description (optional)</Label>
            <Input
              id='foreign-catalog-comment'
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={!ready || submitting}>
            {submitting ? <Loader2 className='mr-2 h-4 w-4 animate-spin' /> : null}
            Create catalog
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function DropConnectionDialog({
  connection,
  onOpenChange,
  onDropped,
}: {
  connection: ConnectionSummary | null
  onOpenChange: (open: boolean) => void
  onDropped?: (name: string) => void
}) {
  const [submitting, setSubmitting] = useState(false)
  const inUse = (connection?.catalogs.length ?? 0) > 0

  const handleDrop = async () => {
    if (!connection) return
    setSubmitting(true)
    try {
      await deleteConnection(connection.name)
      toast.success(`Connection "${connection.name}" removed`)
      onOpenChange(false)
      onDropped?.(connection.name)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not remove the connection'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ConfirmDialog
      open={!!connection}
      onOpenChange={onOpenChange}
      title={`Remove connection ${connection?.name ?? ''}?`}
      desc={
        inUse
          ? `Catalogs ${connection?.catalogs.join(', ')} use it. Drop them first.`
          : 'Its stored credentials are deleted too. This cannot be undone.'
      }
      confirmText='Remove'
      destructive
      disabled={inUse}
      isLoading={submitting}
      handleConfirm={handleDrop}
    />
  )
}
