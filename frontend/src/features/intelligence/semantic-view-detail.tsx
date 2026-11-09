import { useAuthStore } from '@/stores/auth-store'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingOverlay } from '@/components/ui/loading-overlay'
import { StatusBadge } from '@/components/ui/status-badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ContextInspector } from './context-inspector'
import { AutopilotPanel } from './autopilot-panel'
import { semanticViewsApi, type SemanticExpression,
  type SemanticPreview, type SemanticViewDetail, type SemanticVersion } from './semantic-views-api'

function expressionText(value: SemanticExpression | undefined): string {
  if (typeof value === 'string') return value
  return value?.dialects?.map((item) => `${item.dialect}: ${item.expression}`).join('\n') ?? ''
}

function contextText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value == null) return ''
  return JSON.stringify(value)
}

function dateText(value: string | null | undefined): string {
  if (!value) return 'Not yet'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString()
}

function Fact({ label, children }: { label: string, children: React.ReactNode }) {
  return <div className='min-w-0'><dt className='text-muted-foreground'>{label}</dt><dd className='mt-1'>{children}</dd></div>
}

/** Only the properties a definition sets, as a quiet two-column list. */
function Props({ items }: { items: [string, string | null | undefined][] }) {
  const shown = items.filter(([, value]) => value)
  if (!shown.length) return null
  return <dl className='grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs'>
    {shown.map(([label, value]) => <div key={label} className='contents'>
      <dt className='text-muted-foreground'>{label}</dt><dd className='break-words'>{value}</dd></div>)}
  </dl>
}

const COMPLETENESS: Record<string, string> = {
  datasets_with_grain: 'Tables with a row grain',
  relationships_with_cardinality: 'Joins with a known match type',
  metrics_with_descriptions: 'Numbers with a description',
  metrics_with_synonyms: 'Numbers with the words people use',
  time_metrics_with_default_dimension: 'Numbers with a default date',
}

const SEVERITY: Record<string, string> = { error: 'Must fix', warning: 'Suggestion', info: 'Tip' }

function DefinitionSection({ title, children, empty }: {
  title: string
  children: React.ReactNode
  empty?: boolean
}) {
  return <section className='space-y-3 border-t pt-5' aria-label={title}>
    <h3 className='text-sm font-medium'>{title}</h3>
    {empty ? <p className='text-sm text-muted-foreground'>None defined in this version.</p> : children}
  </section>
}

