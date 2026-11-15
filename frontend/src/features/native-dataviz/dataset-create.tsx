import { lazy, Suspense, useEffect, useId, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingLines } from '@/components/ui/loading-overlay'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { dashboardDatasetCreateSchema, type DashboardDatasetCreate, type ObjectSelection,
  type OperationReceipt, type Parameter, type SemanticSelection, type SqlSelection } from './contracts'
import { DatasetInspectionPanel, type DatasetInspection } from './dataset-inspection'
import { DatasetObjectPicker } from './dataset-object-picker'
import { DatasetParameterDeclarations } from './dataset-parameter-declarations'
import { declarationsFromDrafts, type DeclarationDraft } from './parameter-values'
import { DatasetPreviewPane } from './dataset-preview'
import { DatasetSemanticPicker } from './dataset-semantic-picker'
import { nativeDocuments } from './documents'

const DatasetSqlEditor = lazy(() => import('./dataset-sql-editor').then(module => ({ default: module.DatasetSqlEditor })))
type Selection = ObjectSelection | SqlSelection | SemanticSelection
type SourceKind = 'object' | 'sql' | 'semantic'
type SaveIntent = { body: DashboardDatasetCreate; operation_id: string; expected_revision: number }
type Props = {
  dashboardId: string
  dashboardRevision: number
  onSaved: (receipt: OperationReceipt, datasetId: string) => void
  onReloadDashboard: () => void
  onCancel: () => void
}

export function DatasetCreate(props: Props) {
  const securityEpoch = useAuthStore(state => state.securityEpoch)
  return <CreateDataset key={`${props.dashboardId}:${securityEpoch}`} {...props} securityEpoch={securityEpoch} />
}

function inspectionError(error: unknown) {
  if (error instanceof ApiError) {
    if ([401, 403, 404].includes(error.status)) return 'This source is unavailable with your current access. Choose another source.'
    if (error.status === 409) return 'The source changed. Select it again and inspect its current columns.'
    if (error.status === 422) return 'The source could not be inspected. Check the selection or use one supported read-only query.'
    if (error.status === 429) return 'Query capacity is busy. Try inspecting the source again shortly.'
    if (error.status === 504) return 'Source inspection timed out. Try again.'
  }
  return 'The source could not be inspected. Try again.'
}

