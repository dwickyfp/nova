import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ChevronDown, Filter, Plus, Search, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import type {
  DashboardDefinition, DatasetField, DatasetReferenceSummary,
} from '../contracts'
import { nativeDocuments } from '../documents'
import { cellLabel, type Cell } from '../charts/chart-query'
import { useDatasetDefinition } from '../charts/chart-data'
import { isNumeric, isTemporal } from '../charts/builder-model'

import { predicate, type CrossFilter, type FilterDefinition, type FilterValue } from './filter-model'

export type { CrossFilter, FilterDefinition, FilterValue }
type Scalar = NonNullable<Cell>

function describe(value: FilterValue | undefined, field?: DatasetField): string {
  if (!value) return 'All'
  switch (value.kind) {
    case 'members': return value.values.length ? value.values.length === 1 ? cellLabel(value.values[0]) : `${value.values.length} selected` : 'All'
    case 'range': return value.from || value.to ? `${value.from ? cellLabel(value.from) : '…'} – ${value.to ? cellLabel(value.to) : '…'}` : 'All'
    case 'text': return value.value ? `Contains “${value.value}”` : 'All'
    case 'relative': return `${value.mode === 'previous' ? 'Previous' : value.mode === 'to_date' ? 'This' : 'Last'} ${value.mode === 'to_date' ? value.unit : `${value.count} ${value.unit}${value.count > 1 ? 's' : ''}`}${value.mode === 'to_date' ? ' to date' : ''}`
    case 'boolean': return value.value ? 'True' : 'False'
  }
  return field ? field.name : 'All'
}

function useFilterOptions(filter: FilterDefinition, search: string, enabled: boolean, draft: boolean) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const field = filter.field
  return useQuery({
    queryKey: ['native-dataviz', 'filter-options', epoch, field?.dataset_id, field?.dataset_revision, field?.field_id, search, draft],
    queryFn: ({ signal }) => nativeDocuments(epoch).queryDataset(field!.dataset_id, {
      revision: field!.dataset_revision, draft, dimensions: [{ field_id: field!.field_id }], limit: 200,
      ...(search.trim() ? { filters: { expression: predicate(field!, 'like_literal', [{ type: 'string', value: search.trim() }]) } } : {}),
    }, { signal }),
    enabled: enabled && Boolean(field),
    staleTime: 60_000, retry: false,
  })
}

function MembersControl({ filter, value, onChange, field, draft }: ControlProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const text = field?.logical_type === 'string'
  const options = useFilterOptions(filter, text ? search : '', open, draft)
  const selected = value?.kind === 'members' ? value.values : []
  const key = (cell: Scalar | null) => cell === null ? 'null' : `${cell.type}:${String(cell.value)}`
  const single = filter.kind === 'single_value'
  const toggle = (cell: Scalar | null) => {
    const exists = selected.some(item => key(item) === key(cell))
    const next = single ? (exists ? [] : [cell]) : exists ? selected.filter(item => key(item) !== key(cell)) : [...selected, cell]
    onChange(next.length ? { kind: 'members', values: next } : undefined)
    if (single) setOpen(false)
  }
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild>
      <Button variant='outline' size='sm' className='h-9 max-w-64 justify-between gap-2' aria-label={`${filter.title} filter`}>
        <span className='truncate'><span className='text-muted-foreground'>{filter.title}:</span> {describe(value, field)}</span>
        <ChevronDown className='size-4 opacity-60' />
      </Button>
    </PopoverTrigger>
    <PopoverContent align='start' className='w-72 p-0'>
      {text && <div className='flex items-center gap-2 border-b px-3'>
        <Search className='size-4 text-muted-foreground' aria-hidden />
        <Input aria-label={`Search ${filter.title}`} value={search} onChange={event => setSearch(event.target.value)}
          placeholder='Search values' className='h-10 border-0 px-0 shadow-none focus-visible:ring-0' />
      </div>}
      <div role='listbox' aria-label={`${filter.title} values`} aria-multiselectable={!single} className='max-h-72 overflow-y-auto p-1'>
        {options.isPending ? <p role='status' className='p-3 text-sm text-muted-foreground'>Loading values…</p>
          : options.isError ? <p role='alert' className='p-3 text-sm text-muted-foreground'>Values are unavailable with your current role.</p>
            : !options.data.rows.length ? <p className='p-3 text-sm text-muted-foreground'>No values found.</p>
              : options.data.rows.map(row => {
                const cell = row[0]
                const active = selected.some(item => key(item) === key(cell))
                return <button key={key(cell)} type='button' role='option' aria-selected={active} onClick={() => toggle(cell)}
                  className='flex min-h-9 w-full items-center gap-2 rounded px-2 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none'>
                  <span className={cn('flex size-4 items-center justify-center rounded-sm border', active && 'border-primary bg-primary text-primary-foreground')}>
                    {active && <Check className='size-3' />}
                  </span>
                  <span className='truncate'>{cellLabel(cell)}</span>
                </button>
              })}
        {options.data?.truncated && <p className='px-2 py-1 text-xs text-muted-foreground'>Showing the first 200 values. Search to narrow the list.</p>}
      </div>
      {selected.length > 0 && <div className='border-t p-1'>
        <Button variant='ghost' size='sm' className='w-full' onClick={() => { onChange(undefined); setOpen(false) }}>Clear selection</Button>
      </div>}
    </PopoverContent>
  </Popover>
}

