import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { semanticViewsApi, type SemanticVersion } from '@/features/intelligence/semantic-views-api'
import { useAuthStore } from '@/stores/auth-store'
import type { SemanticSelection } from './contracts'

type Props = {
  disabled?: boolean
  onSelect: (source: SemanticSelection | null) => void
}

export function DatasetSemanticPicker(props: Props) {
  const securityEpoch = useAuthStore(state => state.securityEpoch)
  return <SemanticPicker key={securityEpoch} {...props} securityEpoch={securityEpoch} />
}

function SemanticPicker({ disabled, onSelect, securityEpoch }: Props & { securityEpoch: number }) {
  const [viewId, setViewId] = useState('')
  const views = useQuery({
    queryKey: ['native-dataviz', 'semantic-views', securityEpoch],
    queryFn: ({ signal }) => semanticViewsApi.list({ signal }),
    retry: false, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
  const detail = useQuery({
    queryKey: ['native-dataviz', 'semantic-view', securityEpoch, viewId],
    queryFn: ({ signal }) => semanticViewsApi.get(viewId, signal),
    enabled: Boolean(viewId),
    retry: false, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
  const published = (views.data ?? []).filter(view => view.active_version !== null && view.status === 'ACTIVE')
  const current = detail.data?.id === viewId && detail.data.status === 'ACTIVE'
    ? detail.data.versions.find(version => version.version === detail.data?.active_version
      && version.status === 'ACTIVE' && /^[a-f0-9]{64}$/.test(version.fingerprint)) : undefined

  return <div className='min-w-0 space-y-3'>
    <Select value={viewId} disabled={disabled || views.isPending || views.isError} onValueChange={value => {
      setViewId(value)
      onSelect(null)
    }}>
      <SelectTrigger aria-label='Published Semantic View' className='h-11 w-full sm:h-9'>
        <SelectValue placeholder='Choose a published Semantic View' />
      </SelectTrigger>
      <SelectContent>{published.map(view => <SelectItem key={view.id} value={view.id}>
        {view.name} · {view.database_name}
      </SelectItem>)}</SelectContent>
    </Select>
    {views.isPending || (viewId && detail.isPending)
      ? <p role='status' className='text-sm text-muted-foreground'>Loading Semantic Views…</p>
      : views.isError || detail.isError
        ? <div role='alert' className='space-y-2 text-sm'>
            <p className='text-destructive'>Semantic Views could not be loaded with your current access.</p>
            <Button type='button' variant='outline' disabled={disabled || views.isFetching || detail.isFetching}
              onClick={() => { onSelect(null); if (views.isError) void views.refetch(); else void detail.refetch() }}>
              Retry loading Semantic Views
            </Button>
          </div>
        : !published.length
          ? <p role='status' className='text-sm text-muted-foreground'>No published Semantic Views are available with your current role.</p>
          : viewId && !current
            ? <p role='alert' className='text-sm text-destructive'>This Semantic View no longer has an available published version. Choose another source.</p>
            : null}
    {current && !detail.isError && <SemanticFields
      key={`${viewId}:${current.version}:${current.fingerprint}`} viewId={viewId}
      version={current} disabled={disabled} onSelect={onSelect} />}
  </div>
}

function SemanticFields({ viewId, version, disabled, onSelect }: Props & {
  viewId: string
  version: SemanticVersion
}) {
  const [metrics, setMetrics] = useState<string[]>([])
  const [dimensions, setDimensions] = useState<string[]>([])
  const [filters, setFilters] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const definition = version.definition
  const groups = [
    { label: 'Metrics', names: (definition.metrics ?? []).map(metric => metric.name),
      selected: metrics, set: setMetrics, limit: 64 },
    { label: 'Dimensions', names: (definition.datasets ?? []).flatMap(dataset => (dataset.fields ?? [])
      .filter(field => field.kind === 'dimension' || field.dimension
        || ['Date', 'Time', 'DateTime', 'DateTimeTz'].includes(field.datatype ?? ''))
      .map(field => `${dataset.name}.${field.name}`)), selected: dimensions, set: setDimensions, limit: 32 },
    { label: 'Named filters', names: (definition.named_filters ?? []).map(filter => filter.name),
      selected: filters, set: setFilters, limit: 32 },
  ]

  function toggle(group: typeof groups[number], name: string) {
    const next = group.selected.includes(name) ? group.selected.filter(item => item !== name) : [...group.selected, name]
    group.set(next)
    const selectedMetrics = group.label === 'Metrics' ? next : metrics
    onSelect(selectedMetrics.length ? {
      kind: 'semantic_model', model_id: viewId, model_revision: version.version,
      model_fingerprint: version.fingerprint, metrics: selectedMetrics,
      dimensions: group.label === 'Dimensions' ? next : dimensions,
      named_filters: group.label === 'Named filters' ? next : filters,
    } : null)
  }

  return <div className='space-y-3'>
    <p className='text-sm text-muted-foreground'>Published version {version.version}. Choose at least one metric; dimensions define the breakdown.</p>
    <Input aria-label='Search semantic fields' value={search} disabled={disabled}
      placeholder='Search metrics, dimensions and filters' onChange={event => setSearch(event.target.value)} />
    <div className='grid gap-3 md:grid-cols-3'>
      {groups.map(group => <fieldset key={group.label} className='min-w-0 rounded-md border p-2'>
        <legend className='px-1 text-sm font-medium'>{group.label} ({group.selected.length})</legend>
        <div className='max-h-60 overflow-y-auto'>
          {group.names.filter(name => name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
            .map(name => <Label key={name} className='flex min-h-11 cursor-pointer items-center gap-2 py-2 text-sm font-normal'>
              <Checkbox aria-label={`${group.label}: ${name}`} checked={group.selected.includes(name)}
                disabled={disabled || (!group.selected.includes(name) && group.selected.length >= group.limit)}
                onCheckedChange={() => toggle(group, name)} />
              <span className='min-w-0 break-words'>{name}</span>
            </Label>)}
          {!group.names.some(name => name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
            && <p className='py-2 text-xs text-muted-foreground'>No matching fields.</p>}
        </div>
      </fieldset>)}
    </div>
  </div>
}
