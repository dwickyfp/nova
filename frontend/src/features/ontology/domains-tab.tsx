import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderTree } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingLines } from '@/components/ui/loading-overlay'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { ontologyApi, type Domain } from './api'
import { splitList, useDomains, useIsAdmin, useOntologyKey } from './model'

const TOP = '__top__'

export function DomainsTab() {
  const isAdmin = useIsAdmin()
  const domains = useDomains()
  const [editing, setEditing] = useState<Domain | 'new' | null>(null)

  return (
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <p className='text-sm text-muted-foreground'>
          A domain owns its concepts. Its steward roles approve changes; its reader roles decide
          who sees the concepts.
        </p>
        {isAdmin ? <Button onClick={() => setEditing('new')}>New domain</Button> : null}
      </div>

      {domains.isLoading ? (
        <LoadingLines rows={4} />
      ) : domains.isError ? (
        <EmptyState
          variant='error'
          icon={FolderTree}
          title='Domains could not be loaded'
          description={domains.error.message}
          action={<Button variant='outline' onClick={() => domains.refetch()}>Try again</Button>}
        />
      ) : !domains.data?.length ? (
        <EmptyState
          icon={FolderTree}
          title='No domains you can see'
          description={
            isAdmin
              ? 'Create a domain such as Finance and name the StarRocks roles that steward it.'
              : 'ACCOUNTADMIN creates domains and names the roles that steward and read them.'
          }
          action={isAdmin ? <Button onClick={() => setEditing('new')}>New domain</Button> : undefined}
        />
      ) : (
        <div className='overflow-x-auto rounded-md border'>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Domain</TableHead>
                <TableHead>Stewards</TableHead>
                <TableHead>Readers</TableHead>
                <TableHead>Status</TableHead>
                {isAdmin ? <TableHead className='w-0'><span className='sr-only'>Actions</span></TableHead> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {domains.data.map((domain) => (
                <TableRow key={domain.id}>
                  <TableCell className='max-w-72'>
                    <span className='block truncate font-medium'>{domain.qualified_path}</span>
                    {domain.description ? (
                      <span className='block truncate text-xs text-muted-foreground'>
                        {domain.description}
                      </span>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    {domain.steward_roles.join(', ')}
                    {domain.can_review ? (
                      <span className='ml-2 text-xs text-muted-foreground'>(your role)</span>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    {domain.visibility_roles.length ? domain.visibility_roles.join(', ') : 'Everyone'}
                  </TableCell>
                  <TableCell>
                    <StatusBadge tone={domain.status === 'ACTIVE' ? 'success' : 'neutral'}>
                      {domain.status === 'ACTIVE' ? 'Active' : 'Retired'}
                    </StatusBadge>
                  </TableCell>
                  {isAdmin ? (
                    <TableCell>
                      <Button size='sm' variant='ghost' onClick={() => setEditing(domain)}>
                        Edit
                      </Button>
                    </TableCell>
                  ) : null}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className='sm:max-w-lg'>
          {editing ? (
            <DomainForm
              domain={editing === 'new' ? undefined : editing}
              domains={domains.data ?? []}
              onClose={() => setEditing(null)}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function DomainForm({
  domain,
  domains,
  onClose,
}: {
  domain?: Domain
  domains: Domain[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const prefix = useOntologyKey()
  const [name, setName] = useState('')
  const [parentId, setParentId] = useState(TOP)
  const [description, setDescription] = useState(domain?.description ?? '')
  const [stewards, setStewards] = useState((domain?.steward_roles ?? []).join(', '))
  const [readers, setReaders] = useState((domain?.visibility_roles ?? []).join(', '))
  const [status, setStatus] = useState<Domain['status']>(domain?.status ?? 'ACTIVE')
  const save = useMutation({
    mutationFn: () =>
      domain
        ? ontologyApi.updateDomain(domain.id, {
            description: description.trim(),
            steward_roles: splitList(stewards),
            visibility_roles: splitList(readers),
            status,
            expected_revision: domain.revision,
          })
        : ontologyApi.createDomain({
            name: name.trim(),
            parent_id: parentId === TOP ? null : parentId,
            description: description.trim(),
            steward_roles: splitList(stewards),
            visibility_roles: splitList(readers),
          }),
    onSuccess: (saved) => {
      toast.success(`${saved.qualified_path} saved`)
      queryClient.invalidateQueries({ queryKey: prefix })
      onClose()
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const nameValid = /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name.trim())
  const ready = splitList(stewards).length > 0 && (domain || nameValid)

  return (
    <>
      <DialogHeader>
        <DialogTitle>{domain ? `Edit ${domain.qualified_path}` : 'New domain'}</DialogTitle>
        <DialogDescription>
          Roles are StarRocks roles. A steward approves while that role is active. Seeing a
          concept never grants access to the data behind it.
        </DialogDescription>
      </DialogHeader>
      <div className='grid gap-4'>
        {domain ? null : (
          <>
            <div className='grid gap-1.5'>
              <Label htmlFor='domain-name'>Name</Label>
              <Input
                id='domain-name'
                value={name}
                placeholder='Finance'
                aria-invalid={Boolean(name) && !nameValid}
                onChange={(event) => setName(event.target.value)}
              />
              <p className='text-xs text-muted-foreground'>
                Letters, digits and underscores. It starts every concept name in the domain.
              </p>
            </div>
            <div className='grid gap-1.5'>
              <Label htmlFor='domain-parent'>Inside</Label>
              <Select value={parentId} onValueChange={setParentId}>
                <SelectTrigger id='domain-parent' className='w-full'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={TOP}>No parent domain</SelectItem>
                  {domains.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.qualified_path}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </>
        )}
        <div className='grid gap-1.5'>
          <Label htmlFor='domain-description'>Description</Label>
          <Textarea
            id='domain-description'
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
        <div className='grid gap-1.5'>
          <Label htmlFor='domain-stewards'>Steward roles</Label>
          <Input
            id='domain-stewards'
            value={stewards}
            placeholder='finance_steward'
            onChange={(event) => setStewards(event.target.value)}
          />
        </div>
        <div className='grid gap-1.5'>
          <Label htmlFor='domain-readers'>Reader roles</Label>
          <Input
            id='domain-readers'
            value={readers}
            placeholder='Leave empty for everyone'
            onChange={(event) => setReaders(event.target.value)}
          />
        </div>
        {domain ? (
          <div className='grid gap-1.5'>
            <Label htmlFor='domain-status'>Status</Label>
            <Select value={status} onValueChange={(value) => setStatus(value as Domain['status'])}>
              <SelectTrigger id='domain-status' className='w-full'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='ACTIVE'>Active</SelectItem>
                <SelectItem value='RETIRED'>Retired: hidden from readers</SelectItem>
              </SelectContent>
            </Select>
          </div>
        ) : null}
      </div>
      <DialogFooter>
        <Button variant='outline' onClick={onClose}>
          Cancel
        </Button>
        <Button disabled={!ready || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving' : 'Save'}
        </Button>
      </DialogFooter>
    </>
  )
}