function DefinitionDetail({ view, version }: {
  view: SemanticViewDetail
  version: SemanticVersion
}) {
  const definition = version.definition
  const migration = version.validation?.migration
  const legacyReview = migration?.raw_definition_preserved === true
  const datasets = definition.datasets ?? []
  const metrics = definition.metrics ?? []
  const relationships = definition.relationships ?? []
  const namedFilters = definition.named_filters ?? []
  const hierarchies = Object.entries(definition.hierarchies ?? {})
  const entities = Object.entries(definition.entities ?? {})

  return <div className='space-y-5'>
    <dl className='grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4'>
      <Fact label='Where the data lives'><span className='break-all font-mono text-xs'>{[view.catalog_name, view.database_name, view.schema_name].filter(Boolean).join(' / ')}</span>{legacyReview && view.database_name === 'NOVA_SYSTEM' ? <span className='ms-2 text-warning-strong'>(scope pending review)</span> : null}</Fact>
      <Fact label='Owner'>{view.owner_name || 'Unknown'}</Fact>
      {view.visibility ? <Fact label='Who can see it'>{view.visibility === 'PRIVATE' ? 'Only its owner' : 'Everyone with access to the data'}</Fact> : null}
      <Fact label='Last updated'>{dateText(view.updated_at ?? view.created_at)}</Fact>
    </dl>
    {definition.description ? <p className='text-sm leading-6'>{definition.description}</p> : null}
    {definition.ai_context ? <p className='text-sm'><span className='font-medium'>Business context: </span>{contextText(definition.ai_context)}</p> : null}
    {legacyReview ? <p className='rounded-lg border border-warning bg-warning/10 p-4 text-sm'>Legacy format: add a new Ossie 0.1.1 version before validating or publishing. Review the original definition and verified questions below.</p> : null}

    {!legacyReview ? <>
    <DefinitionSection title='Datasets and fields' empty={datasets.length === 0}>
      <div className='space-y-4'>
        {datasets.map((dataset) => <details key={dataset.name} open={datasets.length === 1} className='group min-w-0 rounded-lg border'>
          <summary className='flex cursor-pointer list-none flex-wrap items-start justify-between gap-2 bg-surface-1 px-4 py-3 group-open:border-b'>
            <div className='min-w-0'>
              <h4 className='flex items-center gap-2 font-medium'><ChevronRight className='size-4 text-muted-foreground transition-transform group-open:rotate-90' aria-hidden='true' />{dataset.name}
                <span className='text-xs font-normal text-muted-foreground'>{(dataset.fields ?? []).length} fields</span></h4>
              <p className='break-all ps-6 font-mono text-xs text-muted-foreground'>{dataset.source || 'No source'}</p>
              {dataset.description ? <p className='mt-1 ps-6 text-sm text-muted-foreground'>{dataset.description}</p> : null}
              {dataset.synonyms?.length ? <p className='mt-1 ps-6 text-xs text-muted-foreground'>Also known as: {dataset.synonyms.join(', ')}</p> : null}
              {dataset.ai_context ? <p className='mt-1 ps-6 text-xs text-muted-foreground'>Context: {contextText(dataset.ai_context)}</p> : null}
            </div>
            {(dataset.grain?.keys?.length || dataset.primary_key?.length) ?
              <span className='text-xs text-muted-foreground'>One row per {(dataset.grain?.keys?.length ? dataset.grain.keys : dataset.primary_key)?.join(', ')}</span> : null}
          </summary>
          {dataset.unique_keys?.length ? <p className='border-b px-4 py-2 text-xs text-muted-foreground'>Unique keys: {dataset.unique_keys.map((keys) => Array.isArray(keys) ? keys.join(' + ') : keys).join('; ')}</p> : null}
          {(dataset.fields ?? []).length ? <div className='min-w-0 overflow-x-auto'>
            <table className='w-full min-w-[520px] text-left text-xs'>
              <thead className='text-muted-foreground'><tr><th className='px-4 py-2 font-medium'>Field</th><th className='px-4 py-2 font-medium'>Type</th><th className='px-4 py-2 font-medium'>Expression</th><th className='px-4 py-2 font-medium'>Meaning</th></tr></thead>
              <tbody className='divide-y'>{dataset.fields?.map((field) => <tr key={field.name}>
                <td className='px-4 py-2 align-top font-medium'>{field.name}{field.dimension?.is_time ? <span className='ms-1 font-normal text-muted-foreground'>(date)</span> : null}</td>
                <td className='px-4 py-2 align-top'>{field.datatype || 'Unspecified'}</td>
                <td className='max-w-xs break-words px-4 py-2 align-top font-mono'>{expressionText(field.expression) || 'Unspecified'}</td>
                <td className='max-w-xs break-words px-4 py-2 align-top'>
                  {field.description || 'No description'}
                  {field.synonyms?.length ? <p className='mt-1 text-muted-foreground'>Also known as: {field.synonyms.join(', ')}</p> : null}
                  {field.sample_values?.length ? <p className='mt-1 text-muted-foreground'>Examples: {field.sample_values.map(contextText).join(', ')}</p> : null}
                  {field.ai_context ? <p className='mt-1 text-muted-foreground'>Context: {contextText(field.ai_context)}</p> : null}
                </td>
              </tr>)}</tbody>
            </table>
          </div> : <p className='px-4 py-3 text-sm text-muted-foreground'>No fields defined.</p>}
        </details>)}
      </div>
    </DefinitionSection>

    <DefinitionSection title='Metrics' empty={metrics.length === 0}>
      <div className='divide-y rounded-lg border'>{metrics.map((metric) => <div key={metric.name} className='grid min-w-0 gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]'>
        <div><h4 className='font-medium'>{metric.name}</h4><p className='text-xs text-muted-foreground'>{metric.datatype || 'Type unspecified'}{metric.base_dataset ? ` · ${metric.base_dataset}` : ''}</p>
          {metric.description ? <p className='mt-1 text-sm text-muted-foreground'>{metric.description}</p> : null}</div>
        <div className='min-w-0 space-y-2'><pre className='overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-muted px-3 py-2 font-mono text-xs'>{expressionText(metric.expression) || 'No expression'}</pre>
          <Props items={[
            ['Derived from', Array.isArray(metric.dependencies) ? metric.dependencies.join(', ') : ''],
            ['Also known as', metric.synonyms?.join(', ')],
            ['Default date', metric.default_time_dimension],
            ['Display', [metric.format, metric.currency, metric.unit].filter(Boolean).join(' · ')],
            ['Additivity', metric.additivity ? String(metric.additivity) : ''],
            ['Grain', metric.grain && Object.keys(metric.grain).length ? contextText(metric.grain) : ''],
            ['Allowed breakdowns', metric.allowed_dimensions?.join(', ')],
            ['Not additive over', metric.non_additive_dimensions?.join(', ')],
            ['Filters', metric.filters?.map(contextText).join('; ')],
            ['Join path', metric.preferred_relationship_path?.join(' → ')],
            ['Visibility', metric.visibility],
            ['Governance', [metric.owner_domain, metric.authority, ...(metric.supporting_domains ?? [])].filter(Boolean).join(' · ')],
            ['Context', metric.ai_context ? contextText(metric.ai_context) : ''],
          ]} />
        </div>
      </div>)}</div>
    </DefinitionSection>

    <DefinitionSection title='Relationships' empty={relationships.length === 0}>
      <div className='divide-y rounded-lg border'>{relationships.map((relationship) => <div key={relationship.name} className='px-4 py-3 text-sm'>
        <p className='font-medium'>{relationship.name}</p>
        <p className='mt-1 break-words font-mono text-xs'>{relationship.from}.{(relationship.from_columns ?? []).join(', ')} = {relationship.to}.{(relationship.to_columns ?? []).join(', ')}</p>
        {relationship.cardinality ? <p className='mt-1 text-xs text-muted-foreground'>Cardinality: {String(relationship.cardinality)}</p> : null}
        {relationship.preferred ? <p className='mt-1 text-xs text-muted-foreground'>Preferred join path</p> : null}
        {relationship.ai_context ? <p className='mt-1 text-xs text-muted-foreground'>Context: {contextText(relationship.ai_context)}</p> : null}
      </div>)}</div>
    </DefinitionSection>

    <DefinitionSection title='Hierarchies' empty={hierarchies.length === 0}>
      <div className='divide-y rounded-lg border'>{hierarchies.map(([name, levels]) => <div key={name} className='flex flex-wrap justify-between gap-2 px-4 py-3 text-sm'>
        <span className='font-medium'>{name}</span><span className='break-words font-mono text-xs'>{Array.isArray(levels) ? levels.join(' → ') : JSON.stringify(levels)}</span>
      </div>)}</div>
    </DefinitionSection>

    <DefinitionSection title='Named filters' empty={namedFilters.length === 0}>
      <div className='divide-y rounded-lg border'>{namedFilters.map((filter) => <div key={filter.name} className='grid gap-1 px-4 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]'>
        <div><p className='font-medium'>{filter.name}</p>{filter.description ? <p className='text-xs text-muted-foreground'>{filter.description}</p> : null}
          {filter.dataset ? <p className='text-xs text-muted-foreground'>Dataset: {filter.dataset}</p> : null}
          {filter.synonyms?.length ? <p className='text-xs text-muted-foreground'>Also known as: {filter.synonyms.join(', ')}</p> : null}
          {filter.ai_context ? <p className='text-xs text-muted-foreground'>Context: {contextText(filter.ai_context)}</p> : null}</div>
        <code className='break-words text-xs'>{expressionText(filter.expression) || 'No expression'}</code>
      </div>)}</div>
    </DefinitionSection>

    {entities.length ? <DefinitionSection title='Entities'>
      <div className='divide-y rounded-lg border'>{entities.map(([name, value]) => <div key={name} className='grid gap-1 px-4 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]'>
        <span className='font-medium'>{name}</span><code className='break-words text-xs'>{JSON.stringify(value)}</code>
      </div>)}</div>
    </DefinitionSection> : null}

    {definition.question_routing_instructions || definition.query_generation_instructions ? <DefinitionSection title='Agent guidance'>
      {definition.question_routing_instructions ? <div><h4 className='text-xs font-medium'>Question routing</h4><p className='mt-1 whitespace-pre-wrap text-sm'>{definition.question_routing_instructions}</p></div> : null}
      {definition.query_generation_instructions ? <div><h4 className='text-xs font-medium'>Query generation</h4><p className='mt-1 whitespace-pre-wrap text-sm'>{definition.query_generation_instructions}</p></div> : null}
    </DefinitionSection> : null}
    </> : null}

    <details className='border-t pt-5'>
      <summary className='cursor-pointer text-sm font-semibold'>Raw Ossie definition (advanced)</summary>
      <p className='text-xs text-muted-foreground'>{legacyReview ? 'Review copy stored with this imported version. The untouched original appears below.' : `Normalized JSON stored with version v${version.version}. Use “Use as draft” in Version history to edit it.`}</p>
      <pre className='max-h-96 overflow-auto rounded-lg border bg-muted p-4 font-mono text-xs'>{JSON.stringify(definition, null, 2)}</pre>
    </details>
    {legacyReview && migration?.raw_definition ? <DefinitionSection title='Original imported definition'>
      <p className='text-xs text-muted-foreground'>Preserved from Agent Studio for review. Its format is not executable by this View.</p>
      <pre className='max-h-96 overflow-auto rounded-lg border bg-muted p-4 font-mono text-xs'>{JSON.stringify(migration.raw_definition, null, 2)}</pre>
    </DefinitionSection> : null}
  </div>
}

