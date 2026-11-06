import { useEffect, useMemo, useRef, useState } from 'react'
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
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import {
  createConnection,
  createForeignCatalog,
  deleteConnection,
  setConnectionCredentials,
  type ConnectionSummary,
} from './api'
import {
  SOURCES,
  addressComplete,
  sourceFor,
  type SourceDescriptor,
  type SourceType,
} from './sources'

const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/
const NAME_HINT = 'Use letters, digits, and underscores, starting with a letter.'

function errorMessage(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback
}

// ── Shared fields ─────────────────────────────────────────────

function SourcePicker({
  value,
  onChange,
  idPrefix,
}: {
  value: SourceType
  onChange: (value: SourceType) => void
  idPrefix: string
}) {
  return (
    <RadioGroup
      value={value}
      onValueChange={(next) => onChange(next as SourceType)}
      aria-label='Source'
      className='grid gap-2 sm:grid-cols-3'
    >
      {SOURCES.map((source) => {
        const id = `${idPrefix}-${source.type}`
        return (
          <Label
            key={source.type}
            htmlFor={id}
            className={cn(
              'flex cursor-pointer flex-col items-start gap-1 rounded-lg border border-surface-border p-3 font-normal transition-colors hover:bg-surface-1',
              value === source.type && 'border-primary bg-surface-1',
            )}
          >
            <span className='flex items-center gap-2 text-sm font-medium'>
              <RadioGroupItem id={id} value={source.type} />
              {source.label}
            </span>
            <span className='text-xs text-muted-foreground'>{source.summary}</span>
          </Label>
        )
      })}
    </RadioGroup>
  )
}

function AddressFields({
  source,
  values,
  onChange,
  idPrefix,
}: {
  source: SourceDescriptor
  values: Record<string, string>
  onChange: (values: Record<string, string>) => void
  idPrefix: string
}) {
  return (
    <div className='grid gap-3 sm:grid-cols-2'>
      {source.fields.map((field) => {
        const id = `${idPrefix}-${field.key}`
        return (
          <div key={field.key} className='space-y-1.5'>
            <Label htmlFor={id}>
              {field.label}
              {field.required ? null : (
                <span className='font-normal text-muted-foreground'> (optional)</span>
              )}
            </Label>
            <Input
              id={id}
              value={values[field.key] ?? ''}
              onChange={(e) => onChange({ ...values, [field.key]: e.target.value })}
              placeholder={field.placeholder}
              inputMode={field.numeric ? 'numeric' : undefined}
              autoComplete='off'
              spellCheck={false}
            />
            {field.hint ? <p className='text-xs text-muted-foreground'>{field.hint}</p> : null}
          </div>
        )
      })}
    </div>
  )
}

function CredentialFields({
  source,
  username,
  password,
  onUsername,
  onPassword,
  idPrefix,
}: {
  source: SourceDescriptor
  username: string
  password: string
  onUsername: (value: string) => void
  onPassword: (value: string) => void
  idPrefix: string
}) {
  return (
    <div className='space-y-1.5'>
      <div className='grid gap-3 sm:grid-cols-2'>
        <div className='space-y-1.5'>
          <Label htmlFor={`${idPrefix}-username`}>{source.usernameLabel}</Label>
          <Input
            id={`${idPrefix}-username`}
            value={username}
            onChange={(e) => onUsername(e.target.value)}
            autoComplete='off'
            spellCheck={false}
          />
        </div>
        <div className='space-y-1.5'>
          <Label htmlFor={`${idPrefix}-password`}>{source.passwordLabel}</Label>
          <Input
            id={`${idPrefix}-password`}
            type='password'
            value={password}
            onChange={(e) => onPassword(e.target.value)}
            autoComplete='new-password'
          />
        </div>
      </div>
      {source.passwordHint ? (
        <p className='text-xs text-muted-foreground'>{source.passwordHint}</p>
      ) : null}
    </div>
  )
}

// ── Add connection ────────────────────────────────────────────

type CredentialMode = 'enter' | 'reference'

