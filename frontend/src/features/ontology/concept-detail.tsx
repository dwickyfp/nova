import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines } from '@/components/ui/loading-overlay'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { StatusBadge } from '@/components/ui/status-badge'
import { ontologyApi, type Binding, type Concept, type Scope } from './api'
import {
  KIND_LABELS,
  RELATION_LABELS,
  formatDate,
  useConceptProposals,
  useOntologyKey,
} from './model'
import { ConceptDialog, DeprecateDialog, RelationDialog } from './proposal-forms'

export function ConceptSheet({
  conceptId,
  scope,
  onClose,
}: {
  conceptId: string | null
  scope: Scope
  onClose: () => void
}) {
  return (
    <Sheet open={conceptId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className='w-full gap-0 overflow-y-auto sm:max-w-xl'>
        {conceptId ? <ConceptDetail conceptId={conceptId} scope={scope} /> : null}
      </SheetContent>
    </Sheet>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className='border-t px-4 py-4'>
      <h3 className='mb-2 text-sm font-medium'>{title}</h3>
      {children}
    </section>
  )
}

function Fact({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <div className='grid grid-cols-[8rem_1fr] gap-2 text-sm'>
      <dt className='text-muted-foreground'>{label}</dt>
      <dd className='min-w-0 break-words'>{value}</dd>
    </div>
  )
}

function bindingStatus(binding: Binding) {
  if (binding.usable) return <StatusBadge tone='success'>In use</StatusBadge>
  if (binding.status === 'REVOKED') return <StatusBadge tone='neutral'>Revoked</StatusBadge>
  return <StatusBadge tone='warning'>Needs review</StatusBadge>
}

function ConceptDetail({ conceptId, scope }: { conceptId: string; scope: Scope }) {
  const detail = useQuery({
    queryKey: useOntologyKey('concept', conceptId, scope),
    queryFn: () => ontologyApi.concept(conceptId, scope),
  })
  const working = useQuery({
    queryKey: useOntologyKey('concept', conceptId, 'working'),
    queryFn: () => ontologyApi.concept(conceptId, 'working'),
    retry: false,
  })
  const bindings = useQuery({
    queryKey: useOntologyKey('bindings', conceptId, scope),
    queryFn: () => ontologyApi.bindings(conceptId, scope),
  })
  const related = useQuery({
    queryKey: useOntologyKey('related', conceptId, scope),
    queryFn: () => ontologyApi.related(conceptId, scope),
  })
  const pending = useConceptProposals(conceptId)
  const [dialog, setDialog] = useState<'revise' | 'link' | 'deprecate' | null>(null)

  if (detail.isLoading) {
    return (
      <div className='p-4'>
        <SheetHeader className='p-0'>
          <SheetTitle>Loading concept</SheetTitle>
          <SheetDescription className='sr-only'>Loading the concept definition</SheetDescription>
        </SheetHeader>
        <LoadingLines rows={5} className='mt-4' />
      </div>
    )
  }
  if (detail.isError || !detail.data) {
    return (
      <div className='p-4'>
        <SheetHeader className='p-0'>
          <SheetTitle>Concept unavailable</SheetTitle>
          <SheetDescription className='sr-only'>The concept could not be shown</SheetDescription>
        </SheetHeader>
        <EmptyState
          className='mt-4'
          variant='error'
          title='This concept is not available'
          description='It may not be in the published release, or your active role cannot see its domain.'
        />
      </div>
    )
  }

  const concept = detail.data.concept
  const editable: Concept | undefined =
    working.data?.concept.status === 'APPROVED' ? working.data.concept : undefined
  const names = new Map(
    [concept, ...(related.data?.concepts ?? [])].map((item) => [item.id, item.qualified_name])
  )

  return (
    <>
      <SheetHeader>
        <div className='flex flex-wrap items-center gap-2'>
          <StatusBadge tone='neutral'>{KIND_LABELS[concept.kind]}</StatusBadge>
          {concept.status === 'DEPRECATED' ? (
            <StatusBadge tone='warning'>Deprecated</StatusBadge>
          ) : null}
          {pending.length ? (
            <StatusBadge tone='info'>
              {pending.length} change{pending.length === 1 ? '' : 's'} in review
            </StatusBadge>
          ) : null}
        </div>
        <SheetTitle className='break-all'>{concept.qualified_name}</SheetTitle>
        <SheetDescription>{concept.label}</SheetDescription>
      </SheetHeader>

      <div className='px-4 pb-4'>
        <p className='text-sm whitespace-pre-line'>{concept.definition}</p>
        <dl className='mt-4 grid gap-2'>
          <Fact label='Unit' value={concept.unit} />
          <Fact label='Grain' value={concept.grain} />
          <Fact label='Time semantics' value={concept.time_semantics} />
          <Fact label='Other names' value={concept.aliases.join(', ')} />
          <Fact label='Excludes' value={concept.exclusions.join('; ')} />
          <Fact label='Classification' value={concept.classification.replace('_', ' ').toLowerCase()} />
          <Fact label='Version' value={`${concept.revision}, ${formatDate(concept.updated_at)}`} />
        </dl>
        {editable ? (
          <div className='mt-4 flex flex-wrap gap-2'>
            <Button size='sm' onClick={() => setDialog('revise')}>Propose a revision</Button>
            <Button size='sm' variant='outline' onClick={() => setDialog('link')}>
              Link a concept
            </Button>
            <Button size='sm' variant='outline' onClick={() => setDialog('deprecate')}>
              Deprecate
            </Button>
          </div>
        ) : null}
      </div>

      <Section title='Computed by'>
        {bindings.isLoading ? (
          <LoadingLines rows={2} />
        ) : bindings.isError ? (
          <p className='text-sm text-destructive'>Bindings could not be loaded.</p>
        ) : bindings.data?.representations.length ? (
          <ul className='grid gap-2'>
            {bindings.data.representations.map((binding) => (
              <li key={binding.id} className='rounded-md border p-3 text-sm'>
                <div className='flex flex-wrap items-center justify-between gap-2'>
                  <span className='font-mono text-xs break-all'>
                    {binding.semantic.view_id}.{binding.metric}
                  </span>
                  {bindingStatus(binding)}
                </div>
                <p className='mt-1 text-xs text-muted-foreground'>
                  Version {binding.semantic.version}
                  {binding.unit ? `, ${binding.unit}` : ''}
                  {binding.purpose !== 'general' ? `, for ${binding.purpose}` : ''}
                </p>
                {binding.status_reason && !binding.usable ? (
                  <p className='mt-1 text-xs text-warning-strong'>{binding.status_reason}</p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className='text-sm text-muted-foreground'>
            {concept.kind === 'METRIC' || concept.kind === 'MEASURE'
              ? 'No metric you can see computes this concept, so Studio answers that it cannot be computed.'
              : 'This kind of concept is not computed by a metric.'}
          </p>
        )}
      </Section>

      <Section title='Related concepts'>
        {related.isLoading ? (
          <LoadingLines rows={2} />
        ) : related.data?.relations.length ? (
          <ul className='grid gap-2 text-sm'>
            {related.data.relations.map((relation) => (
              <li key={relation.id}>
                <span>{names.get(relation.source_id) ?? relation.source_id}</span>{' '}
                <span className='text-muted-foreground'>{RELATION_LABELS[relation.type]}</span>{' '}
                <span>{names.get(relation.target_id) ?? relation.target_id}</span>
                <p className='text-xs text-muted-foreground'>{relation.justification}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className='text-sm text-muted-foreground'>No linked concepts you can see.</p>
        )}
      </Section>

      <Section title='History'>
        <ol className='grid gap-2 text-sm'>
          {detail.data.history.map((item) => (
            <li key={item.revision} className='flex flex-wrap justify-between gap-2'>
              <span>
                Version {item.revision}: {item.label}
                {item.status === 'DEPRECATED' ? ' (deprecated)' : ''}
              </span>
              <span className='text-xs text-muted-foreground'>
                {item.changed_by}, {formatDate(item.updated_at)}
              </span>
            </li>
          ))}
        </ol>
      </Section>

      {editable ? (
        <>
          <ConceptDialog
            open={dialog === 'revise'}
            onOpenChange={(open) => setDialog(open ? 'revise' : null)}
            concept={editable}
          />
          <RelationDialog
            source={editable}
            open={dialog === 'link'}
            onOpenChange={(open) => setDialog(open ? 'link' : null)}
          />
          <DeprecateDialog
            concept={editable}
            open={dialog === 'deprecate'}
            onOpenChange={(open) => setDialog(open ? 'deprecate' : null)}
          />
        </>
      ) : null}
    </>
  )
}
