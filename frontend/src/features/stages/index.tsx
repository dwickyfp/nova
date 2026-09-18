import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertCircle,
  Boxes,
  ChevronRight,
  FolderOpen,
  Plus,
  Trash2,
} from 'lucide-react'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines } from '@/components/ui/loading-overlay'
import { PageHeader } from '@/components/ui/page-header'
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
  SimpleTablePagination,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
import { StageBrowser } from './stage-browser'
import {
  createStage,
  deleteStage,
  fetchStages,
  type Stage,
  type StageCreatePayload,
} from './api'

const PAGE_SIZE = 10

const emptyForm: StageCreatePayload = {
  name: '',
  database_name: '',
  schema_name: '',
  storage_connection: '',
  base_prefix: '',
}

export function StagesPage() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Stage | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState<StageCreatePayload>({ ...emptyForm })
  const [dropTarget, setDropTarget] = useState<Stage | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  const stagesQuery = useQuery({
    queryKey: ['stages'],
    queryFn: fetchStages,
  })

  useEffect(() => {
    if (stagesQuery.error) {
      toast.error('Failed to load stages', {
        description: stagesQuery.error.message,
      })
    }
  }, [stagesQuery.error])

  const createMutation = useMutation({
    mutationFn: createStage,
    onSuccess: (stage) => {
      toast.success(`Stage "${stage.name}" created`, {
        description: `Scope: ${stage.database_name}.${stage.schema_name}`,
      })
      setCreateOpen(false)
      setForm({ ...emptyForm })
      queryClient.invalidateQueries({ queryKey: ['stages'] })
    },
    onError: (err: Error) => toast.error('Could not create stage', { description: err.message }),
  })

  const dropMutation = useMutation({
    mutationFn: (id: string) => deleteStage(id),
    onSuccess: () => {
      toast.success('Stage deleted')
      setDropTarget(null)
      queryClient.invalidateQueries({ queryKey: ['stages'] })
    },
    onError: (err: Error) => toast.error('Could not delete stage', { description: err.message }),
  })

  if (selected) {
    return (
      <StageBrowser
        stage={selected}
        onBack={() => setSelected(null)}
      />
    )
  }

  const stages = stagesQuery.data?.stages ?? []
  const total = stages.length
  const pageStart = (page - 1) * pageSize
  const pageItems = stages.slice(pageStart, pageStart + pageSize)

  const createDisabled =
    !form.name.trim() || !form.database_name.trim() || !form.schema_name.trim()

  return (
    <div className='flex min-h-0 flex-1 flex-col gap-6'>
      <PageHeader
        title='Stage Manager'
        description='Named folders backed by object storage. Browse, upload, and query files through @stage references.'
        actions={
          <Button
            size='sm'
            onClick={() => {
              setForm({ ...emptyForm })
              setCreateOpen(true)
            }}
          >
            <Plus className='me-1.5 size-4' />
            Create stage
          </Button>
        }
      />

      <SimpleTableViewport>
        {stagesQuery.isFetching && !stagesQuery.isLoading ? (
          <div className='border-b border-border bg-muted px-4 py-2 text-xs text-muted-foreground'>
            Refreshing stages...
          </div>
        ) : null}
        <table className='w-full'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Stage
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Scope
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Storage connection
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Created
              </th>
              <th className='w-24 px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {stagesQuery.isLoading ? (
              <tr>
                <td colSpan={5} className='px-4 py-6'>
                  <LoadingLines rows={5} />
                </td>
              </tr>
            ) : stagesQuery.isError ? (
              <tr>
                <td colSpan={5} className='px-4 py-6'>
                  <EmptyState
                    variant='error'
                    icon={AlertCircle}
                    title='Could not load stages'
                    description='The stage service did not respond. Check the connection and retry.'
                    action={
                      <Button variant='outline' size='sm' onClick={() => void stagesQuery.refetch()}>
                        Retry
                      </Button>
                    }
                  />
                </td>
              </tr>
            ) : stages.length === 0 ? (
              <tr>
                <td colSpan={5} className='px-4 py-6'>
                  <EmptyState
                    icon={Boxes}
                    title='No stages registered'
                    description='A stage is a named folder bound to a database and schema. Create one to upload and query files.'
                    action={
                      <Button size='sm' onClick={() => setCreateOpen(true)}>
                        <Plus className='me-1.5 size-4' />
                        Create stage
                      </Button>
                    }
                  />
                </td>
              </tr>
            ) : (
              pageItems.map((stage) => (
                <tr
                  key={stage.id}
                  className='cursor-pointer border-b border-border transition-colors hover:bg-muted/50'
                  onClick={() => setSelected(stage)}
                >
                  <td className='px-4 py-3'>
                    <div className='flex items-center gap-2'>
                      <FolderOpen className='size-4 shrink-0 text-muted-foreground' />
                      <span className='text-sm font-medium'>@{stage.name}</span>
                    </div>
                  </td>
                  <td className='px-4 py-3 text-xs text-muted-foreground'>
                    {stage.database_name}.{stage.schema_name}
                  </td>
                  <td className='px-4 py-3 text-xs text-muted-foreground'>
                    {stage.storage_connection}
                  </td>
                  <td className='px-4 py-3 text-xs text-muted-foreground whitespace-nowrap'>
                    {stage.created_at
                      ? new Date(stage.created_at).toLocaleDateString(undefined, {
                          year: 'numeric',
                          month: 'short',
                          day: 'numeric',
                        })
                      : '—'}
                  </td>
                  <td className='px-4 py-3 text-right'>
                    <div className='flex items-center justify-end gap-1'>
                      <Button
                        variant='ghost'
                        size='sm'
                        className='h-8 gap-1 px-2 text-xs'
                        onClick={(event) => {
                          event.stopPropagation()
                          setSelected(stage)
                        }}
                      >
                        Browse
                        <ChevronRight className='size-3.5' />
                      </Button>
                      <Button
                        variant='ghost'
                        size='sm'
                        className='size-8 p-0 text-muted-foreground hover:text-destructive'
                        aria-label={`Delete stage ${stage.name}`}
                        onClick={(event) => {
                          event.stopPropagation()
                          setDropTarget(stage)
                        }}
                      >
                        <Trash2 className='size-3.5' />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </SimpleTableViewport>

      <SimpleTablePagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
        }}
      />

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className='sm:max-w-lg'>
          <DialogHeader>
            <DialogTitle>Create stage</DialogTitle>
            <DialogDescription>
              A stage is a virtual folder bound to one database and schema. Files land
              in the configured storage connection.
            </DialogDescription>
          </DialogHeader>
          <div className='grid gap-4 py-2'>
            <div className='grid gap-1.5'>
              <Label htmlFor='stage-name'>Name</Label>
              <Input
                id='stage-name'
                value={form.name}
                onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                placeholder='stage1'
                autoComplete='off'
              />
              <p className='text-xs text-muted-foreground'>
                Referenced in SQL as <code className='rounded bg-muted px-1 py-0.5'>@{form.name || 'stage1'}.file.csv</code>
              </p>
            </div>
            <div className='grid gap-4 sm:grid-cols-2'>
              <div className='grid gap-1.5'>
                <Label htmlFor='stage-database'>Database</Label>
                <Input
                  id='stage-database'
                  value={form.database_name}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, database_name: event.target.value }))
                  }
                  placeholder='DATALAKE'
                  autoComplete='off'
                />
              </div>
              <div className='grid gap-1.5'>
                <Label htmlFor='stage-schema'>Schema</Label>
                <Input
                  id='stage-schema'
                  value={form.schema_name}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, schema_name: event.target.value }))
                  }
                  placeholder='bronze'
                  autoComplete='off'
                />
              </div>
            </div>
            <div className='grid gap-1.5'>
              <Label htmlFor='stage-connection'>Storage connection</Label>
              <Input
                id='stage-connection'
                value={form.storage_connection}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, storage_connection: event.target.value }))
                }
                placeholder='production'
                autoComplete='off'
              />
              <p className='text-xs text-muted-foreground'>
                Connection name from the server configuration. The provider stays
                invisible to stage users.
              </p>
            </div>
            <div className='grid gap-1.5'>
              <Label htmlFor='stage-prefix'>
                Base prefix <span className='font-normal text-muted-foreground'>(optional)</span>
              </Label>
              <Input
                id='stage-prefix'
                value={form.base_prefix ?? ''}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, base_prefix: event.target.value }))
                }
                placeholder='datalake/bronze/stage1'
                autoComplete='off'
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={createDisabled || createMutation.isPending}
              onClick={() => createMutation.mutate(form)}
            >
              {createMutation.isPending ? 'Creating...' : 'Create stage'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(dropTarget)}
        onOpenChange={(open) => {
          if (!open) setDropTarget(null)
        }}
        title={`Delete @${dropTarget?.name ?? ''}?`}
        desc={
          <span>
            This removes the stage registration. Files already stored under the
            stage prefix are not deleted by this action.
          </span>
        }
        destructive
        confirmText='Delete stage'
        isLoading={dropMutation.isPending}
        handleConfirm={() => {
          if (dropTarget) dropMutation.mutate(dropTarget.id)
        }}
      />
    </div>
  )
}