/**
 * Register a source. The address and credentials are entered here once and
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
  const [name, setName] = useState('')
  const [type, setType] = useState<SourceType>('postgres')
  const [address, setAddress] = useState<Record<string, string>>({})
  const [mode, setMode] = useState<CredentialMode>('enter')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [secretRef, setSecretRef] = useState('')
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) {
      setName('')
      setType('postgres')
      setAddress({})
      setMode('enter')
      setUsername('')
      setPassword('')
      setSecretRef('')
      setComment('')
    }
  }, [open])

  const source = sourceFor(type) ?? SOURCES[0]
  const nameValid = NAME_PATTERN.test(name.trim())
  const credentialsReady =
    mode === 'enter' ? username.trim() !== '' && password !== '' : secretRef.trim() !== ''
  const ready = nameValid && addressComplete(source, address) && credentialsReady

  const handleCreate = async () => {
    if (!ready) return
    setSubmitting(true)
    try {
      const base = {
        name: name.trim(),
        connector_type: type,
        endpoint: source.toEndpoint(address),
        comment: comment.trim() || undefined,
      }
      const created = await createConnection(
        mode === 'enter'
          ? { ...base, credentials: { username: username.trim(), password } }
          : { ...base, secret_ref: secretRef.trim() }
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
      <DialogContent className='max-h-[90vh] overflow-y-auto sm:max-w-xl'>
        <DialogHeader>
          <DialogTitle>Add connection</DialogTitle>
          <DialogDescription>
            The address and credentials are stored on the server and are not
            shown again. People pick this connection by its name.
          </DialogDescription>
        </DialogHeader>
        <div className='space-y-4 py-2'>
          <SourcePicker
            value={type}
            onChange={(next) => {
              setType(next)
              setAddress({})
            }}
            idPrefix='connection-source'
          />
          <div className='space-y-1.5'>
            <Label htmlFor='connection-name'>Name</Label>
            <Input
              id='connection-name'
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={`sales_${type}`}
              aria-invalid={name !== '' && !nameValid}
            />
            {name !== '' && !nameValid ? (
              <p className='text-xs text-destructive'>{NAME_HINT}</p>
            ) : null}
          </div>
          <AddressFields
            source={source}
            values={address}
            onChange={setAddress}
            idPrefix='connection'
          />
          <div className='space-y-1.5'>
            <Label htmlFor='connection-credential-mode'>Credentials</Label>
            <Select value={mode} onValueChange={(value) => setMode(value as CredentialMode)}>
              <SelectTrigger id='connection-credential-mode'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='enter'>Enter them now</SelectItem>
                <SelectItem value='reference'>Use a secret store reference</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {mode === 'enter' ? (
            <CredentialFields
              source={source}
              username={username}
              password={password}
              onUsername={setUsername}
              onPassword={setPassword}
              idPrefix='connection'
            />
          ) : (
            <div className='space-y-1.5'>
              <Label htmlFor='connection-secret-ref'>Secret reference</Label>
              <Input
                id='connection-secret-ref'
                value={secretRef}
                onChange={(e) => setSecretRef(e.target.value)}
                placeholder={`aws://nova/sales-${type}`}
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
            <Label htmlFor='connection-comment'>
              Description <span className='font-normal text-muted-foreground'>(optional)</span>
            </Label>
            <Input
              id='connection-comment'
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
            Add connection
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Credentials ───────────────────────────────────────────────

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
  const source = sourceFor(connection?.connector_type) ?? SOURCES[1]

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
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader>
          <DialogTitle>Enter credentials</DialogTitle>
          <DialogDescription>
            New credentials for {connection?.name} replace the current ones.
            They are stored encrypted and never shown again.
          </DialogDescription>
        </DialogHeader>
        <div className='py-2'>
          <CredentialFields
            source={source}
            username={username}
            password={password}
            onUsername={setUsername}
            onPassword={setPassword}
            idPrefix='credentials'
          />
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

// ── Add catalog ───────────────────────────────────────────────

const NEW_CONNECTION = '__new__'

/**
 * Add a catalog over a StarRocks, PostgreSQL, or Snowflake source: reuse a
 * connection of that type, or register one in the same step.
 */
