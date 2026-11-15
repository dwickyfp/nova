import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Columns3, Database, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { LoadingLines } from '@/components/ui/loading-overlay'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import type { DatasetReferenceSummary, OperationReceipt } from './contracts'
import { DatasetCreate } from './dataset-create'
import { DatasetAttach } from './dataset-attach'
import { DatasetInspectionPanel } from './dataset-inspection'
import { DatasetPreviewPane } from './dataset-preview'
import { nativeDocuments } from './documents'

type Props = {
  dashboardId: string
  workspaceId: string
  revision: number
  onCommitted: (receipt: OperationReceipt) => void
  onReload: () => void
}

export function DashboardDataTab(props: Props) {
  const epoch = useAuthStore(state => state.securityEpoch)
  return <DataTab key={`${props.dashboardId}:${epoch}`} {...props} epoch={epoch} />
}

function DataTab({ dashboardId, workspaceId, revision, onCommitted, onReload, epoch }: Props & { epoch: number }) {
  const [selectedId, setSelectedId] = useState('')
  const [creating, setCreating] = useState<'new' | 'existing' | null>(null)
  const query = useQuery({
    queryKey: ['native-dataviz', 'dashboard-datasets', epoch, dashboardId, revision],
    queryFn: ({ signal }) => nativeDocuments(epoch).dashboardDatasets(dashboardId, revision, { signal }),
    retry: false, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
  const sources = query.data?.datasets ?? []
  const selected = selectedId ? sources.find(item => item.dataset_id === selectedId)
    : sources.find(item => item.title !== null)

  if (query.isError) return <div className='min-h-0 flex-1 overflow-auto p-4'>
    <EmptyState variant='error' title='Datasets are unavailable'
      description='The dashboard could not be read with your current access. Reload to check again.'
      action={<Button variant='outline' onClick={() => void query.refetch()}>Reload datasets</Button>} />
  </div>

  return <div className='@container/data flex min-h-0 min-w-0 flex-1 flex-col'>
    <div className='flex min-h-0 min-w-0 flex-1 flex-col @2xl/data:flex-row'>
    <aside aria-label='Dashboard datasets' className='flex min-h-0 shrink-0 flex-col border-b @2xl/data:w-60 @2xl/data:border-r @2xl/data:border-b-0'>
      <div className='flex h-12 shrink-0 items-center justify-between gap-2 pr-2 pl-4'>
        <h2 className='text-sm font-medium'>Datasets</h2>
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button aria-label='Add dataset' title='Add dataset' variant='ghost' size='icon'
            className='size-11 @2xl/data:size-8' disabled={Boolean(creating) || query.isPending || sources.length >= 128}><Plus /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align='start'>
            <DropdownMenuItem className='min-h-11' onSelect={() => setCreating('new')}>New dataset</DropdownMenuItem>
            <DropdownMenuItem className='min-h-11' onSelect={() => setCreating('existing')}>Use existing dataset</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {query.isPending ? <LoadingLines rows={2} className='px-3' />
        : <nav aria-label='Choose a dataset' className='flex min-h-0 gap-1 overflow-auto p-2 @2xl/data:flex-col'>
            {sources.map((item, index) => <button key={item.dataset_id} type='button'
              disabled={Boolean(creating) || item.title === null}
              aria-current={!creating && selected?.dataset_id === item.dataset_id ? 'true' : undefined}
              className={cn('flex min-h-11 max-w-64 shrink-0 items-start gap-2 rounded-md px-2 py-2.5 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60 @2xl/data:max-w-none',
                !creating && selected?.dataset_id === item.dataset_id ? 'bg-accent text-accent-foreground' : 'hover:bg-muted')}
              onClick={() => setSelectedId(item.dataset_id)}>
              <Database className='mt-0.5 size-4 shrink-0' aria-hidden='true' />
              <span className='line-clamp-2 min-w-0 break-words' title={item.title ?? undefined}>{item.title ?? `Unavailable dataset ${index + 1}`}</span>
            </button>)}
          </nav>}
      {sources.length >= 128 && <p className='px-3 pb-3 text-xs text-muted-foreground'>This dashboard has reached its 128-dataset limit.</p>}
    </aside>
    {creating ? <div className='min-h-0 min-w-0 flex-1 overflow-auto p-4'>
      {creating === 'new' ? <DatasetCreate dashboardId={dashboardId} dashboardRevision={revision}
        onCancel={() => setCreating(null)} onReloadDashboard={onReload}
        onSaved={(receipt, datasetId) => {
          setSelectedId(datasetId)
          setCreating(null)
          onCommitted(receipt)
        }} /> : <DatasetAttach dashboardId={dashboardId} workspaceId={workspaceId} revision={revision}
          references={sources} onCancel={() => setCreating(null)} onReload={onReload}
          onSaved={(receipt, datasetId) => {
            setSelectedId(datasetId)
            setCreating(null)
            onCommitted(receipt)
          }} />}
    </div> : query.isPending ? <LoadingLines rows={4} className='flex-1 p-4' />
      : !sources.length ? <div className='min-h-0 flex-1 overflow-auto p-4'>
          <EmptyState icon={Database} title='Add a source to this dashboard'
            description='Choose a table, view, SQL query or published Semantic View. Rows load only when you run a preview.'
            action={<Button className='h-11 sm:h-9' onClick={() => setCreating('new')}>Add dataset</Button>} />
        </div>
        : selected?.title ? <SavedDataset key={`${selected.dataset_id}:${selected.revision}`}
            reference={selected} epoch={epoch} />
          : <div className='min-h-0 flex-1 overflow-auto p-4'>
              <EmptyState title='Select an available dataset'
                description='You need edit access to inspect a dataset in this dashboard. Reload after its access or references change.'
                action={<Button variant='outline' onClick={onReload}>Reload dashboard</Button>} />
            </div>}
    </div>
  </div>
}

function SavedDataset({ reference, epoch }: { reference: DatasetReferenceSummary; epoch: number }) {
  const query = useQuery({
    queryKey: ['native-dataviz', 'dataset-draft', epoch, reference.dataset_id, reference.revision],
    queryFn: ({ signal }) => nativeDocuments(epoch).read('datasets', reference.dataset_id, {
      draft: true, revision: reference.revision, signal,
    }),
    retry: false, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
  if (query.isPending) return <LoadingLines rows={4} className='flex-1 p-4' />
  const dataset = query.data?.definition
  if (query.isError || !dataset || !('dataset_id' in dataset) || !('source' in dataset)) {
    return <div className='min-h-0 flex-1 overflow-auto p-4'>
      <EmptyState variant='error' title='This dataset is unavailable'
        description='Its revision could not be read with your current access.'
        action={<Button variant='outline' onClick={() => void query.refetch()}>Reload dataset</Button>} />
    </div>
  }
  const sourceLabel = dataset.source.kind === 'sql' ? 'SQL query'
    : dataset.source.kind === 'semantic_model' ? 'Semantic View'
      : dataset.source.kind === 'dashboard_local_metric_view' ? 'Dashboard metric view'
        : dataset.source.kind === 'table' ? 'Table' : 'View'

  return <section aria-label={`${dataset.title} dataset`} className='flex min-h-0 min-w-0 flex-1 flex-col'>
    <header className='flex shrink-0 flex-wrap items-start justify-between gap-3 border-b p-4'>
      <div className='min-w-0'>
        <h3 className='break-words text-base font-medium'>{dataset.title}</h3>
        <p className='text-xs text-muted-foreground'>{sourceLabel} · Version {reference.revision}</p>
      </div>
      <Sheet>
        <SheetTrigger asChild><Button variant='outline' className='h-11 @5xl/data:hidden'><Columns3 />Fields and lineage</Button></SheetTrigger>
        <SheetContent className='flex min-h-0 flex-col'>
          <SheetHeader><SheetTitle>Fields and lineage</SheetTitle>
            <SheetDescription>{dataset.title} · Version {reference.revision}</SheetDescription></SheetHeader>
          <div className='min-h-0 overflow-auto p-4'><DatasetInspectionPanel inspection={dataset} /></div>
        </SheetContent>
      </Sheet>
    </header>
    <div className='flex min-h-0 min-w-0 flex-1'>
      <div className='flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4'>
        {'sql' in dataset.source && <details className='shrink-0 rounded-md border text-sm'>
          <summary className='cursor-pointer p-3'>Source SQL</summary>
          <pre className='max-h-48 overflow-auto border-t p-3 text-xs'>{dataset.source.sql}</pre>
        </details>}
        <DatasetPreviewPane datasetId={dataset.dataset_id} revision={reference.revision} draft title={dataset.title}
          parameters={dataset.parameters} />
      </div>
      <aside aria-label='Dataset fields and lineage' className='hidden min-h-0 w-80 shrink-0 overflow-auto border-l p-4 @5xl/data:block'>
        <DatasetInspectionPanel inspection={dataset} />
      </aside>
    </div>
  </section>
}
