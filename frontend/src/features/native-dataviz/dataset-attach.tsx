import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { LoadingLines } from '@/components/ui/loading-overlay'
import { ApiError } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import type { DatasetReferenceSummary, DocumentSummary, Operation, OperationReceipt } from './contracts'
import { DatasetInspectionPanel } from './dataset-inspection'
import { nativeDocuments } from './documents'

type Props = {
  dashboardId: string
  workspaceId: string
  revision: number
  references: DatasetReferenceSummary[]
  onSaved: (receipt: OperationReceipt, datasetId: string) => void
  onReload: () => void
  onCancel: () => void
}
type Cursor = { updatedAt: string; resourceId: string }

export function DatasetAttach(props: Props) {
  const epoch = useAuthStore(state => state.securityEpoch)
  return <AttachDataset key={`${props.dashboardId}:${props.workspaceId}:${epoch}`} {...props} epoch={epoch} />
}

function AttachDataset({ dashboardId, workspaceId, revision, references, onSaved, onReload, onCancel, epoch }: Props & { epoch: number }) {
  const [cursor, setCursor] = useState<Cursor>()
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<DocumentSummary | null>(null)
  const [phase, setPhase] = useState<'editing' | 'saving' | 'uncertain' | 'saved'>('editing')
  const [error, setError] = useState('')
  const [conflictAt, setConflictAt] = useState<number>()
  const intent = useRef<{ command: Operation; datasetId: string } | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  const locked = phase !== 'editing'
  const listing = useQuery({
    queryKey: ['native-dataviz', 'attachable-datasets', epoch, workspaceId, cursor],
    queryFn: ({ signal }) => nativeDocuments(epoch).list('datasets', workspaceId, {
      draft: true, limit: 50, before: cursor, signal,
    }),
    retry: false, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
  const detail = useQuery({
    queryKey: ['native-dataviz', 'dataset-draft', epoch, selected?.resource_id, selected?.revision],
    queryFn: ({ signal }) => nativeDocuments(epoch).read('datasets', selected!.resource_id, {
      draft: true, revision: selected!.revision, signal,
    }),
    enabled: Boolean(selected),
    retry: false, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
  const items = listing.data ?? []
  const visible = items.filter(item => item.title.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  const definition = detail.isSuccess && detail.data.definition
  const dataset = definition && 'dataset_id' in definition && 'source' in definition ? definition : null
  const included = (id: string) => references.some(item => item.dataset_id === id)
  const ready = selected && dataset && !included(selected.resource_id) && references.length < 128 && conflictAt !== revision
  const current = (controller: AbortController) => !controller.signal.aborted
    && useAuthStore.getState().securityEpoch === epoch

  async function save() {
    if (request.current) return
    if (!intent.current) {
      if (!ready) return
      intent.current = {
        datasetId: selected.resource_id,
        command: {
          operation_id: crypto.randomUUID(), expected_revision: revision,
          changes: [{ op: 'datasets.replace', datasets: [
            ...references.map(item => ({ dataset_id: item.dataset_id, revision: item.revision })),
            { dataset_id: selected.resource_id, revision: selected.revision },
          ] }],
        },
      }
    }
    const saved = intent.current
    const controller = new AbortController()
    request.current = controller
    setPhase('saving')
    setError('')
    let receipt: OperationReceipt | undefined
    try {
      receipt = await nativeDocuments(epoch).apply(dashboardId, saved.command, { signal: controller.signal })
      if (receipt.base_revision !== saved.command.expected_revision) throw new Error('Unexpected save revision')
      if (current(controller)) setPhase('saved')
    } catch (caught) {
      receipt = undefined
      if (!current(controller)) return
      if (caught instanceof ApiError && [401, 403, 404, 409, 422].includes(caught.status)) {
        intent.current = null
        setPhase('editing')
        setConflictAt(caught.status === 409 ? revision : undefined)
        setError(caught.status === 409
          ? 'The dashboard changed. Reload it and review the current datasets before adding this revision.'
          : 'This dataset could not be added with your current access. Reload the list and choose an available dataset.')
        if (caught.status !== 409) setSelected(null)
      } else {
        setPhase('uncertain')
        setError('The save result is not confirmed. Retry this save to recover its result before making another change.')
      }
    } finally {
      if (request.current === controller) request.current = null
    }
    if (receipt && current(controller)) onSaved(receipt, saved.datasetId)
  }

  if (phase === 'saved') return <p role='status' className='text-sm'>Dataset added to this dashboard.</p>

  return <section aria-label='Use existing dataset' className='min-w-0 space-y-4'>
    <div className='space-y-1'>
      <h3 className='text-base font-medium'>Use existing dataset</h3>
      <p className='text-sm text-muted-foreground'>Choose a dataset you can edit in this workspace. The dashboard keeps the selected revision; later edits require an explicit update.</p>
    </div>
    <Input aria-label='Search this dataset page' placeholder='Search this page' className='h-11 sm:h-9'
      value={search} disabled={locked} onChange={event => setSearch(event.target.value)} />
    {listing.isPending ? <LoadingLines rows={3} />
      : listing.isError ? <p role='alert' className='text-sm text-destructive'>Datasets could not be loaded with your current access.</p>
        : <div role='group' aria-label='Available datasets' className='max-h-72 overflow-auto rounded-md border'>
          {visible.map(item => <button key={item.resource_id} type='button' disabled={locked || included(item.resource_id)}
            aria-pressed={selected?.resource_id === item.resource_id}
            className={cn('flex min-h-11 w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:opacity-60',
              selected?.resource_id === item.resource_id ? 'bg-accent text-accent-foreground' : 'hover:bg-muted')}
            onClick={() => { setSelected(item); setError('') }}>
            <span className='min-w-0 break-words'>{item.title}</span>
            <span className='shrink-0 text-xs'>{included(item.resource_id) ? 'Already added' : `Revision ${item.revision}`}</span>
          </button>)}
          {!visible.length && <p role='status' className='p-3 text-sm text-muted-foreground'>
            {search ? 'No matching datasets on this page.' : 'No editable datasets are available on this page.'}
          </p>}
        </div>}
    <div className='flex flex-wrap gap-2'>
      <Button type='button' variant='outline' className='h-11 sm:h-9' disabled={locked || listing.isFetching}
        onClick={() => { setSelected(null); if (cursor) setCursor(undefined); else void listing.refetch() }}>Reload list</Button>
      <Button type='button' variant='outline' className='h-11 sm:h-9' disabled={locked || listing.isFetching || listing.isError || items.length < 50}
        onClick={() => { const last = items[items.length - 1]; if (last) { setCursor({ updatedAt: last.updated_at, resourceId: last.resource_id }); setSelected(null); setSearch('') } }}>Next page</Button>
    </div>
    {selected && detail.isPending && <p role='status' className='text-sm text-muted-foreground'>Reading the selected revision…</p>}
    {selected && detail.isError && <p role='alert' className='text-sm text-destructive'>This dataset revision is unavailable. Reload the list and choose again.</p>}
    {dataset && <div className='space-y-3'>
      <p className='break-words text-sm font-medium'>{dataset.title} · Revision {selected?.revision}</p>
      <DatasetInspectionPanel inspection={dataset} />
    </div>}
    {references.length >= 128 && <p role='alert' className='text-sm'>This dashboard has reached its 128-dataset limit.</p>}
    {error && <div role='alert' className='space-y-2 text-sm'>
      <p className='text-destructive'>{error}</p>
      {conflictAt !== undefined && <Button type='button' variant='outline' onClick={onReload}>Reload dashboard</Button>}
    </div>}
    {phase === 'saving' && <p role='status' className='text-sm text-muted-foreground'>Adding dataset…</p>}
    <div className='flex flex-wrap gap-2'>
      <Button type='button' className='h-11 sm:h-9' disabled={phase !== 'uncertain' && (locked || !ready)}
        onClick={() => void save()}>{phase === 'uncertain' ? 'Retry save' : 'Add selected revision'}</Button>
      <Button type='button' variant='ghost' className='h-11 sm:h-9' disabled={locked} onClick={onCancel}>Cancel</Button>
    </div>
  </section>
}
