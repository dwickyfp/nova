import { useAuthStore } from '@/stores/auth-store'
import { versionStatusWord } from './semantic-view-glance'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
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

function DefinitionSection({ title, children, empty }: {
  title: string
  children: React.ReactNode
  empty?: boolean
}) {
  return <section className='space-y-3 border-t pt-5' aria-label={title}>
    <h3 className='text-sm font-semibold'>{title}</h3>
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
    <dl className='grid gap-x-6 gap-y-3 rounded-lg border bg-surface-2 p-4 text-sm sm:grid-cols-2'>
      <div><dt className='text-muted-foreground'>Where the data lives</dt>
        <dd className='mt-1 break-all font-mono'>{[view.catalog_name, view.database_name, view.schema_name].filter(Boolean).join(' / ')}{legacyReview && view.database_name === 'NOVA_SYSTEM' ? <span className='ms-2 font-sans text-warning-strong'>(scope pending review)</span> : null}</dd></div>
      <div><dt className='text-muted-foreground'>Owner</dt><dd className='mt-1'>{view.owner_name || 'Unknown'}</dd></div>
      <div><dt className='text-muted-foreground'>View status</dt><dd className='mt-1'>{view.status === 'DEPRECATED' ? 'Retired' : view.active_version ? 'Live' : 'Draft'}</dd></div>
      {view.visibility ? <div><dt className='text-muted-foreground'>Who can see it</dt><dd className='mt-1'>{view.visibility === 'PRIVATE' ? 'Only its owner' : 'Everyone with access to the data'}</dd></div> : null}
      <div><dt className='text-muted-foreground'>Live version</dt><dd className='mt-1'>{view.active_version ? `v${view.active_version}` : 'None yet'}</dd></div>
      <div><dt className='text-muted-foreground'>Created</dt><dd className='mt-1'>{dateText(view.created_at)}</dd></div>
      <div><dt className='text-muted-foreground'>Last updated</dt><dd className='mt-1'>{dateText(view.updated_at)}</dd></div>
      <div><dt className='text-muted-foreground'>Version shown</dt><dd className='mt-1'>v{version.version} · {versionStatusWord(version.status, view.status === 'DEPRECATED')}</dd></div>
    </dl>
    {definition.description ? <p className='text-sm leading-6'>{definition.description}</p> : null}
    {definition.ai_context ? <p className='text-sm'><span className='font-medium'>Business context: </span>{contextText(definition.ai_context)}</p> : null}
    {legacyReview ? <p className='rounded-lg border border-warning bg-warning/10 p-4 text-sm'>Legacy format: add a new Ossie 0.1.1 version before validating or publishing. Review the original definition and verified questions below.</p> : null}

    {!legacyReview ? <>
    <DefinitionSection title='Datasets and fields' empty={datasets.length === 0}>
      <div className='space-y-4'>
        {datasets.map((dataset) => <article key={dataset.name} className='min-w-0 rounded-lg border'>
          <div className='flex flex-wrap items-start justify-between gap-2 border-b bg-surface-1 px-4 py-3'>
            <div className='min-w-0'>
              <h4 className='font-medium'>{dataset.name}</h4>
              <p className='break-all font-mono text-xs text-muted-foreground'>{dataset.source || 'No source'}</p>
              {dataset.description ? <p className='mt-1 text-sm text-muted-foreground'>{dataset.description}</p> : null}
              {dataset.synonyms?.length ? <p className='mt-1 text-xs text-muted-foreground'>Also known as: {dataset.synonyms.join(', ')}</p> : null}
              {dataset.ai_context ? <p className='mt-1 text-xs text-muted-foreground'>Context: {contextText(dataset.ai_context)}</p> : null}
            </div>
            {(dataset.grain?.keys?.length || dataset.primary_key?.length) ?
              <span className='text-xs text-muted-foreground'>Grain: {(dataset.grain?.keys?.length ? dataset.grain.keys : dataset.primary_key)?.join(', ')}</span> : null}
          </div>
          {dataset.unique_keys?.length ? <p className='border-b px-4 py-2 text-xs text-muted-foreground'>Unique keys: {dataset.unique_keys.map((keys) => Array.isArray(keys) ? keys.join(' + ') : keys).join('; ')}</p> : null}
          {(dataset.fields ?? []).length ? <div className='min-w-0 overflow-x-auto'>
            <table className='w-full min-w-[520px] text-left text-xs'>
              <thead className='text-muted-foreground'><tr><th className='px-4 py-2 font-medium'>Field</th><th className='px-4 py-2 font-medium'>Type</th><th className='px-4 py-2 font-medium'>Expression</th><th className='px-4 py-2 font-medium'>Meaning</th></tr></thead>
              <tbody className='divide-y'>{dataset.fields?.map((field) => <tr key={field.name}>
                <td className='px-4 py-2 align-top font-medium'>{field.name}{field.dimension ? <span className='ms-1 text-muted-foreground'>({field.dimension.is_time ? 'time dimension' : 'dimension'})</span> : null}</td>
                <td className='px-4 py-2 align-top'>{field.datatype || 'Unspecified'}</td>
                <td className='max-w-xs break-words px-4 py-2 align-top font-mono'>{expressionText(field.expression) || 'Unspecified'}</td>
                <td className='max-w-xs break-words px-4 py-2 align-top'>
                  {field.description || 'No description'}
                  {field.synonyms?.length ? <p className='mt-1 text-muted-foreground'>Also known as: {field.synonyms.join(', ')}</p> : null}
                  {field.search_strategy ? <p className='mt-1 text-muted-foreground'>Search: {field.search_strategy}</p> : null}
                  {field.sample_values?.length ? <p className='mt-1 text-muted-foreground'>Examples: {field.sample_values.map(contextText).join(', ')}</p> : null}
                  {field.ai_context ? <p className='mt-1 text-muted-foreground'>Context: {contextText(field.ai_context)}</p> : null}
                </td>
              </tr>)}</tbody>
            </table>
          </div> : <p className='px-4 py-3 text-sm text-muted-foreground'>No fields defined.</p>}
        </article>)}
      </div>
    </DefinitionSection>

    <DefinitionSection title='Metrics' empty={metrics.length === 0}>
      <div className='divide-y rounded-lg border'>{metrics.map((metric) => <div key={metric.name} className='grid min-w-0 gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]'>
        <div><h4 className='font-medium'>{metric.name}</h4><p className='text-xs text-muted-foreground'>{metric.datatype || 'Type unspecified'}{metric.base_dataset ? ` · ${metric.base_dataset}` : ''}</p>
          {metric.description ? <p className='mt-1 text-sm text-muted-foreground'>{metric.description}</p> : null}</div>
        <div className='min-w-0'><pre className='overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs'>{expressionText(metric.expression) || 'No expression'}</pre>
          {Array.isArray(metric.dependencies) && metric.dependencies.length ? <p className='mt-1 text-xs text-muted-foreground'>Derived from: {metric.dependencies.join(', ')}</p> : null}
          {metric.additivity ? <p className='mt-1 text-xs text-muted-foreground'>Additivity: {String(metric.additivity)}</p> : null}
          {metric.grain && Object.keys(metric.grain).length ? <p className='mt-1 text-xs text-muted-foreground'>Grain: {contextText(metric.grain)}</p> : null}
          {metric.format || metric.currency || metric.unit ? <p className='mt-1 text-xs text-muted-foreground'>Display: {[metric.format, metric.currency, metric.unit].filter(Boolean).join(' · ')}</p> : null}
          {metric.default_time_dimension ? <p className='mt-1 text-xs text-muted-foreground'>Time dimension: {metric.default_time_dimension}</p> : null}
          {metric.allowed_dimensions?.length ? <p className='mt-1 text-xs text-muted-foreground'>Allowed dimensions: {metric.allowed_dimensions.join(', ')}</p> : null}
          {metric.non_additive_dimensions?.length ? <p className='mt-1 text-xs text-muted-foreground'>Non additive dimensions: {metric.non_additive_dimensions.join(', ')}</p> : null}
          {metric.filters?.length ? <p className='mt-1 text-xs text-muted-foreground'>Filters: {metric.filters.map(contextText).join('; ')}</p> : null}
          {metric.preferred_relationship_path?.length ? <p className='mt-1 text-xs text-muted-foreground'>Preferred relationship path: {metric.preferred_relationship_path.join(' → ')}</p> : null}
          {metric.visibility ? <p className='mt-1 text-xs text-muted-foreground'>Visibility: {metric.visibility}</p> : null}
          {metric.synonyms?.length ? <p className='mt-1 text-xs text-muted-foreground'>Also known as: {metric.synonyms.join(', ')}</p> : null}
          {metric.owner_domain || metric.authority || metric.supporting_domains?.length ? <p className='mt-1 text-xs text-muted-foreground'>Governance: {[metric.owner_domain, metric.authority, ...(metric.supporting_domains ?? [])].filter(Boolean).join(' · ')}</p> : null}
          {metric.ai_context ? <p className='mt-1 text-xs text-muted-foreground'>Context: {contextText(metric.ai_context)}</p> : null}
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
    <div className='mb-3'>
      <h3 className='font-medium'>Try it and look inside</h3>
      <p className='text-sm text-muted-foreground'>Ask a business question to see how this version would answer it, or look at what it defines.</p>
    </div>
    <TabsList className='mb-4 max-w-full justify-start overflow-x-auto'>
      <TabsTrigger value='preview'>Try a question</TabsTrigger>
      <TabsTrigger value='definition'>What it defines</TabsTrigger>
      <TabsTrigger value='quality'>Health check</TabsTrigger>
      <TabsTrigger value='verified'>Approved examples</TabsTrigger>
      <TabsTrigger value='context'>Related concepts</TabsTrigger>
      <TabsTrigger value='autopilot'>Suggested additions</TabsTrigger>
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
      <p className='text-sm text-muted-foreground'>Type a question the way people ask it. Nova shows how this version would answer, without running it on the data.</p>
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
      </> : <EmptyState title='No question tried yet' description='Ask something like \u201crevenue by city last month\u201d to see which measures and breakdowns this version would use.' />}
      </>}
    </TabsContent>
    <TabsContent value='quality' className='mt-0 min-w-0 space-y-5'>
      {quality.isPending ? <LoadingOverlay label='Loading quality report' /> : quality.isError ?
        <EmptyState variant='error' title='Could not load quality report' description='Try again to inspect this version.' action={<Button variant='outline' onClick={() => void quality.refetch()}>Retry</Button>} /> :
        <>
          <div className='flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-surface-2 p-4'>
            <div className='flex items-center gap-2'><StatusBadge tone={quality.data.valid ? 'success' : 'danger'}>{quality.data.valid ? 'Ready to use' : 'Needs fixes'}</StatusBadge><span className='text-sm'>v{version.version}</span></div>
            
          </div>
          {quality.data.errors.length ? <section><h4 className='text-sm font-medium'>Errors</h4><ul className='mt-2 list-disc space-y-1 ps-5 text-sm'>{quality.data.errors.map((error) => <li key={error}>{error}</li>)}</ul></section> : null}
          <DefinitionSection title='Completeness' empty={!Object.keys(quality.data.quality).length}>
            <dl className='divide-y rounded-lg border'>{Object.entries(quality.data.quality).map(([key, value]) => <div key={key} className='flex flex-wrap justify-between gap-2 px-4 py-2 text-sm'><dt>{key.replace(/_/g, ' ')}</dt><dd className='font-medium'>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
          </DefinitionSection>
          <DefinitionSection title='Suggestions to improve' empty={!quality.data.findings.length}>
            <div className='divide-y rounded-lg border'>{quality.data.findings.map((finding, index) => <div key={`${finding.code}-${index}`} className='space-y-1 px-4 py-3 text-sm'><div className='flex flex-wrap items-center gap-2'><StatusBadge tone={finding.severity === 'error' ? 'danger' : 'warning'}>{finding.severity}</StatusBadge>{finding.object_name ? <span className='text-muted-foreground'>{finding.object_name}</span> : null}</div><p>{finding.message}</p></div>)}</div>
          </DefinitionSection>
          {quality.data.quality_lab ? <DefinitionSection title='Approved examples still give the same answer'>
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
