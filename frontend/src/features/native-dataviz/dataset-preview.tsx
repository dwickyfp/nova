import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import type { Parameter, ParameterInput } from './contracts'
import { DataTable } from './data-table'
import { DatasetParameterInputs } from './dataset-parameter-inputs'
import { nativeDocuments } from './documents'
import { initialParameterDrafts, resolveParameterInputs } from './parameter-values'

type DatasetPreviewProps = {
  datasetId: string
  revision: number
  draft: boolean
  title: string
  parameters?: Parameter[]
}

function previewError(error: Error): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return 'The dataset or its source changed. Inspect the source and reload the dataset.'
    if ([401, 403, 404].includes(error.status)) return 'This dataset is unavailable with your current access.'
    if (error.status === 429) return 'Query capacity is busy. Try the preview again shortly.'
    if (error.status === 504) return 'The preview timed out. Reduce the row limit and try again.'
    if (error.status === 422) return 'The preview could not be completed. Check the source and try a smaller row limit.'
  }
  return 'The preview could not be loaded. Try again.'
}

export function DatasetPreviewPane(props: DatasetPreviewProps) {
  const securityEpoch = useAuthStore(state => state.securityEpoch)
  return <PreviewScope key={`${props.datasetId}:${props.revision}:${props.draft}:${securityEpoch}`}
    {...props} securityEpoch={securityEpoch} />
}

function PreviewScope({ datasetId, revision, draft, title, parameters = [], securityEpoch }: DatasetPreviewProps & {
  securityEpoch: number
}) {
  const labelId = useId()
  const [requested, setRequested] = useState(false)
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [scopeId] = useState(() => crypto.randomUUID())
  const [generation, setGeneration] = useState(0)
  const [drafts, setDrafts] = useState(() => initialParameterDrafts(parameters))
  const [submitted, setSubmitted] = useState<ParameterInput[]>([])
  const [inputError, setInputError] = useState('')
  const query = useQuery({
    queryKey: ['native-dataviz', 'preview', securityEpoch, datasetId, revision, draft, scopeId, generation, offset, limit],
    queryFn: ({ signal }) => nativeDocuments(securityEpoch).previewDataset(
      datasetId, { revision, draft, offset, limit, ...(submitted.length ? { parameters: submitted } : {}) }, { signal },
    ),
    enabled: requested,
    retry: false,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  const data = query.data
  const range = data && (data.rows.length
    ? `Rows ${data.offset + 1}–${data.offset + data.rows.length}` : '0 rows')

  function runPreview() {
    try {
      setSubmitted(resolveParameterInputs(parameters, drafts))
      setInputError('')
    } catch (error) {
      setInputError(error instanceof Error ? error.message : 'Check the preview parameters.')
      return
    }
    if (requested) void query.refetch()
    else setRequested(true)
  }

  return <section aria-label={`${title} preview`} className='flex min-h-0 min-w-0 flex-1 flex-col gap-2'>
    <DatasetParameterInputs parameters={parameters} drafts={drafts} onChange={next => {
      setDrafts(next)
      setSubmitted([])
      setRequested(false)
      setGeneration(value => value + 1)
      setOffset(0)
      setInputError('')
    }} />
    {inputError && <p role='alert' className='text-sm text-destructive'>{inputError}</p>}
    <div className='flex shrink-0 flex-wrap items-center gap-2'>
      <span id={labelId} className='text-sm text-muted-foreground'>Row limit</span>
      <Select value={String(limit)} disabled={query.isFetching} onValueChange={value => {
        setOffset(0)
        setLimit(Number(value))
      }}>
        <SelectTrigger aria-labelledby={labelId} className='w-auto min-w-20'>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {[25, 50, 100, 200].map(value => <SelectItem key={value} value={String(value)}>{value}</SelectItem>)}
        </SelectContent>
      </Select>
      <Button variant='outline' onClick={runPreview} disabled={query.isFetching || parameters.some(parameter => parameter.sensitive)}>
        {requested ? 'Refresh preview' : 'Run preview'}
      </Button>
      {query.isFetching && <span role='status' className='text-sm text-muted-foreground'>Loading preview…</span>}
    </div>
    {query.isError ? <div role='alert' className='min-h-0 flex-1 overflow-auto p-4 text-sm'>
      {previewError(query.error)}
    </div> : query.isFetching ? <div aria-busy='true' className='min-h-0 flex-1' />
      : data ? <DataTable fields={data.fields} rows={data.rows} label={`${title} rows`}
        contextKey={`${datasetId}:${revision}:${draft}:${securityEpoch}:${offset}:${limit}`} />
        : <p className='min-h-0 flex-1 p-4 text-sm text-muted-foreground'>
          Run a preview to read this dataset with your current role.
        </p>}
    {data && !query.isError && !query.isFetching && <div className='flex shrink-0 flex-wrap items-center gap-2 text-sm'>
      <span role='status' className='me-auto text-muted-foreground'>{range}</span>
      {data.has_more && data.next_offset === null && <span className='text-muted-foreground'>Preview limit reached.</span>}
      <Button variant='outline' disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</Button>
      <Button variant='outline' disabled={typeof data.next_offset !== 'number'}
        onClick={() => { if (typeof data.next_offset === 'number') setOffset(data.next_offset) }}>Next</Button>
    </div>}
  </section>
}