function RangeControl({ filter, value, onChange, field }: ControlProps) {
  const range = value?.kind === 'range' ? value : { kind: 'range' as const, from: null, to: null }
  const date = isTemporal(field)
  const scalar = (text: string): Scalar | null => {
    if (!text) return null
    if (date) return field?.logical_type === 'timestamp' ? { type: 'timestamp', value: `${text}T00:00:00Z` } : { type: 'date', value: text }
    return /^-?\d+(\.\d+)?$/.test(text) ? (text.includes('.') ? { type: 'decimal', value: text } : { type: 'integer', value: text }) : null
  }
  const raw = (cell: Scalar | null) => cell ? String(cell.value).slice(0, date ? 10 : undefined) : ''
  const update = (side: 'from' | 'to', text: string) => {
    const next = { ...range, [side]: scalar(text) }
    onChange(next.from || next.to ? next : undefined)
  }
  return <fieldset className='flex items-center gap-1 rounded-md border px-2'>
    <legend className='sr-only'>{filter.title}</legend>
    <span className='text-sm text-muted-foreground'>{filter.title}</span>
    <Input type={date ? 'date' : 'number'} aria-label={`${filter.title} from`} value={raw(range.from)}
      onChange={event => update('from', event.target.value)} className='h-8 w-36 border-0 shadow-none' />
    <span className='text-muted-foreground'>–</span>
    <Input type={date ? 'date' : 'number'} aria-label={`${filter.title} to`} value={raw(range.to)}
      onChange={event => update('to', event.target.value)} className='h-8 w-36 border-0 shadow-none' />
  </fieldset>
}

const RELATIVE_PRESETS: { label: string; value: Extract<FilterValue, { kind: 'relative' }> }[] = [
  { label: 'Last 7 days', value: { kind: 'relative', unit: 'day', count: 7, mode: 'rolling' } },
  { label: 'Last 30 days', value: { kind: 'relative', unit: 'day', count: 30, mode: 'rolling' } },
  { label: 'Month to date', value: { kind: 'relative', unit: 'month', count: 1, mode: 'to_date' } },
  { label: 'Previous month', value: { kind: 'relative', unit: 'month', count: 1, mode: 'previous' } },
  { label: 'Quarter to date', value: { kind: 'relative', unit: 'quarter', count: 1, mode: 'to_date' } },
  { label: 'Previous quarter', value: { kind: 'relative', unit: 'quarter', count: 1, mode: 'previous' } },
  { label: 'Year to date', value: { kind: 'relative', unit: 'year', count: 1, mode: 'to_date' } },
  { label: 'Last 12 months', value: { kind: 'relative', unit: 'month', count: 12, mode: 'rolling' } },
]

