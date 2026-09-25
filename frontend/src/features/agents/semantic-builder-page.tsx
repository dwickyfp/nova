import { useMemo, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowRight,
  KeyRound,
  Plus,
  Sigma,
  Table2,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { semanticViewsApi } from '@/features/intelligence/semantic-views-api'
import { metadataApi } from './metadata-api'
import {
  draftToOssieYaml,
  emptyDraft,
  mapDatatype,
  newId,
  type DraftDataset,
  type DraftField,
  type SemanticDraft,
} from './semantic-draft'

/**
 * Visual Semantic View builder.
 *
 * The user edits datasets, fields, metrics, and relationships as forms; the
 * Ossie YAML is generated from that draft and shown live. Nothing here writes
 * YAML by hand, so an invalid document is hard to produce by accident, and the
 * backend validator remains the final gate.
 */
export function SemanticBuilderPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<SemanticDraft>(emptyDraft)
  const [saveOpen, setSaveOpen] = useState(false)

  const databasesQuery = useQuery({
    queryKey: ['agents', 'databases'],
    queryFn: () => metadataApi.listDatabases(),
  })

  const yaml = useMemo(() => draftToOssieYaml(draft), [draft])

  const create = useMutation({
    mutationFn: () =>
      semanticViewsApi.create({
        name: draft.name.trim(),
        database: draft.datasets[0]?.source.split('.').slice(-2)[0] ?? '',
        definition: yaml,
      }),
    onSuccess: (view) => {
      toast.success(`Semantic View "${view.name}" created as draft`)
      setSaveOpen(false)
      queryClient.invalidateQueries({ queryKey: ['intelligence', 'semantic'] })
      void navigate({ to: '/semantic-views' })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const addDataset = (dataset: DraftDataset) => {
    setDraft((d) => ({ ...d, datasets: [...d.datasets, dataset] }))
  }

  const updateDataset = (id: string, patch: Partial<DraftDataset>) =>
    setDraft((d) => ({
      ...d,
      datasets: d.datasets.map((ds) => (ds.id === id ? { ...ds, ...patch } : ds)),
    }))

  const removeDataset = (id: string) =>
    setDraft((d) => ({ ...d, datasets: d.datasets.filter((ds) => ds.id !== id) }))

  return (
    <>
      <Header fixed />
      <Main scroll>
        <div className='mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between'>
          <div className='min-w-0'>
            <h1 className='text-2xl font-semibold tracking-tight'>Build Semantic View</h1>
            <p className='mt-1 max-w-2xl text-sm text-muted-foreground'>
              Choose a source table and name the values people will ask about. Save a draft, then check and publish it from Semantic Views.
            </p>
          </div>
          <div className='flex shrink-0 items-center gap-2'>
            <Button variant='outline' asChild><Link to='/semantic-views'>Semantic Views</Link></Button>
            <Button
              disabled={!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(draft.name.trim()) || draft.datasets.length === 0}
              onClick={() => setSaveOpen(true)}
            >
              Create draft
            </Button>
          </div>
        </div>

        <ol className='mb-6 grid gap-3 rounded-lg border bg-surface-1 p-4 text-sm sm:grid-cols-3'>
          <li><b>1. Name the view</b><p className='text-muted-foreground'>Describe the business topic.</p></li>
          <li><b>2. Add data</b><p className='text-muted-foreground'>Select a table; its columns are added automatically.</p></li>
          <li><b>3. Save a draft</b><p className='text-muted-foreground'>Validate and publish it from the view page.</p></li>
        </ol>
        <div className='min-h-0 space-y-6'>
            <div className='space-y-6'>
              <section className='space-y-4 rounded-lg border p-4'>
                <div>
                  <h2 className='font-medium'>1. Name this view</h2>
                  <p className='text-sm text-muted-foreground'>Choose a short name and explain what questions it should answer.</p>
                </div>
                <div className='grid gap-4 sm:grid-cols-2'>
                  <div className='space-y-2'>
                    <Label htmlFor='m-name'>View name</Label>
                    <Input
                      id='m-name'
                      value={draft.name}
                      onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                      placeholder='sales_analytics'
                    />
                    <p className='text-xs text-muted-foreground'>Use letters, numbers, and underscores; start with a letter or underscore.</p>
                  </div>
                  <div className='space-y-2'>
                    <Label htmlFor='m-desc'>Description</Label>
                    <Input
                      id='m-desc'
                      value={draft.description}
                      onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                      placeholder='Sales and customer analytics'
                    />
                  </div>
                </div>
              </section>

              {databasesQuery.isPending ? <p role='status' className='text-sm text-muted-foreground'>Loading databases…</p> : null}
              {databasesQuery.isError ? <p role='alert' className='text-sm text-destructive'>Could not load databases. <Button variant='link' onClick={() => void databasesQuery.refetch()}>Retry</Button></p> : null}
              <DatasetsSection
                datasets={draft.datasets}
                databases={databasesQuery.data ?? []}
                onAdd={addDataset}
                onUpdate={updateDataset}
                onRemove={removeDataset}
              />

              <MetricsSection
                metrics={draft.metrics}
                onChange={(metrics) => setDraft((d) => ({ ...d, metrics }))}
              />

              <RelationshipsSection
                relationships={draft.relationships}
                datasets={draft.datasets}
                onChange={(relationships) => setDraft((d) => ({ ...d, relationships }))}
              />
            </div>

          <details className='rounded-lg border p-4'>
            <summary className='cursor-pointer font-medium'>Preview generated Ossie YAML (advanced)</summary>
            <p className='mt-2 text-sm text-muted-foreground'>Nova creates this definition from the fields above. You do not need to edit it to save the view.</p>
            <Badge variant='outline' className='mt-3'>{yaml.split('\n').length} lines</Badge>
            <pre className='mt-3 max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs leading-relaxed'>{yaml}</pre>
          </details>
        </div>
      </Main>

      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Semantic View draft</DialogTitle>
          </DialogHeader>
          <p className='text-sm text-muted-foreground'>
            Save <span className='font-medium'>{draft.name}</span> with{' '}
            {draft.datasets.length} dataset(s), {draft.metrics.length} metric(s), and{' '}
            {draft.relationships.length} relationship(s)?
          </p>
          <DialogFooter>
            <Button variant='outline' onClick={() => setSaveOpen(false)}>
              Cancel
            </Button>
            <Button disabled={create.isPending} onClick={() => create.mutate()}>
              Create draft
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

// ── Datasets ───────────────────────────────────────────────────

function DatasetsSection({
  datasets,
  databases,
  onAdd,
  onUpdate,
  onRemove,
}: {
  datasets: DraftDataset[]
  databases: string[]
  onAdd: (dataset: DraftDataset) => void
  onUpdate: (id: string, patch: Partial<DraftDataset>) => void
  onRemove: (id: string) => void
}) {
  const [database, setDatabase] = useState<string>('')
  const [table, setTable] = useState<string>('')

  const tablesQuery = useQuery({
    queryKey: ['agents', 'tables', database],
    queryFn: () => metadataApi.listTables(database),
    enabled: Boolean(database),
  })

  const detailQuery = useQuery({
    queryKey: ['agents', 'table-detail', database, table],
    queryFn: () => metadataApi.getTableDetail(database, table),
    enabled: Boolean(database && table),
  })

  const importTable = () => {
    if (!database || !table) return
    const columns = detailQuery.data?.columns ?? []
    const fields: DraftField[] = columns.map((column) => ({
      id: newId('f'),
      name: column.name,
      expression: column.name,
      datatype: mapDatatype(column.data_type),
      isTimeDimension: /date|time|timestamp/i.test(column.data_type),
      description: '',
    }))
    const primaryKey = columns.find((c) => c.column_key === 'PRI')?.name ?? ''
    onAdd({
      id: newId('ds'),
      name: table,
      source: `${database}.${table}`,
      primaryKey,
      description: '',
      fields,
    })
  }

  return (
    <section className='space-y-4 rounded-lg border p-4'>
      <div className='flex items-center gap-2'>
        <Table2 className='size-4' />
        <h2 className='font-medium'>2. Add a source table</h2>
      </div>
      <p className='text-sm text-muted-foreground'>Select a database and table. Nova imports its columns so you can choose which ones describe the business topic.</p>

      <div className='grid gap-3 sm:grid-cols-[1fr_1fr_auto]'>
        <Select value={database || undefined} onValueChange={(v) => { setDatabase(v); setTable('') }}>
          <SelectTrigger aria-label='Source database'>
            <SelectValue placeholder='Database' />
          </SelectTrigger>
          <SelectContent>
            {databases.map((db) => (
              <SelectItem key={db} value={db}>
                {db}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={table || undefined} onValueChange={setTable} disabled={!database}>
          <SelectTrigger aria-label='Source table'>
            <SelectValue placeholder={tablesQuery.isLoading ? 'Loading…' : 'Table'} />
          </SelectTrigger>
          <SelectContent>
            {tablesQuery.data?.tables.map((t) => (
              <SelectItem key={t.name} value={t.name}>
                {t.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button onClick={importTable} disabled={!table || detailQuery.isLoading}>
          <Plus className='size-4' />
          Add table
        </Button>
      </div>
      {tablesQuery.isError ? <p role='alert' className='text-sm text-destructive'>Could not load tables. <Button variant='link' onClick={() => void tablesQuery.refetch()}>Retry</Button></p> : null}
      {detailQuery.isError ? <p role='alert' className='text-sm text-destructive'>Could not load columns for this table. <Button variant='link' onClick={() => void detailQuery.refetch()}>Retry</Button></p> : null}

      {datasets.length === 0 ? (
        <p className='text-sm text-muted-foreground'>
          Add a table to start. Its columns become fields automatically.
        </p>
      ) : (
        <div className='space-y-3'>
          {datasets.map((dataset) => (
            <DatasetCard
              key={dataset.id}
              dataset={dataset}
              onUpdate={(patch) => onUpdate(dataset.id, patch)}
              onRemove={() => onRemove(dataset.id)}
            />
          ))}
        </div>
      )}
    </section>
  )
}

function DatasetCard({
  dataset,
  onUpdate,
  onRemove,
}: {
  dataset: DraftDataset
  onUpdate: (patch: Partial<DraftDataset>) => void
  onRemove: () => void
}) {
  const [expanded, setExpanded] = useState(true)

  const updateField = (id: string, patch: Partial<DraftField>) =>
    onUpdate({ fields: dataset.fields.map((f) => (f.id === id ? { ...f, ...patch } : f)) })

  const removeField = (id: string) =>
    onUpdate({ fields: dataset.fields.filter((f) => f.id !== id) })

  return (
    <div className='rounded-md border'>
      <div className='flex items-center justify-between gap-2 border-b px-3 py-2'>
        <button
          type='button'
          className='flex min-w-0 items-center gap-2 text-left'
          onClick={() => setExpanded((v) => !v)}
        >
          <Badge variant='secondary'>{dataset.name}</Badge>
          <span className='truncate text-xs text-muted-foreground'>{dataset.source}</span>
        </button>
        <Button size='icon' variant='ghost' onClick={onRemove} aria-label='Remove dataset'>
          <Trash2 className='size-4' />
        </Button>
      </div>

      {expanded ? (
        <div className='space-y-3 p-3'>
          <div className='grid gap-3 sm:grid-cols-2'>
            <div className='space-y-1.5'>
              <Label className='text-xs'>Dataset name</Label>
              <Input
                value={dataset.name}
                onChange={(e) => onUpdate({ name: e.target.value })}
              />
            </div>
            <div className='space-y-1.5'>
              <Label className='text-xs'>Primary key</Label>
              <Input
                value={dataset.primaryKey}
                onChange={(e) => onUpdate({ primaryKey: e.target.value })}
                placeholder='order_id'
              />
            </div>
          </div>

          <div className='space-y-1.5'>
            <Label className='text-xs'>Description</Label>
            <Input
              value={dataset.description}
              onChange={(e) => onUpdate({ description: e.target.value })}
              placeholder='One row per order'
            />
          </div>

          <Separator />
          <div className='flex items-center gap-2 text-xs font-medium text-muted-foreground'>
            <KeyRound className='size-3' />
            Fields ({dataset.fields.length})
          </div>
          <div className='space-y-2'>
            {dataset.fields.map((field) => (
              <div key={field.id} className='grid gap-2 sm:grid-cols-[1fr_1.4fr_auto]'>
                <Input
                  value={field.name}
                  onChange={(e) => updateField(field.id, { name: e.target.value })}
                  aria-label='Field name'
                />
                <Input
                  value={field.expression}
                  onChange={(e) => updateField(field.id, { expression: e.target.value })}
                  aria-label='Field expression'
                  className='font-mono text-xs'
                />
                <div className='flex items-center gap-2'>
                  <label className='flex items-center gap-1 text-xs whitespace-nowrap'>
                    <Checkbox
                      checked={field.isTimeDimension}
                      onCheckedChange={(v) => updateField(field.id, { isTimeDimension: Boolean(v) })}
                    />
                    time
                  </label>
                  <Button
                    size='icon'
                    variant='ghost'
                    onClick={() => removeField(field.id)}
                    aria-label={`Remove ${field.name}`}
                  >
                    <Trash2 className='size-4' />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

// ── Metrics ────────────────────────────────────────────────────

function MetricsSection({
  metrics,
  onChange,
}: {
  metrics: SemanticDraft['metrics']
  onChange: (metrics: SemanticDraft['metrics']) => void
}) {
  const add = () =>
    onChange([
      ...metrics,
      { id: newId('m'), name: '', expression: '', datatype: 'Decimal', description: '' },
    ])
  const update = (id: string, patch: Partial<SemanticDraft['metrics'][number]>) =>
    onChange(metrics.map((m) => (m.id === id ? { ...m, ...patch } : m)))
  const remove = (id: string) => onChange(metrics.filter((m) => m.id !== id))

  return (
    <section className='space-y-4 rounded-lg border p-4'>
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-2'>
          <Sigma className='size-4' />
          <h2 className='font-medium'>3. Add measures (optional)</h2>
        </div>
        <Button size='sm' variant='outline' onClick={add}>
          <Plus className='size-3.5' />
          Add metric
        </Button>
      </div>
      <p className='text-sm text-muted-foreground'>A measure is a number people ask for, such as total revenue or order count. Add one if your view needs calculations.</p>
      {metrics.length === 0 ? (
        <p className='text-sm text-muted-foreground'>
          Define business measures like total_revenue = SUM(orders.total_amount).
        </p>
      ) : (
        <div className='space-y-2'>
          {metrics.map((metric) => (
            <div key={metric.id} className='grid gap-2 sm:grid-cols-[1fr_1.6fr_auto]'>
              <Input
                value={metric.name}
                onChange={(e) => update(metric.id, { name: e.target.value })}
                placeholder='total_revenue'
                aria-label='Metric name'
              />
              <Input
                value={metric.expression}
                onChange={(e) => update(metric.id, { expression: e.target.value })}
                placeholder='SUM(orders.total_amount)'
                aria-label='Metric expression'
                className='font-mono text-xs'
              />
              <Button
                size='icon'
                variant='ghost'
                onClick={() => remove(metric.id)}
                aria-label={`Remove ${metric.name}`}
              >
                <Trash2 className='size-4' />
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// ── Relationships ──────────────────────────────────────────────

function RelationshipsSection({
  relationships,
  datasets,
  onChange,
}: {
  relationships: SemanticDraft['relationships']
  datasets: DraftDataset[]
  onChange: (relationships: SemanticDraft['relationships']) => void
}) {
  const add = () => {
    const from = datasets[0]?.name ?? ''
    const to = datasets[1]?.name ?? datasets[0]?.name ?? ''
    onChange([
      ...relationships,
      { id: newId('r'), name: `${from}_to_${to}`, from, to, fromColumns: '', toColumns: '' },
    ])
  }
  const update = (id: string, patch: Partial<SemanticDraft['relationships'][number]>) =>
    onChange(relationships.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const remove = (id: string) => onChange(relationships.filter((r) => r.id !== id))

  return (
    <section className='space-y-4 rounded-lg border p-4'>
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-2'>
          <ArrowRight className='size-4' />
          <h2 className='font-medium'>4. Connect tables (optional)</h2>
        </div>
        <Button size='sm' variant='outline' onClick={add} disabled={datasets.length < 2}>
          <Plus className='size-3.5' />
          Add relationship
        </Button>
      </div>
      <p className='text-sm text-muted-foreground'>If you added more than one table, connect their matching key columns so questions can use both.</p>
      {datasets.length < 2 ? (
        <p className='text-sm text-muted-foreground'>
          Add at least two datasets to define a join.
        </p>
      ) : relationships.length === 0 ? (
        <p className='text-sm text-muted-foreground'>
          Define how datasets join, e.g. orders.customer_id = customers.customer_id.
        </p>
      ) : (
        <div className='space-y-3'>
          {relationships.map((rel) => (
            <div key={rel.id} className='grid gap-2 sm:grid-cols-[1fr_1fr_1fr_1fr_auto]'>
              <Select value={rel.from} onValueChange={(v) => update(rel.id, { from: v })}>
                <SelectTrigger aria-label='From dataset'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {datasets.map((d) => (
                    <SelectItem key={d.id} value={d.name}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                value={rel.fromColumns}
                onChange={(e) => update(rel.id, { fromColumns: e.target.value })}
                placeholder='customer_id'
                aria-label='From columns'
                className='font-mono text-xs'
              />
              <Select value={rel.to} onValueChange={(v) => update(rel.id, { to: v })}>
                <SelectTrigger aria-label='To dataset'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {datasets.map((d) => (
                    <SelectItem key={d.id} value={d.name}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                value={rel.toColumns}
                onChange={(e) => update(rel.id, { toColumns: e.target.value })}
                placeholder='customer_id'
                aria-label='To columns'
                className='font-mono text-xs'
              />
              <Button
                size='icon'
                variant='ghost'
                onClick={() => remove(rel.id)}
                aria-label='Remove relationship'
              >
                <Trash2 className='size-4' />
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