export function AddCatalogDialog({
  open,
  onOpenChange,
  connections,
  initialConnection,
  onCreated,
  onConnectionCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  connections: ConnectionSummary[]
  /** Preselect a connection, and its source type. */
  initialConnection?: string
  onCreated?: (name: string) => void
  /** A connection was registered, even if the catalog then failed. */
  onConnectionCreated?: () => void
}) {
  const [type, setType] = useState<SourceType>('postgres')
  const [name, setName] = useState('')
  const [connection, setConnection] = useState(NEW_CONNECTION)
  const [connectionName, setConnectionName] = useState('')
  const [address, setAddress] = useState<Record<string, string>>({})
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const usable = useMemo(
    () => connections.filter((c) => c.has_credentials && sourceFor(c.connector_type)),
    [connections],
  )
  const ofType = usable.filter((c) => sourceFor(c.connector_type)?.type === type)
  // The connection list can arrive after the dialog opens: until the person
  // picks one, reuse an existing connection of the chosen type.
  const picked = useRef(false)
  const firstOfType = ofType[0]?.name
  useEffect(() => {
    if (open && !picked.current && connection === NEW_CONNECTION && firstOfType) {
      setConnection(firstOfType)
    }
  }, [open, connection, firstOfType])

  useEffect(() => {
    if (!open) return
    const preset = usable.find((c) => c.name === initialConnection)
    const presetType = sourceFor(preset?.connector_type)?.type
    picked.current = false
    setType(presetType ?? 'postgres')
    setConnection(preset ? preset.name : NEW_CONNECTION)
    setName('')
    setConnectionName('')
    setAddress({})
    setUsername('')
    setPassword('')
    setComment('')
    // Reset only when the dialog opens; the connection list may refresh while open.
  }, [open, initialConnection])

  const source = sourceFor(type) ?? SOURCES[0]
  const creatingConnection = connection === NEW_CONNECTION
  const nameValid = NAME_PATTERN.test(name.trim())
  const connectionNameValue = connectionName.trim() || (nameValid ? `${name.trim()}_conn` : '')
  const connectionNameValid = NAME_PATTERN.test(connectionNameValue)
  const ready =
    nameValid &&
    (!creatingConnection ||
      (connectionNameValid &&
        addressComplete(source, address) &&
        username.trim() !== '' &&
        password !== ''))

  const chooseType = (next: SourceType) => {
    setType(next)
    setAddress({})
    const first = usable.find((c) => sourceFor(c.connector_type)?.type === next)
    setConnection(first ? first.name : NEW_CONNECTION)
  }

  const handleCreate = async () => {
    if (!ready) return
    setSubmitting(true)
    let connectionUsed = connection
    try {
      if (creatingConnection) {
        const created = await createConnection({
          name: connectionNameValue,
          connector_type: type,
          endpoint: source.toEndpoint(address),
          credentials: { username: username.trim(), password },
        })
        connectionUsed = created.name
        onConnectionCreated?.()
      }
    } catch (err) {
      toast.error(errorMessage(err, 'Could not add the connection'))
      setSubmitting(false)
      return
    }
    try {
      const created = await createForeignCatalog({
        name: name.trim(),
        connection: connectionUsed,
        comment: comment.trim() || undefined,
      })
      toast.success(`Catalog "${created.name}" added`)
      onOpenChange(false)
      onCreated?.(created.name)
    } catch (err) {
      const message = errorMessage(err, 'Could not create the catalog')
      toast.error(
        creatingConnection
          ? `Connection "${connectionUsed}" was saved, but the catalog was not created: ${message}`
          : message,
      )
      // The connection exists now: retrying should reuse it, not add it twice.
      setConnection(connectionUsed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='max-h-[90vh] overflow-y-auto sm:max-w-xl'>
        <DialogHeader>
          <DialogTitle>Add catalog</DialogTitle>
          <DialogDescription>
            Its schemas and tables become queryable as catalog.schema.table.
            Nobody can read them until an access policy grants it.
          </DialogDescription>
        </DialogHeader>
        <div className='space-y-4 py-2'>
          <SourcePicker value={type} onChange={chooseType} idPrefix='catalog-source' />
          <div className='space-y-1.5'>
            <Label htmlFor='catalog-name'>Catalog name</Label>
            <Input
              id='catalog-name'
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={`sales_${type === 'postgres' ? 'pg' : type === 'snowflake' ? 'sf' : 'sr'}`}
              aria-invalid={name !== '' && !nameValid}
            />
            {name !== '' && !nameValid ? (
              <p className='text-xs text-destructive'>{NAME_HINT}</p>
            ) : null}
          </div>
          <div className='space-y-1.5'>
            <Label htmlFor='catalog-connection'>Connection</Label>
            <Select
              value={connection}
              onValueChange={(value) => {
                picked.current = true
                setConnection(value)
              }}
            >
              <SelectTrigger id='catalog-connection'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ofType.map((c) => (
                  <SelectItem key={c.name} value={c.name}>
                    {c.name}
                  </SelectItem>
                ))}
                <SelectItem value={NEW_CONNECTION}>New {source.label} connection</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {creatingConnection ? (
            <div className='space-y-4 rounded-lg border border-surface-border p-4'>
              <div className='space-y-1.5'>
                <Label htmlFor='catalog-connection-name'>Connection name</Label>
                <Input
                  id='catalog-connection-name'
                  value={connectionName}
                  onChange={(e) => setConnectionName(e.target.value)}
                  placeholder={nameValid ? `${name.trim()}_conn` : 'sales_conn'}
                  aria-invalid={connectionName !== '' && !connectionNameValid}
                />
                {connectionName !== '' && !connectionNameValid ? (
                  <p className='text-xs text-destructive'>{NAME_HINT}</p>
                ) : null}
              </div>
              <AddressFields
                source={source}
                values={address}
                onChange={setAddress}
                idPrefix='catalog-connection'
              />
              <CredentialFields
                source={source}
                username={username}
                password={password}
                onUsername={setUsername}
                onPassword={setPassword}
                idPrefix='catalog-connection'
              />
              <p className='text-xs text-muted-foreground'>
                Stored encrypted and never shown again. Use an account that can only read.
              </p>
            </div>
          ) : null}
          <div className='space-y-1.5'>
            <Label htmlFor='catalog-comment'>
              Description <span className='font-normal text-muted-foreground'>(optional)</span>
            </Label>
            <Input
              id='catalog-comment'
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
            Add catalog
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Remove connection ─────────────────────────────────────────

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