function RelativeControl({ filter, value, onChange }: ControlProps) {
  const current = value?.kind === 'relative' ? RELATIVE_PRESETS.findIndex(item => JSON.stringify(item.value) === JSON.stringify(value)) : -1
  return <Select value={current >= 0 ? String(current) : 'all'} onValueChange={next => onChange(next === 'all' ? undefined : RELATIVE_PRESETS[Number(next)].value)}>
    <SelectTrigger aria-label={`${filter.title} filter`} className='h-9 w-auto min-w-44'>
      <span className='truncate'><span className='text-muted-foreground'>{filter.title}:</span> <SelectValue /></span>
    </SelectTrigger>
    <SelectContent>
      <SelectItem value='all'>All time</SelectItem>
      {RELATIVE_PRESETS.map((item, index) => <SelectItem key={item.label} value={String(index)}>{item.label}</SelectItem>)}
    </SelectContent>
  </Select>
}

function TextControl({ filter, value, onChange }: ControlProps) {
  return <div className='relative'>
    <Search className='pointer-events-none absolute top-2.5 left-2 size-4 text-muted-foreground' aria-hidden />
    <Input aria-label={`${filter.title} contains`} placeholder={`${filter.title} contains…`} className='h-9 w-56 pl-8'
      value={value?.kind === 'text' ? value.value : ''} onChange={event => onChange(event.target.value ? { kind: 'text', value: event.target.value } : undefined)} />
  </div>
}

function BooleanControl({ filter, value, onChange }: ControlProps) {
  const current = value?.kind === 'boolean' ? String(value.value) : 'all'
  return <Select value={current} onValueChange={next => onChange(next === 'all' ? undefined : { kind: 'boolean', value: next === 'true' })}>
    <SelectTrigger aria-label={`${filter.title} filter`} className='h-9 w-auto min-w-36'>
      <span className='truncate'><span className='text-muted-foreground'>{filter.title}:</span> <SelectValue /></span>
    </SelectTrigger>
    <SelectContent><SelectItem value='all'>All</SelectItem><SelectItem value='true'>True</SelectItem><SelectItem value='false'>False</SelectItem></SelectContent>
  </Select>
}

type ControlProps = {
  filter: FilterDefinition; value: FilterValue | undefined; onChange: (value: FilterValue | undefined) => void
  field?: DatasetField; draft: boolean
}

function FilterControl(props: Omit<ControlProps, 'field'>) {
  const definition = useDatasetDefinition(props.filter.field?.dataset_id, props.filter.field?.dataset_revision)
  const field = (definition.data?.fields as DatasetField[] | undefined)?.find(item => item.field_id === props.filter.field?.field_id)
  const control = { ...props, field }
  switch (props.filter.kind) {
    case 'date_range': case 'number_range_slider': case 'date_picker': return <RangeControl {...control} />
    case 'relative_date': return <RelativeControl {...control} />
    case 'text_entry': return <TextControl {...control} />
    case 'boolean_switch': return <BooleanControl {...control} />
    default: return <MembersControl {...control} />
  }
}

