import { useId, useMemo, useState } from 'react'
import {
  AreaChart, BarChart3, Calendar, Filter, Gauge, Grid3x3, Hash, LineChart, PieChart, Plus,
  MapPin, ScatterChart, SquareStack, Table2, Trash2, TrendingUp, Type, Workflow, X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import type { ChartDefinition, ConditionalFormat, DatasetField, DatasetReferenceSummary } from '../contracts'
import { useDatasetDefinition } from './chart-data'
import {
  AGGREGATION_LABELS, canCalculate, canCombineOther, defaultAggregation, fromChart, initialState, isNumeric, isTemporal, kindOf, missing,
  switchVisual, toChart, VISUALS, type BuilderDimension, type BuilderMeasure, type BuilderState, type Visual,
} from './builder-model'
import { CALCULATION_LABELS, type CalculationKind } from './calculations'
import { humanize, type DateGrain, type SupportedAggregation } from './chart-query'
import { NativeChart } from './native-chart'

const VISUAL_ICONS: Record<string, typeof BarChart3> = {
  kpi: Hash, bar: BarChart3, line: LineChart, area: AreaChart, combo: TrendingUp, pie: PieChart, funnel: Filter,
  table: Table2, pivot: Grid3x3, scatter: ScatterChart, heatmap: Grid3x3, treemap: SquareStack, sankey: Workflow,
  histogram: BarChart3, gauge: Gauge, map: MapPin,
}
const GRAINS: DateGrain[] = ['day', 'week', 'month', 'quarter', 'year']
const NONE = '__none__'

function FieldIcon({ field }: { field?: DatasetField }) {
  if (!field) return null
  const Icon = isNumeric(field) ? Hash : isTemporal(field) ? Calendar : Type
  return <Icon className={cn('size-3.5 shrink-0', isNumeric(field) ? 'text-pill-continuous-foreground' : 'text-pill-discrete-foreground')} aria-hidden />
}

function FieldSelect({ label, fields, value, onChange, allowNone, filter }: {
  label: string; fields: DatasetField[]; value: string; onChange: (value: string) => void
  allowNone?: string; filter?: (field: DatasetField) => boolean
}) {
  const id = useId()
  const options = filter ? fields.filter(filter) : fields
  return <div className='min-w-0 flex-1 space-y-1'>
    <Label htmlFor={id} className='text-xs text-muted-foreground'>{label}</Label>
    <Select value={value || (allowNone ? NONE : '')} onValueChange={next => onChange(next === NONE ? '' : next)}>
      <SelectTrigger id={id} className='h-9 w-full min-w-0'><SelectValue placeholder='Choose a field' /></SelectTrigger>
      <SelectContent>
        {allowNone && <SelectItem value={NONE}>{allowNone}</SelectItem>}
        {options.map(field => <SelectItem key={field.field_id} value={field.field_id}>
          <span className='flex min-w-0 items-center gap-2'><FieldIcon field={field} /><span className='truncate'>{field.name}</span></span>
        </SelectItem>)}
      </SelectContent>
    </Select>
  </div>
}

function DimensionRow({ label, fields, value, onChange, onRemove }: {
  label: string; fields: DatasetField[]; value: BuilderDimension; onChange: (value: BuilderDimension) => void; onRemove?: () => void
}) {
  const field = fields.find(item => item.field_id === value.field_id)
  return <div className='flex min-w-0 items-end gap-2'>
    <FieldSelect label={label} fields={fields} value={value.field_id}
      onChange={field_id => onChange({ field_id, date_grain: isTemporal(fields.find(f => f.field_id === field_id)) ? 'month' : null })} />
    {isTemporal(field) && <div className='w-28 shrink-0 space-y-1'>
      <Label className='text-xs text-muted-foreground'>Group by</Label>
      <Select value={value.date_grain ?? NONE} onValueChange={grain => onChange({ ...value, date_grain: grain === NONE ? null : grain as DateGrain })}>
        <SelectTrigger aria-label={`${label} date grain`} className='h-9 w-full'><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE}>Exact</SelectItem>
          {GRAINS.map(grain => <SelectItem key={grain} value={grain}>{grain[0].toUpperCase() + grain.slice(1)}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>}
    {onRemove && <Button variant='ghost' size='icon' className='size-9 shrink-0' aria-label={`Remove ${label}`} onClick={onRemove}><Trash2 /></Button>}
  </div>
}

function MeasureRow({ label, fields, value, onChange, onRemove, combo, calculable }: {
  label: string; fields: DatasetField[]; value: BuilderMeasure; onChange: (value: BuilderMeasure) => void
  onRemove?: () => void; combo?: boolean; calculable?: boolean
}) {
  const field = fields.find(item => item.field_id === value.field_id)
  const aggregations = (Object.keys(AGGREGATION_LABELS) as SupportedAggregation[]).filter(item =>
    isNumeric(field) || !['sum', 'avg'].includes(item))
  return <div className='space-y-2'>
    <div className='flex min-w-0 items-end gap-2'>
      <FieldSelect label={label} fields={fields} value={value.field_id}
        onChange={field_id => onChange({ ...value, field_id, aggregation: defaultAggregation(fields.find(f => f.field_id === field_id)) })} />
      <div className='w-32 shrink-0 space-y-1'>
        <Label className='text-xs text-muted-foreground'>Aggregate</Label>
        <Select value={value.aggregation} onValueChange={aggregation => onChange({ ...value, aggregation: aggregation as SupportedAggregation })}>
          <SelectTrigger aria-label={`${label} aggregation`} className='h-9 w-full'><SelectValue /></SelectTrigger>
          <SelectContent>{aggregations.map(item => <SelectItem key={item} value={item}>{AGGREGATION_LABELS[item]}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      {onRemove && <Button variant='ghost' size='icon' className='size-9 shrink-0' aria-label={`Remove ${label}`} onClick={onRemove}><Trash2 /></Button>}
    </div>
    {calculable && <div className='flex items-end gap-2'>
      <div className='min-w-0 flex-1 space-y-1'>
        <Label className='text-xs text-muted-foreground'>Show as</Label>
        <Select value={value.showAs ?? NONE} onValueChange={kind => onChange({ ...value, showAs: kind === NONE ? null : kind as CalculationKind })}>
          <SelectTrigger aria-label={`${label} shown as`} className='h-8 w-full'><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>Value</SelectItem>
            {(Object.keys(CALCULATION_LABELS) as CalculationKind[]).map(kind => <SelectItem key={kind} value={kind}>{CALCULATION_LABELS[kind]}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      {value.showAs === 'moving_average' && <div className='w-24 shrink-0 space-y-1'>
        <Label className='text-xs text-muted-foreground'>Rows</Label>
        <Input type='number' min={2} max={1000} value={value.window ?? 3} aria-label={`${label} moving average rows`} className='h-8'
          onChange={event => onChange({ ...value, window: Math.max(2, Math.min(1000, Number(event.target.value) || 3)) })} />
      </div>}
    </div>}
    {combo && <div className='flex gap-2'>
      <Select value={value.series} onValueChange={series => onChange({ ...value, series: series as BuilderMeasure['series'] })}>
        <SelectTrigger aria-label={`${label} mark`} className='h-8 w-28'><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value='bar'>Bars</SelectItem><SelectItem value='line'>Line</SelectItem><SelectItem value='area'>Area</SelectItem></SelectContent>
      </Select>
      <Select value={value.axis} onValueChange={axis => onChange({ ...value, axis: axis as BuilderMeasure['axis'] })}>
        <SelectTrigger aria-label={`${label} axis`} className='h-8 w-32'><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value='left'>Left axis</SelectItem><SelectItem value='right'>Right axis</SelectItem></SelectContent>
      </Select>
    </div>}
  </div>
}

const TONE_LABELS: Record<NonNullable<ConditionalFormat['tone']>, string> = {
  accent: 'Blue', positive: 'Green', negative: 'Red', warning: 'Amber',
}
const OPERATOR_LABELS: Record<NonNullable<ConditionalFormat['operator']>, string> = {
  gt: 'Above', gte: 'At least', lt: 'Below', lte: 'At most', eq: 'Equal to',
}

/** Shading rules for table and pivot values: a colour scale, or a highlight when a value passes a limit. */
function FormatRules({ state, fields, onChange }: { state: BuilderState; fields: DatasetField[]; onChange: (formats: ConditionalFormat[]) => void }) {
  const measures = state.measures.filter((item, index, all) => all.findIndex(other => other.field_id === item.field_id) === index)
  const name = (fieldId: string) => fields.find(field => field.field_id === fieldId)?.name ?? fieldId
  const update = (index: number, change: Partial<ConditionalFormat>) =>
    onChange(state.formats.map((item, i) => i === index ? { ...item, ...change } as ConditionalFormat : item))
  if (!measures.length) return <p className='text-xs text-muted-foreground'>Add a value first; formatting shades values.</p>
  return <div className='space-y-3'>
    {state.formats.map((format, index) => <div key={index} className='space-y-2 rounded-md border p-2.5'>
      <div className='flex items-center gap-2'>
        <Select value={format.field_id} onValueChange={field_id => update(index, { field_id })}>
          <SelectTrigger aria-label='Value to shade' className='h-8 min-w-0 flex-1'><SelectValue /></SelectTrigger>
          <SelectContent>{measures.map(item => <SelectItem key={item.field_id} value={item.field_id}>{humanize(name(item.field_id))}</SelectItem>)}</SelectContent>
        </Select>
        <Button variant='ghost' size='icon' className='size-8 shrink-0' aria-label='Remove formatting'
          onClick={() => onChange(state.formats.filter((_, i) => i !== index))}><Trash2 /></Button>
      </div>
      <div className='flex flex-wrap items-center gap-2'>
        <Select value={format.kind} onValueChange={kind => update(index, kind === 'scale'
          ? { kind: 'scale', operator: null, value: null }
          : { kind: 'rule', operator: 'gt', value: { type: 'decimal', value: '0' } })}>
          <SelectTrigger aria-label='Formatting kind' className='h-8 w-36'><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value='scale'>Colour scale</SelectItem><SelectItem value='rule'>Highlight when</SelectItem></SelectContent>
        </Select>
        {format.kind === 'rule' && <>
          <Select value={format.operator ?? 'gt'} onValueChange={operator => update(index, { operator: operator as ConditionalFormat['operator'] })}>
            <SelectTrigger aria-label='Comparison' className='h-8 w-28'><SelectValue /></SelectTrigger>
            <SelectContent>{Object.entries(OPERATOR_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
          </Select>
          <Input aria-label='Limit' inputMode='decimal' className='h-8 w-28' value={format.value?.value ?? ''}
            onChange={event => {
              const text = event.target.value.replace(/[^0-9.-]/g, '')
              if (/^-?\d*\.?\d*$/.test(text)) update(index, { value: { type: 'decimal', value: text === '' || text === '-' ? '0' : text } })
            }} />
        </>}
        <Select value={format.tone ?? 'accent'} onValueChange={tone => update(index, { tone: tone as ConditionalFormat['tone'] })}>
          <SelectTrigger aria-label='Colour' className='h-8 w-24'><SelectValue /></SelectTrigger>
          <SelectContent>{Object.entries(TONE_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>
            <span aria-hidden className='size-2.5 rounded-full' style={{ backgroundColor: `var(--${value === 'accent' ? 'chart-4' : value === 'negative' ? 'destructive' : value === 'positive' ? 'success' : 'warning'})` }} />{label}
          </SelectItem>)}</SelectContent>
        </Select>
      </div>
    </div>)}
    {state.formats.length < 16 && <Button variant='outline' size='sm'
      onClick={() => onChange([...state.formats, { field_id: measures[0].field_id, kind: 'scale', tone: 'accent' }])}>
      <Plus />Add formatting
    </Button>}
  </div>
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className='space-y-2 border-t pt-4 first:border-t-0 first:pt-0'>
    <h4 className='type-label text-xs font-medium text-muted-foreground'>{title}</h4>
    {children}
  </section>
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  const id = useId()
  return <div className='flex min-h-9 items-center gap-2'>
    <Checkbox id={id} checked={checked} onCheckedChange={value => onChange(value === true)} />
    <Label htmlFor={id} className='font-normal'>{label}</Label>
  </div>
}

type InspectorProps = {
  datasets: DatasetReferenceSummary[]
  chart: ChartDefinition | null
  initialVisual?: Visual
  saving: boolean
  error?: string
  onSave: (chart: ChartDefinition) => void
  onClose: () => void
  /**
   * Edit a chart that is not saved yet (a Studio answer's draft): its fields
   * come from the caller and its preview reads through the draft's governed
   * query. The dataset cannot be changed.
   */
  draft?: { fields: DatasetField[]; draftRef: string; saveLabel: string }
}

/** Configure and style one native chart; the preview runs the same governed query as the canvas. */
export function ChartInspector(props: InspectorProps) {
  const initialDataset = props.chart
    ? { id: props.chart.dataset_id, revision: props.chart.dataset_revision }
    : props.datasets.find(item => item.title !== null)
      ? { id: props.datasets.find(item => item.title !== null)!.dataset_id, revision: props.datasets.find(item => item.title !== null)!.revision }
      : null
  const [dataset, setDataset] = useState(initialDataset)
  const definition = useDatasetDefinition(props.draft ? undefined : dataset?.id, dataset?.revision)
  if (props.draft && dataset) return <InspectorShell {...props} title='Edit chart'>
    <Builder key={`${dataset.id}:${dataset.revision}`} {...props} fields={props.draft.fields}
      dataset={dataset} onDataset={setDataset} />
  </InspectorShell>
  if (!dataset) return <InspectorShell {...props} title='Chart'>
    <p className='p-4 text-sm text-muted-foreground'>Add a dataset in the Data tab before building a chart.</p>
  </InspectorShell>
  return <InspectorShell {...props} title={props.chart ? 'Edit chart' : 'New chart'}>
    {definition.isPending ? <p role='status' className='p-4 text-sm text-muted-foreground'>Loading dataset fields…</p>
      : definition.isError ? <p role='alert' className='p-4 text-sm text-muted-foreground'>This dataset is unavailable with your current role.</p>
        : <Builder key={`${dataset.id}:${dataset.revision}`} {...props} fields={definition.data.fields as DatasetField[]}
          dataset={dataset} onDataset={setDataset} />}
  </InspectorShell>
}

function InspectorShell({ title, onClose, draft, children }: InspectorProps & { title: string; children: React.ReactNode }) {
  return <aside aria-label='Chart inspector' className='flex h-full min-h-0 w-full min-w-0 flex-col bg-card'>
    {/* A draft is edited inside a panel that has its own title and close button. */}
    {!draft && <header className='flex h-12 shrink-0 items-center justify-between gap-2 border-b pr-2 pl-4'>
      <h3 className='truncate text-sm font-medium'>{title}</h3>
      <Button variant='ghost' size='icon' className='size-8 text-muted-foreground hover:text-foreground' aria-label='Close inspector' onClick={onClose}><X /></Button>
    </header>}
    {children}
  </aside>
}

function Builder({ datasets, chart, initialVisual, saving, error, onSave, fields, dataset, onDataset, draft }: InspectorProps & {
  fields: DatasetField[]; dataset: { id: string; revision: number }; onDataset: (value: { id: string; revision: number }) => void
}) {
  const [state, setState] = useState<BuilderState>(() => chart && chart.dataset_id === dataset.id
    ? fromChart(chart, fields)
    : switchVisual(initialState({ chart_id: chart?.chart_id ?? crypto.randomUUID(), dataset_id: dataset.id,
      dataset_revision: dataset.revision, fields }), initialVisual ?? 'bar', fields))
  const titleId = useId()
  const set = (changes: Partial<BuilderState>) => setState(current => ({ ...current, ...changes }))
  const kind = kindOf(state.visual)
  const gap = missing(state)
  const preview = useMemo(() => gap && gap !== 'Name the chart' ? null : toChart({ ...state, title: state.title || 'Preview' }), [state, gap])
  const numeric = fields.filter(isNumeric)
  const dimensionLabels = kind === 'cartesian' ? ['X axis', 'Second X field'] : kind === 'category' ? ['Category']
    : kind === 'heatmap' ? ['Columns', 'Rows'] : kind === 'flow' ? ['From', 'To'] : kind === 'hierarchy' ? ['Level 1', 'Level 2', 'Level 3']
      : ['Group by']
  const maxDimensions = kind === 'cartesian' ? 2 : kind === 'table' ? 8 : kind === 'hierarchy' ? 3 : kind === 'heatmap' || kind === 'flow' ? 2 : 1
  const maxMeasures = kind === 'cartesian' ? 4 : kind === 'table' ? 8 : kind === 'single_measure' ? 2 : 1
  const addDimension = () => {
    const used = new Set(state.dimensions.map(item => item.field_id))
    const field = fields.find(item => !used.has(item.field_id) && !isNumeric(item)) ?? fields.find(item => !used.has(item.field_id))
    if (field) set({ dimensions: [...state.dimensions, { field_id: field.field_id, date_grain: isTemporal(field) ? 'month' : null }] })
  }
  const addMeasure = () => {
    const field = numeric.find(item => !state.measures.some(m => m.field_id === item.field_id)) ?? numeric[0] ?? fields[0]
    if (field) set({ measures: [...state.measures, { field_id: field.field_id, aggregation: defaultAggregation(field), series: 'line', axis: 'left' }] })
  }

  return <>
    <div className='h-52 shrink-0 border-b p-4'>
      <div className='flex size-full min-h-0 flex-col rounded-md border bg-background p-2'>
        {preview ? <NativeChart chart={preview} draft draftRef={draft?.draftRef} /> : <p className='m-auto text-sm text-muted-foreground'>{gap}</p>}
      </div>
    </div>
    <Tabs defaultValue='configure' className='flex min-h-0 flex-1 flex-col gap-0'>
      <TabsList className='mx-4 mt-4 shrink-0'>
        <TabsTrigger value='configure'>Configure</TabsTrigger>
        <TabsTrigger value='style'>Style</TabsTrigger>
      </TabsList>
      <TabsContent value='configure' className='min-h-0 flex-1 space-y-4 overflow-y-auto p-4'>
        <div className='space-y-1'>
          <Label htmlFor={titleId} className='text-xs text-muted-foreground'>Chart name</Label>
          <Input id={titleId} value={state.title} maxLength={256} placeholder='Revenue by region' className='h-9'
            onChange={event => set({ title: event.target.value })} />
        </div>
        {!draft && <Section title='Dataset'>
          <Select value={`${dataset.id}:${dataset.revision}`} onValueChange={value => {
            const [id, revision] = value.split(':')
            onDataset({ id, revision: Number(revision) })
          }}>
            <SelectTrigger aria-label='Dataset' className='h-9 w-full'><SelectValue /></SelectTrigger>
            <SelectContent>{datasets.filter(item => item.title !== null).map(item =>
              <SelectItem key={item.dataset_id} value={`${item.dataset_id}:${item.revision}`}>{item.title}</SelectItem>)}</SelectContent>
          </Select>
        </Section>}
        <Section title='Visualization'>
          <div role='radiogroup' aria-label='Visualization type' className='grid grid-cols-4 gap-1.5'>
            {VISUALS.map(item => {
              const Icon = VISUAL_ICONS[item.value] ?? BarChart3
              const active = state.visual === item.value
              return <button key={item.value} type='button' role='radio' aria-checked={active}
                onClick={() => setState(current => switchVisual(current, item.value as Visual, fields))}
                className={cn('flex min-h-14 flex-col items-center justify-center gap-1 rounded-md border px-1 text-xs transition-colors',
                  'focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                  active ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:bg-muted')}>
                <Icon className='size-4' aria-hidden />{item.label}
              </button>
            })}
          </div>
        </Section>
        {kind === 'map' ? <>
          <Section title='Location'>
            <FieldSelect label='Latitude' fields={fields} filter={isNumeric} value={state.y} onChange={y => set({ y })} />
            <FieldSelect label='Longitude' fields={fields} filter={isNumeric} value={state.x} onChange={x => set({ x })} />
          </Section>
          <Section title='Point size'>
            {state.measures[0] ? <MeasureRow label='Value' fields={fields} value={state.measures[0]}
              onChange={value => set({ measures: [value] })} onRemove={() => set({ measures: [] })} />
              : <>
                <p className='text-xs text-muted-foreground'>Each point counts its rows.</p>
                <Button variant='outline' size='sm' onClick={addMeasure}><Plus />Size by a value</Button>
              </>}
          </Section>
        </> : kind === 'scatter' || kind === 'distribution' ? <Section title='Fields'>
          <FieldSelect label={kind === 'distribution' ? 'Value' : 'X axis'} fields={fields} filter={isNumeric} value={state.x} onChange={x => set({ x })} />
          {kind === 'scatter' && <>
            <FieldSelect label='Y axis' fields={fields} filter={isNumeric} value={state.y} onChange={y => set({ y })} />
            <FieldSelect label='Size' fields={fields} filter={isNumeric} value={state.size} allowNone='None' onChange={size => set({ size })} />
          </>}
          {kind === 'distribution' && <div className='space-y-1'>
            <Label className='text-xs text-muted-foreground'>Bins</Label>
            <Input type='number' min={2} max={200} value={state.bins} className='h-9 w-24'
              onChange={event => set({ bins: Math.max(2, Math.min(200, Number(event.target.value) || 20)) })} />
          </div>}
        </Section> : <>
          {kind !== 'single_measure' && <Section title={state.visual === 'pivot' ? 'Rows' : kind === 'table' ? 'Columns' : 'Group by'}>
            {state.dimensions.map((item, index) => <DimensionRow key={index} label={dimensionLabels[index] ?? `Field ${index + 1}`}
              fields={fields} value={item}
              onChange={value => set({ dimensions: state.dimensions.map((old, i) => i === index ? value : old) })}
              onRemove={state.dimensions.length > (kind === 'table' ? 0 : 1) ? () => set({ dimensions: state.dimensions.filter((_, i) => i !== index) }) : undefined} />)}
            {state.dimensions.length < maxDimensions && <Button variant='outline' size='sm' onClick={addDimension}><Plus />Add field</Button>}
          </Section>}
          {state.visual === 'pivot' && <Section title='Columns'>
            {state.pivotColumns.map((item, index) => <DimensionRow key={index} label={`Across ${index + 1}`} fields={fields} value={item}
              onChange={value => set({ pivotColumns: state.pivotColumns.map((old, i) => i === index ? value : old) })}
              onRemove={state.pivotColumns.length > 1 ? () => set({ pivotColumns: state.pivotColumns.filter((_, i) => i !== index) }) : undefined} />)}
            {state.pivotColumns.length < 3 && <Button variant='outline' size='sm' onClick={() => {
              const used = new Set([...state.dimensions, ...state.pivotColumns].map(item => item.field_id))
              const field = fields.find(item => !used.has(item.field_id) && !isNumeric(item))
              if (field) set({ pivotColumns: [...state.pivotColumns, { field_id: field.field_id, date_grain: isTemporal(field) ? 'month' : null }] })
            }}><Plus />Add field</Button>}
          </Section>}
          <Section title={kind === 'single_measure' ? 'Value' : 'Values'}>
            {state.measures.map((item, index) => <MeasureRow key={index}
              label={kind === 'single_measure' ? (index === 0 ? 'Value' : 'Compare with') : kind === 'cartesian' ? `Y axis ${index + 1}` : `Value ${index + 1}`}
              fields={fields} value={item} combo={state.visual === 'combo'} calculable={canCalculate(state)}
              onChange={value => set({ measures: state.measures.map((old, i) => i === index ? value : old) })}
              onRemove={state.measures.length > (kind === 'table' ? 0 : 1) ? () => set({ measures: state.measures.filter((_, i) => i !== index) }) : undefined} />)}
            {state.measures.length < maxMeasures && <Button variant='outline' size='sm' onClick={addMeasure}><Plus />{kind === 'single_measure' ? 'Add comparison' : 'Add value'}</Button>}
          </Section>
        </>}
        {(kind === 'cartesian' || kind === 'scatter' || kind === 'map') && <Section title='Color / group by'>
          {state.color ? <DimensionRow label='Color' fields={fields} value={state.color} onChange={color => set({ color })} onRemove={() => set({ color: null })} />
            : <Button variant='outline' size='sm' onClick={() => {
              const field = fields.find(item => !isNumeric(item) && !state.dimensions.some(d => d.field_id === item.field_id))
              if (field) set({ color: { field_id: field.field_id, date_grain: null } })
            }}><Plus />Split by a field</Button>}
        </Section>}
        {kind === 'cartesian' && <Section title='Layout'>
          <div className='flex flex-wrap gap-2'>
            <Select value={state.orientation} onValueChange={value => set({ orientation: value as BuilderState['orientation'] })}>
              <SelectTrigger aria-label='Orientation' className='h-9 w-36'><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value='vertical'>Vertical</SelectItem><SelectItem value='horizontal'>Horizontal</SelectItem></SelectContent>
            </Select>
            <Select value={state.stack} onValueChange={value => set({ stack: value as BuilderState['stack'] })}>
              <SelectTrigger aria-label='Stacking' className='h-9 w-36'><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value='none'>Side by side</SelectItem><SelectItem value='stacked'>Stacked</SelectItem><SelectItem value='percent'>100% stacked</SelectItem></SelectContent>
            </Select>
          </div>
          {state.visual !== 'bar' && <Toggle label='Smooth lines' checked={state.smooth} onChange={smooth => set({ smooth })} />}
        </Section>}
        {kind === 'category' && <Section title='Slices'>
          {state.visual === 'pie' && <Toggle label='Donut' checked={state.donut} onChange={donut => set({ donut })} />}
          <div className='flex items-center gap-2'>
            <Label className='text-xs text-muted-foreground'>Show the top</Label>
            <Input type='number' aria-label='Maximum categories' min={2} max={100} value={state.maxCategories} className='h-9 w-20'
              onChange={event => set({ maxCategories: Math.max(2, Math.min(100, Number(event.target.value) || 12)) })} />
          </div>
          {state.visual === 'pie' && canCombineOther(state.measures[0]?.aggregation)
            && <Toggle label='Combine the rest into Other' checked={state.otherSlice} onChange={otherSlice => set({ otherSlice })} />}
        </Section>}
        {kind === 'table' && <Section title='Totals'>
          <Toggle label={state.visual === 'pivot' ? 'Show totals' : 'Show a total row'} checked={state.grandTotal} onChange={grandTotal => set({ grandTotal })} />
          {state.visual === 'pivot' && state.dimensions.length > 1 && <Toggle label='Subtotal each row group' checked={state.subtotals} onChange={subtotals => set({ subtotals })} />}
        </Section>}
        <Section title='Sort and limit'>
          <div className='flex flex-wrap gap-2'>
            <Select value={state.sort} onValueChange={value => set({ sort: value as BuilderState['sort'] })}>
              <SelectTrigger aria-label='Sort' className='h-9 w-44'><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value='none'>Natural order</SelectItem><SelectItem value='desc'>Largest value first</SelectItem><SelectItem value='asc'>Smallest value first</SelectItem></SelectContent>
            </Select>
            <Select value={String(state.rowLimit)} onValueChange={value => set({ rowLimit: Number(value) })}>
              <SelectTrigger aria-label='Row limit' className='h-9 w-32'><SelectValue /></SelectTrigger>
              <SelectContent>{[100, 500, 1000, 5000, 10000].map(n => <SelectItem key={n} value={String(n)}>{n.toLocaleString()} rows</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </Section>
      </TabsContent>
      <TabsContent value='style' className='min-h-0 flex-1 space-y-4 overflow-y-auto p-4'>
        <Section title='Display'>
          <Toggle label='Show title' checked={state.showTitle} onChange={showTitle => set({ showTitle })} />
          <Toggle label='Show legend' checked={state.showLegend} onChange={showLegend => set({ showLegend })} />
          <Toggle label='Show data labels' checked={state.showLabels} onChange={showLabels => set({ showLabels })} />
          <Toggle label='Show axis titles' checked={state.showAxisTitles} onChange={showAxisTitles => set({ showAxisTitles })} />
        </Section>
        <Section title='Colors'>
          <Select value={state.palette} onValueChange={value => set({ palette: value as BuilderState['palette'] })}>
            <SelectTrigger aria-label='Palette' className='h-9 w-full'><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value='neutral'>Neutral, highlight differences</SelectItem><SelectItem value='categorical'>Categorical</SelectItem><SelectItem value='single_tone'>Single tone</SelectItem></SelectContent>
          </Select>
        </Section>
        {kind === 'table' && <Section title='Conditional formatting'>
          <FormatRules state={state} fields={fields} onChange={formats => set({ formats })} />
        </Section>}
        <Section title='Number format'>
          <div className='flex flex-wrap gap-2'>
            <Select value={state.number.kind} onValueChange={value => set({ number: { ...state.number, kind: value as BuilderState['number']['kind'] } })}>
              <SelectTrigger aria-label='Number format' className='h-9 w-32'><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value='number'>Number</SelectItem><SelectItem value='currency'>Currency</SelectItem><SelectItem value='percent'>Percent</SelectItem></SelectContent>
            </Select>
            <Input type='number' aria-label='Decimal places' min={0} max={6} value={state.number.decimals} className='h-9 w-20'
              onChange={event => set({ number: { ...state.number, decimals: Math.max(0, Math.min(6, Number(event.target.value) || 0)) } })} />
            {state.number.kind === 'currency' && <Input aria-label='Currency code' value={state.number.currency} maxLength={3} className='h-9 w-20 uppercase'
              onChange={event => set({ number: { ...state.number, currency: event.target.value.toUpperCase().replace(/[^A-Z]/g, '') } })} />}
          </div>
        </Section>
      </TabsContent>
    </Tabs>
    <footer className='flex min-h-14 shrink-0 flex-wrap items-center justify-end gap-2 border-t px-4 py-2.5'>
      {(error || gap) && <p role={error ? 'alert' : 'status'} className={cn('me-auto text-sm', error ? 'text-destructive' : 'text-muted-foreground')}>{error ?? gap}</p>}
      <Button disabled={Boolean(gap) || saving || (state.number.kind === 'currency' && state.number.currency.length !== 3)}
        onClick={() => onSave(toChart(state))}>{saving ? 'Saving…' : draft ? draft.saveLabel : chart ? 'Save chart' : 'Add to dashboard'}</Button>
    </footer>
  </>
}