/** Names from a plan list whose items are strings or `{name}` objects. */
function planNames(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) =>
    typeof item === 'string' ? [item]
      : item && typeof item === 'object' && typeof (item as { name?: unknown }).name === 'string'
        ? [(item as { name: string }).name] : [])
}

function PreviewDetail({ result }: { result: SemanticPreview }) {
  const plan = (result.semantic_plan ?? {}) as Record<string, unknown>
  const answer = [
    ['Measures', planNames(plan.metrics)],
    ['Broken down by', planNames(plan.dimensions)],
    ['Filters', planNames(plan.filters ?? plan.named_filters)],
  ] as const
  return <div className='space-y-4' aria-live='polite'>
    <section className='space-y-2 rounded-lg border bg-surface-2 p-4 text-sm'>
      <h4 className='font-medium'>How this view would answer</h4>
      <dl className='grid gap-x-6 gap-y-1 sm:grid-cols-[auto_1fr]'>
        {answer.filter(([, names]) => names.length).map(([label, names]) => <div key={label} className='contents'>
          <dt className='text-muted-foreground'>{label}</dt><dd>{names.map((name) => name.replace(/_/g, ' ')).join(', ')}</dd></div>)}
        <dt className='text-muted-foreground'>Answered from</dt>
        <dd>{result.plan_source === 'verified_query' ? 'an approved example' : 'AI, checked against these definitions'}</dd>
        {result.relationship_path.length ? <><dt className='text-muted-foreground'>Tables joined</dt><dd>{result.relationship_path.map((name) => name.replace(/_/g, ' ')).join(' → ')}</dd></> : null}
      </dl>
    </section>
    {result.warnings.length ? <div className='rounded-lg border border-warning bg-warning/10 p-4 text-sm'><p className='font-medium'>Worth checking</p><ul className='mt-2 list-disc space-y-1 ps-5'>{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div> : null}
    <details className='rounded-lg border p-4 text-sm'>
      <summary className='cursor-pointer text-muted-foreground'>Technical details</summary>
      <section className='mt-3'><h4 className='text-sm font-medium'>Semantic plan</h4><pre className='mt-2 max-h-64 overflow-auto rounded-lg border bg-muted p-4 font-mono text-xs'>{JSON.stringify(result.semantic_plan, null, 2)}</pre></section>
      <section className='mt-3'><h4 className='text-sm font-medium'>Generated StarRocks SQL</h4><pre className='mt-2 max-h-64 overflow-auto rounded-lg border bg-muted p-4 font-mono text-xs'>{result.generated_sql}</pre></section>
    </details>
  </div>
}

export function SemanticViewDetailPanel({ view, version, onVersionCreated }: {
  view: SemanticViewDetail
  version: SemanticVersion
  onVersionCreated: (version: number) => void
}) {
  const queryClient = useQueryClient()
  const epoch = useAuthStore((state) => state.securityEpoch)
  const [question, setQuestion] = useState('')
  const [preview, setPreview] = useState<SemanticPreview | null>(null)
  const [previewQuestion, setPreviewQuestion] = useState('')
  const [activeTab, setActiveTab] = useState('preview')
  const latestVersion = Math.max(...view.versions.map((item) => item.version))
  const legacyReview = version.validation?.migration?.raw_definition_preserved === true
  const verified = version.definition.verified_queries ?? []

  const previewMutation = useMutation({
    mutationFn: () => semanticViewsApi.preview(view.id, version.version, question.trim()),
    onMutate: () => setPreview(null),
    onSuccess: (result) => { setPreview(result); setPreviewQuestion(question.trim()) },
    onError: (error: Error) => toast.error(error.message),
  })
  const saveVerified = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error('Run a preview first')
      return semanticViewsApi.saveVerifiedQuery(view.id, version.version, {
        question: previewQuestion,
        semantic_plan: preview.semantic_plan,
        verified_sql: preview.generated_sql,
      })
    },
    onSuccess: async (created) => {
      toast.success(`Verified question added to draft v${created.version}`)
      await queryClient.invalidateQueries({ queryKey: ['intelligence', 'semantic'] })
      onVersionCreated(created.version)
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const quality = useQuery({
    queryKey: ['intelligence', 'semantic', view.id, 'quality', version.version, epoch],
    queryFn: () => semanticViewsApi.quality(view.id, version.version),
    enabled: activeTab === 'quality',
  })

  return <Tabs value={activeTab} onValueChange={setActiveTab} className='min-w-0'>
    <TabsList indicatorVariant='underline' className='mb-4 h-auto w-full max-w-full justify-start overflow-x-auto rounded-none border-b bg-transparent p-0'>
      {[['preview', 'Try a question'], ['definition', 'Definitions'], ['quality', 'Health check'],
        ['verified', 'Approved examples'], ['context', 'Concepts'], ['autopilot', 'Suggestions']].map(([value, label]) =>
        <TabsTrigger key={value} value={value} className='flex-none rounded-none px-3 py-2'>{label}</TabsTrigger>)}
    </TabsList>
    <TabsContent value='context' className='mt-0 min-w-0'>
      <ContextInspector semantic={{ view_id: view.id, version: version.version, fingerprint: version.fingerprint }} />
    </TabsContent>
    <TabsContent value='autopilot' className='mt-0 min-w-0'>
      <AutopilotPanel view={view} version={version} onVersionCreated={onVersionCreated} />
    </TabsContent>
    <TabsContent value='definition' className='mt-0 min-w-0'>
      <DefinitionDetail view={view} version={version} />
    </TabsContent>
    <TabsContent value='preview' className='mt-0 min-w-0 space-y-4'>
      {legacyReview ? <EmptyState title='Legacy version cannot be previewed' description='Add a supported Ossie 0.1.1 version, validate it, then preview business questions.' /> : <>
      <p className='text-sm text-muted-foreground'>Nova shows how this version would answer, without running it on the data.</p>
      <form className='flex flex-col gap-2 sm:flex-row sm:items-end' onSubmit={(event) => {
        event.preventDefault()
        if (question.trim()) previewMutation.mutate()
      }}>
        <div className='min-w-0 flex-1 space-y-1.5'><Label htmlFor='semantic-question'>Business question</Label><Input id='semantic-question' value={question} onChange={(event) => setQuestion(event.target.value)} placeholder='Revenue by region last month' /></div>
        <Button type='submit' disabled={!question.trim() || previewMutation.isPending}>Try question</Button>
      </form>
      {previewMutation.isPending ? <LoadingOverlay label='Working out the answer' /> : previewMutation.isError ?
        <EmptyState variant='error' title='Could not preview this question' description='Check the question or try again.' action={<Button variant='outline' onClick={() => previewMutation.mutate()}>Retry preview</Button>} /> : preview ? <>
        <PreviewDetail result={preview} />
        <div className='space-y-2 rounded-lg border p-4'>
          <Button variant='outline' disabled={saveVerified.isPending || version.version !== latestVersion} onClick={() => saveVerified.mutate()}>Save as approved example</Button>
          <p className='text-xs text-muted-foreground'>Saving creates a new draft version. Check it and make it live before agents use the example.</p>
          {version.version !== latestVersion ? <p className='text-xs text-warning-strong'>Open the latest version to save an approved example.</p> : null}
          {saveVerified.isError ? <p role='alert' className='text-xs text-destructive'>Could not save this example. Try the question again, then save.</p> : null}
        </div>
      </> : <EmptyState title='No question tried yet' description='Ask something like “revenue by city last month” to see which measures and breakdowns this version would use.' />}
      </>}
    </TabsContent>
    <TabsContent value='quality' className='mt-0 min-w-0 space-y-5'>
      {quality.isPending ? <LoadingOverlay label='Loading quality report' /> : quality.isError ?
        <EmptyState variant='error' title='Could not load quality report' description='Try again to inspect this version.' action={<Button variant='outline' onClick={() => void quality.refetch()}>Retry</Button>} /> :
        <>
          <div className='flex flex-wrap items-center gap-3'>
            <StatusBadge tone={quality.data.valid ? 'success' : 'danger'}>{quality.data.valid ? 'Ready to use' : 'Needs fixes'}</StatusBadge>
            <span className='text-sm text-muted-foreground'>{quality.data.valid ? 'Nothing blocks this version from going live' : 'Fix the problems below before it can go live'}</span>
          </div>
          {quality.data.errors.length ? <section><h4 className='text-sm font-medium'>Problems</h4><ul className='mt-2 list-disc space-y-1 ps-5 text-sm text-destructive'>{quality.data.errors.map((error) => <li key={error}>{error}</li>)}</ul></section> : null}
          {(() => {
            const ratios = Object.entries(COMPLETENESS).filter(([key]) => typeof quality.data.quality[key] === 'number')
            const examples = quality.data.quality.verified_query_count
            return <DefinitionSection title='Completeness' empty={!ratios.length && typeof examples !== 'number'}>
              <dl className='grid gap-x-8 gap-y-3 sm:grid-cols-2'>
                {ratios.map(([key, label]) => {
                  const percent = Math.round(Number(quality.data.quality[key]) * 100)
                  return <div key={key} className='space-y-1.5 text-sm'>
                    <div className='flex justify-between gap-2'><dt>{label}</dt><dd className='tabular-nums text-muted-foreground'>{percent}%</dd></div>
                    <div className='h-1 overflow-hidden rounded-full bg-muted' aria-hidden='true'><div className={`h-full rounded-full ${percent === 100 ? 'bg-success' : 'bg-muted-foreground/60'}`} style={{ width: `${percent}%` }} /></div>
                  </div>
                })}
                {typeof examples === 'number' ? <div className='flex justify-between gap-2 text-sm'><dt>Approved examples</dt><dd className='tabular-nums text-muted-foreground'>{examples}</dd></div> : null}
              </dl>
            </DefinitionSection>
          })()}
          {(() => {
            // The same advice for many fields reads as one item with the fields named.
            const groups = new Map<string, { severity: string, message: string, objects: string[] }>()
            for (const finding of quality.data.findings) {
              const key = `${finding.severity}|${finding.message}`
              const group = groups.get(key) ?? { severity: finding.severity, message: finding.message, objects: [] }
              if (finding.object_name) group.objects.push(finding.object_name.replace(/_/g, ' '))
              groups.set(key, group)
            }
            return <DefinitionSection title='Suggestions to improve' empty={!groups.size}>
              <ul className='divide-y rounded-lg border'>{[...groups.values()].map((group) => <li key={`${group.severity}-${group.message}`} className='space-y-1 px-4 py-3 text-sm'>
                <div className='flex flex-wrap items-start gap-2'>
                  <StatusBadge tone={group.severity === 'error' ? 'danger' : group.severity === 'warning' ? 'warning' : 'neutral'}>{SEVERITY[group.severity] ?? group.severity}</StatusBadge>
                  <p className='min-w-0 flex-1'>{group.message}</p>
                </div>
                {group.objects.length ? <p className='text-xs text-muted-foreground'>{group.objects.length > 6 ? `${group.objects.slice(0, 6).join(', ')} and ${group.objects.length - 6} more` : group.objects.join(', ')}</p> : null}
              </li>)}</ul>
            </DefinitionSection>
          })()}
          {quality.data.quality_lab?.total ? <DefinitionSection title='Approved examples still give the same answer'>
            <p className='text-sm'>{quality.data.quality_lab.matched} of {quality.data.quality_lab.total} give the same answer; {quality.data.quality_lab.changed} changed and need a look.</p>
            {quality.data.quality_lab.unevaluated ? <p className='text-xs text-muted-foreground'>{quality.data.quality_lab.unevaluated} could not be evaluated.</p> : null}
            <div className='divide-y rounded-lg border'>{quality.data.quality_lab.cases.map((item) => <div key={item.verified_query_id} className='flex flex-wrap justify-between gap-2 px-4 py-3 text-sm'><span>{item.question}</span><StatusBadge tone={item.status === 'matched' ? 'success' : 'warning'}>{item.status === 'matched' ? 'Same answer' : 'Answer changed'}</StatusBadge></div>)}</div>
          </DefinitionSection> : null}
        </>}
    </TabsContent>
    <TabsContent value='verified' className='mt-0 min-w-0'>
      {verified.length ? <div className='divide-y rounded-lg border'>{verified.map((item, index) => <article key={item.verified_query_id ?? `${item.question}-${index}`} className='space-y-2 p-4'>
        <h4 className='text-sm font-medium'>{item.question}</h4>
        {item.verified_by || item.tags?.length ? <p className='text-xs text-muted-foreground'>{[item.verified_by ? `Approved by ${item.verified_by}` : '', item.tags?.length ? `Tags: ${item.tags.join(', ')}` : ''].filter(Boolean).join(' · ')}</p> : null}
        <details className='text-xs'><summary className='cursor-pointer text-muted-foreground'>Technical details</summary>
        <pre className='mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 font-mono text-xs'>{item.verified_sql}</pre>
        <pre className='mt-2 max-h-48 overflow-auto rounded-md bg-muted p-3 font-mono'>{JSON.stringify(item.semantic_plan, null, 2)}</pre></details>
      </article>)}</div> : <EmptyState title='No approved examples yet' description='Try a question, and if the answer is right, save it as an approved example so agents answer it the same way every time.' />}
    </TabsContent>
  </Tabs>
}