export function FilterBar({ definition, values, onChange, crossFilters, onClearCross, editing, onEdit, draft }: {
  definition: DashboardDefinition
  values: Record<string, FilterValue>
  onChange: (filterId: string, value: FilterValue | undefined) => void
  crossFilters: CrossFilter[]
  onClearCross: (index: number | null) => void
  editing: boolean
  onEdit: () => void
  draft: boolean
}) {
  const filters = definition.filters ?? []
  if (!filters.length && !crossFilters.length && !editing) return null
  const active = Object.keys(values).length
  return <div role='toolbar' aria-label='Dashboard filters' className='flex shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2'>
    <Filter className='size-4 text-muted-foreground' aria-hidden />
    {filters.map(filter => <FilterControl key={filter.filter_id} filter={filter} value={values[filter.filter_id]} draft={draft}
      onChange={value => onChange(filter.filter_id, value)} />)}
    {crossFilters.map((cross, index) => <Badge key={`${cross.source_widget_id}:${index}`} variant='secondary' className='h-8 gap-1 pr-1'>
      <span className='text-muted-foreground'>Selected:</span> {cross.label}
      <Button variant='ghost' size='icon' className='size-6' aria-label={`Clear selection ${cross.label}`} onClick={() => onClearCross(index)}><X /></Button>
    </Badge>)}
    {(active > 0 || crossFilters.length > 0) && <Button variant='ghost' size='sm' onClick={() => {
      for (const filter of filters) onChange(filter.filter_id, undefined)
      onClearCross(null)
    }}>Reset</Button>}
    {editing && <Button variant='ghost' size='sm' onClick={onEdit}><Plus />{filters.length ? 'Edit filters' : 'Add filter'}</Button>}
  </div>
}

const KINDS: { value: FilterDefinition['kind']; label: string; description: string; fits: (field?: DatasetField) => boolean }[] = [
  { value: 'multi_value', label: 'Multiple values', description: 'Pick one or more values from a list', fits: field => !isNumeric(field) || field?.logical_type === 'integer' },
  { value: 'single_value', label: 'Single value', description: 'Pick exactly one value', fits: () => true },
  { value: 'date_range', label: 'Date range', description: 'Between a start and end date', fits: isTemporal },
  { value: 'relative_date', label: 'Relative date', description: 'Last 7 days, month to date, …', fits: isTemporal },
  { value: 'number_range_slider', label: 'Number range', description: 'Between two numbers', fits: isNumeric },
  { value: 'text_entry', label: 'Text search', description: 'Rows whose text contains a word', fits: field => field?.logical_type === 'string' },
  { value: 'boolean_switch', label: 'True / false', description: 'Filter a yes/no field', fits: field => field?.logical_type === 'boolean' },
]