function CreateDataset({ dashboardId, dashboardRevision, securityEpoch, onSaved, onReloadDashboard, onCancel }: Props & {
  securityEpoch: number
}) {
  const [datasetId] = useState(() => crypto.randomUUID())
  const titleId = useId()
  const activeRole = useAuthStore(state => state.auth.user?.activeRole)
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState<SourceKind>('object')
  const [source, setSource] = useState<Selection | null>(null)
  const [inspection, setInspection] = useState<DatasetInspection | null>(null)
  const [declarations, setDeclarations] = useState<DeclarationDraft[]>([])
  const [phase, setPhase] = useState<'editing' | 'inspecting' | 'saving' | 'uncertain' | 'saved'>('editing')
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const intent = useRef<SaveIntent | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => { request.current?.abort() }, [])
  const locked = phase !== 'editing'
  const savedParameters = inspection && 'parameters' in inspection ? inspection.parameters ?? [] : []
  const inspectable = source && (source.kind !== 'sql' || (source.database && source.sql.trim()))
  const current = (controller: AbortController) => !controller.signal.aborted
    && useAuthStore.getState().securityEpoch === securityEpoch

  function select(next: Selection | null) {
    setSource(next)
    setInspection(null)
    setError('')
    setConflict(false)
  }

  function declare(next: DeclarationDraft[]) {
    setDeclarations(next)
    setInspection(null)
    setError('')
    setConflict(false)
  }

  async function inspect() {
    if (!source || request.current) return
    let parameters: Parameter[] = []
    if ('sql' in source) {
      try {
        parameters = declarationsFromDrafts(declarations)
      } catch (caught) {
        setInspection(null)
        setError(caught instanceof Error ? caught.message : 'Check the parameter declarations.')
        return
      }
    }
    const controller = new AbortController()
    request.current = controller
    setInspection(null)
    setError('')
    setPhase('inspecting')
    try {
      const documents = nativeDocuments(securityEpoch)
      const options = { signal: controller.signal }
      const result = 'sql' in source
        ? await documents.inspectSql({ dataset_id: datasetId, source, ...(parameters.length ? { parameters } : {}) }, options)
        : 'model_id' in source
          ? await documents.inspectSemantic({ dataset_id: datasetId, source }, options)
          : await documents.inspectSource({ dataset_id: datasetId, source }, options)
      if (current(controller)) setInspection(result)
    } catch (caught) {
      if (current(controller)) setError(inspectionError(caught))
    } finally {
      if (current(controller)) setPhase('editing')
      if (request.current === controller) request.current = null
    }
  }

  async function save() {
    if (request.current) return
    if (!intent.current) {
      if (!source || !inspection || !title.trim()) return
      const body = dashboardDatasetCreateSchema.safeParse({ dataset: {
        dataset_id: datasetId, title: title.trim(), source,
        expected_schema_fingerprint: inspection.schema_fingerprint,
        ...('physical_id' in inspection.source ? { expected_physical_id: inspection.source.physical_id } : {}),
        // Save exactly the declarations the analyzer accepted with this schema.
        ...(savedParameters.length ? { parameters: savedParameters } : {}),
      } })
      if (!body.success) { setError('Check the dataset name and source before saving.'); return }
      intent.current = { body: body.data, operation_id: crypto.randomUUID(), expected_revision: dashboardRevision }
    }
    const command = intent.current
    const controller = new AbortController()
    request.current = controller
    setPhase('saving')
    setError('')
    let receipt: OperationReceipt | undefined
    try {
      receipt = await nativeDocuments(securityEpoch).createDashboardDataset(
        dashboardId, command.body, command, { signal: controller.signal },
      )
      if (receipt.base_revision !== command.expected_revision) throw new Error('Unexpected save revision')
      if (current(controller)) setPhase('saved')
    } catch (caught) {
      receipt = undefined
      if (!current(controller)) return
      if (caught instanceof ApiError && [401, 403, 404, 409, 422].includes(caught.status)) {
        intent.current = null
        setInspection(null)
        setPhase('editing')
        setConflict(caught.status === 409)
        setError(caught.status === 409
          ? 'The dashboard or source changed. Reload the dashboard, then inspect the source before saving again. Your selections are retained.'
          : [401, 403, 404].includes(caught.status)
            ? 'The dataset could not be saved with your current access.'
            : 'The dataset could not be saved. Review its source and inspect again.')
      } else {
        // Reuse this exact command: a lost response may follow a committed transaction.
        setPhase('uncertain')
        setError('The save result is not confirmed. Retry this save to recover its result before changing the dataset.')
      }
    } finally {
      if (request.current === controller) request.current = null
    }
    if (receipt && current(controller)) onSaved(receipt, datasetId)
  }

  if (phase === 'saved') return <div className='flex min-h-0 flex-1 flex-col gap-3'>
    <p role='status' className='text-sm'>Dataset saved to this dashboard.</p>
    <DatasetPreviewPane datasetId={datasetId} revision={1} draft title={title.trim()} parameters={savedParameters} />
  </div>

  return <section aria-label='Add dataset' className='min-w-0 space-y-4'>
    <div className='space-y-1'>
      <h3 className='text-base font-medium'>Add dataset</h3>
      <p className='text-sm text-muted-foreground'>Choose a source and inspect its columns. Saving adds the dataset to this dashboard.</p>
      {activeRole && <p className='break-words text-xs text-muted-foreground'>Current role: {activeRole}</p>}
    </div>
    <div className='space-y-1'>
      <Label htmlFor={titleId}>Dataset name</Label>
      <Input id={titleId} value={title} maxLength={256} disabled={locked}
        onChange={event => setTitle(event.target.value)} className='h-11 sm:h-9' />
    </div>
    <Select value={kind} disabled={locked} onValueChange={value => {
      setKind(value as SourceKind)
      select(value === 'sql' ? { kind: 'sql', database: '', sql: '' } : null)
      setDeclarations([])
    }}>
      <SelectTrigger aria-label='Dataset source type' className='h-11 w-full sm:h-9'><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value='object'>Table or view</SelectItem>
        <SelectItem value='sql'>SQL query</SelectItem>
        <SelectItem value='semantic'>Published Semantic View</SelectItem>
      </SelectContent>
    </Select>
    {kind === 'object' && <DatasetObjectPicker disabled={locked} onSelect={select} />}
    {kind === 'semantic' && <DatasetSemanticPicker disabled={locked} onSelect={select} />}
    {kind === 'sql' && source?.kind === 'sql' && <Suspense fallback={<LoadingLines rows={3} />}>
      <DatasetSqlEditor source={source} disabled={locked} onChange={select} />
    </Suspense>}
    {kind === 'sql' && <DatasetParameterDeclarations drafts={declarations} disabled={locked} onChange={declare} />}
    {phase === 'inspecting' && <p role='status' className='text-sm text-muted-foreground'>Inspecting source columns…</p>}
    {phase === 'saving' && <p role='status' className='text-sm text-muted-foreground'>Saving dataset…</p>}
    {error && <div role='alert' className='space-y-2 text-sm'>
      <p className='text-destructive'>{error}</p>
      {conflict && <Button type='button' variant='outline' onClick={onReloadDashboard}>Reload dashboard</Button>}
    </div>}
    {inspection && <DatasetInspectionPanel key={inspection.schema_fingerprint} inspection={inspection} />}
    <div className='flex flex-wrap gap-2'>
      <Button type='button' variant='outline' className='h-11 sm:h-9' disabled={locked || !inspectable}
        onClick={() => void inspect()}>{inspection ? 'Inspect again' : 'Inspect columns'}</Button>
      <Button type='button' className='h-11 sm:h-9'
        disabled={phase !== 'uncertain' && (locked || !inspection || !title.trim())}
        onClick={() => void save()}>{phase === 'uncertain' ? 'Retry save' : 'Save dataset'}</Button>
      <Button type='button' variant='ghost' className='h-11 sm:h-9' disabled={locked} onClick={onCancel}>Cancel</Button>
    </div>
  </section>
}