/** Author dashboard filters; applying them commits one filters operation with CAS. */
export function FilterBuilder({ open, onOpenChange, definition, datasets, saving, error, onApply }: {
  open: boolean; onOpenChange: (open: boolean) => void; definition: DashboardDefinition
  datasets: DatasetReferenceSummary[]; saving: boolean; error?: string
  onApply: (filters: FilterDefinition[]) => void
}) {
  const [items, setItems] = useState<FilterDefinition[]>(() => [...(definition.filters ?? [])])
  const [datasetKey, setDatasetKey] = useState(() => {
    const first = datasets.find(item => item.title !== null)
    return first ? `${first.dataset_id}:${first.revision}` : ''
  })
  const [dataset_id, revision] = datasetKey.split(':')
  const fields = (useDatasetDefinition(dataset_id || undefined, revision ? Number(revision) : undefined).data?.fields ?? []) as DatasetField[]
  const [fieldId, setFieldId] = useState('')
  const field = fields.find(item => item.field_id === fieldId)
  const [kind, setKind] = useState<FilterDefinition['kind']>('multi_value')
  const [title, setTitle] = useState('')
  const usable = useMemo(() => KINDS.filter(item => item.fits(field)), [field])
  const add = () => {
    if (!field || !dataset_id) return
    const binding = { dataset_id, dataset_revision: Number(revision), field_id: field.field_id }
    const next: FilterDefinition = {
      filter_id: crypto.randomUUID(), title: title.trim() || field.name, kind, field: binding,
      scope: { kind: 'global' }, mappings: [], defaults: [], allow_all: true, include_null: false, searchable: true, depends_on: [],
      ...(kind === 'relative_date' ? { relative: { unit: 'month', count: 1, mode: 'to_date', timezone: 'UTC', week_start: 'iso', fiscal_start_month: 1 } } : {}),
    } as FilterDefinition
    setItems([...items, next])
    setFieldId(''); setTitle('')
  }
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className='max-h-[90vh] overflow-y-auto sm:max-w-lg'>
      <DialogHeader>
        <DialogTitle>Dashboard filters</DialogTitle>
        <DialogDescription>Filters apply to every chart built on the same dataset. They narrow results; they never widen what your role can read.</DialogDescription>
      </DialogHeader>
      <ul aria-label='Current filters' className='space-y-1'>
        {items.map(item => <li key={item.filter_id} className='flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm'>
          <span className='min-w-0 truncate'>{item.title} <span className='text-muted-foreground'>· {KINDS.find(k => k.value === item.kind)?.label ?? item.kind}</span></span>
          <Button variant='ghost' size='icon' className='size-8' aria-label={`Remove ${item.title}`} onClick={() => setItems(items.filter(other => other.filter_id !== item.filter_id))}><X /></Button>
        </li>)}
        {!items.length && <li className='text-sm text-muted-foreground'>No filters yet.</li>}
      </ul>
      <div className='space-y-3 rounded-md border p-3'>
        <h4 className='text-sm font-medium'>Add a filter</h4>
        <div className='grid gap-3 sm:grid-cols-2'>
          <div className='space-y-1'>
            <Label className='text-xs text-muted-foreground'>Dataset</Label>
            <Select value={datasetKey} onValueChange={value => { setDatasetKey(value); setFieldId('') }}>
              <SelectTrigger aria-label='Filter dataset' className='h-9 w-full'><SelectValue placeholder='Choose a dataset' /></SelectTrigger>
              <SelectContent>{datasets.filter(item => item.title !== null).map(item =>
                <SelectItem key={item.dataset_id} value={`${item.dataset_id}:${item.revision}`}>{item.title}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className='space-y-1'>
            <Label className='text-xs text-muted-foreground'>Field</Label>
            <Select value={fieldId} onValueChange={value => {
              setFieldId(value)
              const chosen = fields.find(item => item.field_id === value)
              setKind(isTemporal(chosen) ? 'date_range' : isNumeric(chosen) ? 'number_range_slider' : chosen?.logical_type === 'boolean' ? 'boolean_switch' : 'multi_value')
            }}>
              <SelectTrigger aria-label='Filter field' className='h-9 w-full'><SelectValue placeholder='Choose a field' /></SelectTrigger>
              <SelectContent>{fields.map(item => <SelectItem key={item.field_id} value={item.field_id}>{item.name}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </div>
        {field && <>
          <div role='radiogroup' aria-label='Filter type' className='grid gap-1.5 sm:grid-cols-2'>
            {usable.map(item => <button key={item.value} type='button' role='radio' aria-checked={kind === item.value}
              onClick={() => setKind(item.value)}
              className={cn('rounded-md border p-2 text-left text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                kind === item.value ? 'border-primary bg-primary/10' : 'hover:bg-muted')}>
              <span className='block font-medium'>{item.label}</span>
              <span className='block text-xs text-muted-foreground'>{item.description}</span>
            </button>)}
          </div>
          <div className='space-y-1'>
            <Label className='text-xs text-muted-foreground'>Label</Label>
            <Input value={title} maxLength={256} placeholder={field.name} onChange={event => setTitle(event.target.value)} className='h-9' aria-label='Filter label' />
          </div>
          <Button variant='outline' onClick={add} disabled={items.length >= 64}><Plus />Add {KINDS.find(k => k.value === kind)?.label.toLowerCase()} filter</Button>
        </>}
      </div>
      {error && <p role='alert' className='text-sm text-destructive'>{error}</p>}
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button disabled={saving} onClick={() => onApply(items)}>{saving ? 'Saving…' : 'Apply filters'}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
