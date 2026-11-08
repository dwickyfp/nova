import { useDeferredValue, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Network, RefreshCw, Search as SearchIcon, Send } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingLines } from '@/components/ui/loading-overlay'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ontologyApi, operationId, type DerivedItem, type DerivedPromotion } from './api'
import { useDomains, useOntologyKey } from './model'

const METHOD_LABELS: Record<string, string> = {
  dataset: 'Dataset with a grain',
  field: 'Categorical or date field',
  relationship: 'Declared relationship',
  metric: 'Published metric',
  metric_expression: 'Metric expression',
  claim: 'Identical metric definition',
}

/** How the derived graph hangs together, read once per response. */
function useGraph(items: DerivedItem[]) {
  return useMemo(() => {
    const byKey = new Map(items.map((item) => [item.key, item]))
    const relations = items.filter((item) => item.item_type === 'relation')
    const entities = items
      .filter((item) => item.concept_kind === 'BUSINESS_ENTITY')
      .sort((a, b) => a.label.localeCompare(b.label))
    const into = (key: string, type: string) =>
      relations.filter((item) => item.relation_type === type && item.target_key === key)
    const from = (key: string, type: string) =>
      relations.filter((item) => item.relation_type === type && item.source_key === key)
    const kind = (key: string | null) => (key ? byKey.get(key)?.concept_kind : undefined)
    /** Dimensions point at the entity they describe with RELATED_TO. */
    const dimensionsOf = (key: string) =>
      into(key, 'RELATED_TO').filter((item) => kind(item.source_key) === 'DIMENSION')
    /** Entity-to-entity references, as opposed to the dimensions above. */
    const links = (relations: DerivedItem[]) =>
      relations.filter(
        (item) =>
          kind(item.source_key) === 'BUSINESS_ENTITY' && kind(item.target_key) === 'BUSINESS_ENTITY'
      )
    return { byKey, entities, into, from, dimensionsOf, links }
  }, [items])
}

function percent(value: number) {
  return `${Math.round(value * 100)}%`
}

/** The table an entity stands for: two "Product" entities differ by it. */
function source(item: DerivedItem) {
  return item.key.replace(/^entity:/, '')
}

function views(item: DerivedItem) {
  return new Set(item.evidence.map((evidence) => evidence.semantic.view_id)).size
}

export function DerivedTab() {
  const client = useQueryClient()
  const key = useOntologyKey('derived')
  const layer = useQuery({ queryKey: key, queryFn: ontologyApi.derived })
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [proposing, setProposing] = useState(false)
  const query = useDeferredValue(search.trim().toLowerCase())
  const items = layer.data?.items ?? []
  const graph = useGraph(items)
  const compile = useMutation({
    mutationFn: () => ontologyApi.compileDerived(),
    onSuccess: (result) => {
      toast.success(
        result.compilations.length
          ? `Compiled ${result.compilations.length} Semantic View${result.compilations.length === 1 ? '' : 's'}`
          : 'No published Semantic View to compile'
      )
      void client.invalidateQueries({ queryKey: key })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const entities = graph.entities.filter((entity) => {
    if (!query) return true
    const haystack = [entity.label, ...entity.aliases, ...entity.roles].join(' ').toLowerCase()
    return haystack.includes(query)
  })
  const counts = {
    entities: graph.entities.length,
    dimensions: items.filter((item) => item.concept_kind === 'DIMENSION').length,
    metrics: items.filter((item) => item.concept_kind === 'METRIC').length,
    relations: items.filter((item) => item.item_type === 'relation').length,
  }

  return (
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-start justify-between gap-3'>
        <p className='max-w-2xl text-sm text-muted-foreground'>
          What Nova proves from published Semantic Views, with the evidence for each item.
          Studio may use it as context. It is never an official definition until a steward
          approves it.
        </p>
        {items.length ? (
          <div className='flex flex-wrap gap-2'>
            <Button
              variant='outline'
              size='sm'
              disabled={compile.isPending}
              onClick={() => compile.mutate()}
            >
              <RefreshCw className='size-3.5' />
              {compile.isPending ? 'Compiling…' : 'Compile now'}
            </Button>
            <Button size='sm' onClick={() => setProposing(true)}>
              <Send className='size-3.5' />
              Propose to stewards
            </Button>
          </div>
        ) : null}
      </div>

      {layer.isLoading ? (
        <LoadingLines rows={6} />
      ) : layer.isError ? (
        <EmptyState
          variant='error'
          icon={Network}
          title='The derived layer could not be loaded'
          description={layer.error.message}
          action={<Button variant='outline' onClick={() => layer.refetch()}>Try again</Button>}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Network}
          title='Nothing derived yet'
          description='Nova compiles a Semantic View when it is published. Compile now to derive from the views already published.'
          action={
            <Button onClick={() => compile.mutate()} disabled={compile.isPending}>
              {compile.isPending ? 'Compiling…' : 'Compile now'}
            </Button>
          }
        />
      ) : (
        <>
          <dl className='grid grid-cols-2 gap-3 sm:grid-cols-4'>
            {(
              [
                ['Entities', counts.entities],
                ['Dimensions', counts.dimensions],
                ['Metrics', counts.metrics],
                ['Relations', counts.relations],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className='rounded-lg border px-3 py-2'>
                <dt className='text-xs text-muted-foreground'>{label}</dt>
                <dd className='text-lg font-medium tabular-nums'>{value}</dd>
              </div>
            ))}
          </dl>
          <div className='relative max-w-sm'>
            <SearchIcon className='pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground' />
            <Input
              aria-label='Search entities'
              placeholder='Entity, alias or role'
              className='pl-8'
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          {entities.length === 0 ? (
            <EmptyState
              icon={SearchIcon}
              title='No entity matches'
              description='Try another name, alias or role.'
              action={<Button variant='outline' onClick={() => setSearch('')}>Clear search</Button>}
            />
          ) : (
            <div className='overflow-x-auto rounded-lg border'>
              <Table className='min-w-[40rem]'>
                <TableHeader>
                  <TableRow>
                    <TableHead>Entity</TableHead>
                    <TableHead>Roles</TableHead>
                    <TableHead className='text-right'>Dimensions</TableHead>
                    <TableHead className='text-right'>Metrics</TableHead>
                    <TableHead className='text-right'>Views</TableHead>
                    <TableHead className='text-right'>Confidence</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entities.map((entity) => (
                    <TableRow
                      key={entity.key}
                      className='cursor-pointer'
                      onClick={() => setSelected(entity.key)}
                    >
                      <TableCell>
                        <button
                          type='button'
                          className='text-left font-medium hover:underline'
                          onClick={() => setSelected(entity.key)}
                        >
                          {entity.label}
                        </button>
                        <div className='text-xs text-muted-foreground'>{source(entity)}</div>
                      </TableCell>
                      <TableCell className='text-muted-foreground'>
                        {entity.roles.length ? entity.roles.join(', ') : '—'}
                      </TableCell>
                      <TableCell className='text-right tabular-nums'>
                        {graph.dimensionsOf(entity.key).length}
                      </TableCell>
                      <TableCell className='text-right tabular-nums'>
                        {graph.into(entity.key, 'MEASURES').length}
                      </TableCell>
                      <TableCell className='text-right tabular-nums'>{views(entity)}</TableCell>
                      <TableCell className='text-right tabular-nums'>
                        {percent(entity.confidence)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </>
      )}

      <PromoteDialog open={proposing} onOpenChange={setProposing} />

      <Sheet open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent className='w-full gap-0 overflow-y-auto sm:max-w-xl'>
          {selected && graph.byKey.get(selected) ? (
            <EntityDetail
              entity={graph.byKey.get(selected) as DerivedItem}
              graph={graph}
              onOpen={setSelected}
            />
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
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

function EntityDetail({
  entity,
  graph,
  onOpen,
}: {
  entity: DerivedItem
  graph: ReturnType<typeof useGraph>
  onOpen: (key: string) => void
}) {
  const label = (key: string | null) => (key ? graph.byKey.get(key)?.label ?? key : '')
  const dimensions = graph
    .dimensionsOf(entity.key)
    .map((relation) => graph.byKey.get(relation.source_key ?? ''))
    .filter((item): item is DerivedItem => Boolean(item))
  const metrics = graph
    .into(entity.key, 'MEASURES')
    .map((relation) => graph.byKey.get(relation.source_key ?? ''))
    .filter((item): item is DerivedItem => Boolean(item))
  const outgoing = graph.links(graph.from(entity.key, 'RELATED_TO'))
  const incoming = graph.links(graph.into(entity.key, 'RELATED_TO'))

  return (
    <>
      <SheetHeader className='px-4 pt-4'>
        <div className='flex flex-wrap items-center gap-2'>
          <StatusBadge tone='info'>Derived entity</StatusBadge>
          <span className='text-xs text-muted-foreground tabular-nums'>
            {percent(entity.confidence)} confidence
          </span>
        </div>
        <SheetTitle>{entity.label}</SheetTitle>
        <p className='text-xs text-muted-foreground'>{source(entity)}</p>
        <SheetDescription>
          {entity.definition || 'No description in the Semantic Views that define it.'}
        </SheetDescription>
      </SheetHeader>

      {entity.roles.length || entity.aliases.length ? (
        <Section title='Also known as'>
          <p className='text-sm'>
            {[...entity.roles.map((role) => `${role} (role)`), ...entity.aliases].join(', ')}
          </p>
        </Section>
      ) : null}

      <Section title='Dimensions'>
        {dimensions.length ? (
          <ul className='flex flex-wrap gap-1.5'>
            {dimensions.map((item) => (
              <li key={item.key} className='rounded-md border px-2 py-0.5 text-sm'>
                {item.label}
                {item.is_time ? <span className='text-muted-foreground'> · date</span> : null}
                {item.aliases.length ? (
                  <span className='text-muted-foreground'> · {item.aliases.join(', ')}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className='text-sm text-muted-foreground'>No categorical or date field.</p>
        )}
      </Section>

      <Section title='Metrics measured on it'>
        {metrics.length ? (
          <ul className='flex flex-col gap-2'>
            {metrics.map((metric) => {
              const parts = graph.into(metric.key, 'DERIVES')
              return (
                <li key={metric.key} className='text-sm'>
                  <div className='flex flex-wrap items-center gap-2'>
                    <span className='font-medium'>{metric.label}</span>
                    {metric.unit ? (
                      <span className='text-muted-foreground'>{metric.unit}</span>
                    ) : null}
                    {metric.official_concept ? (
                      <StatusBadge tone='success'>Official: {metric.official_concept}</StatusBadge>
                    ) : null}
                  </div>
                  {metric.definition ? (
                    <p className='text-muted-foreground'>{metric.definition}</p>
                  ) : null}
                  {parts.length ? (
                    <p className='text-muted-foreground'>
                      Built from {parts.map((part) => label(part.source_key)).join(' and ')}
                    </p>
                  ) : null}
                </li>
              )
            })}
          </ul>
        ) : (
          <p className='text-sm text-muted-foreground'>No metric uses this entity as its base.</p>
        )}
      </Section>

      <Section title='Related entities'>
        {outgoing.length || incoming.length ? (
          <ul className='flex flex-col gap-1 text-sm'>
            {outgoing.map((relation) => (
              <li key={relation.key}>
                Each {entity.label.toLowerCase()} refers to one{' '}
                <button
                  type='button'
                  className='font-medium hover:underline'
                  onClick={() => onOpen(relation.target_key ?? '')}
                >
                  {label(relation.target_key)}
                </button>
                {relation.role ? <span> as its {relation.role}</span> : null}
              </li>
            ))}
            {incoming.map((relation) => (
              <li key={relation.key}>
                <button
                  type='button'
                  className='font-medium hover:underline'
                  onClick={() => onOpen(relation.source_key ?? '')}
                >
                  {label(relation.source_key)}
                </button>{' '}
                refers to it{relation.role ? <span> as its {relation.role}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className='text-sm text-muted-foreground'>No declared relationship.</p>
        )}
      </Section>

      <Section title='Evidence'>
        <ul className='flex flex-col gap-1.5 text-sm'>
          {entity.evidence.map((evidence) => (
            <li key={`${evidence.semantic.view_id}:${evidence.source}`}>
              <span className='font-medium'>{evidence.view_name}</span>{' '}
              <span className='text-muted-foreground'>
                v{evidence.semantic.version} · {METHOD_LABELS[evidence.method] ?? evidence.method}{' '}
                · {evidence.source}
              </span>
            </li>
          ))}
        </ul>
      </Section>
    </>
  )
}

const NO_HOME = 'none'

function PromoteDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='max-h-[85dvh] overflow-y-auto sm:max-w-lg'>
        {open ? <PromoteForm onOpenChange={onOpenChange} /> : null}
      </DialogContent>
    </Dialog>
  )
}

function PromoteForm({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const client = useQueryClient()
  const prefix = useOntologyKey()
  const domains = useDomains()
  const [home, setHome] = useState(NO_HOME)
  const [key] = useState(operationId)
  const [result, setResult] = useState<DerivedPromotion | null>(null)
  const propose = useMutation({
    mutationFn: () =>
      ontologyApi.proposeDerived({
        operation_id: key,
        shared_domain_id: home === NO_HOME ? null : home,
      }),
    onSuccess: (value) => {
      setResult(value)
      void client.invalidateQueries({ queryKey: prefix })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (result) {
    const reasons = new Map<string, string[]>()
    for (const item of result.skipped) {
      reasons.set(item.reason, [...(reasons.get(item.reason) ?? []), item.label])
    }
    return (
      <>
        <DialogHeader>
          <DialogTitle>
            {result.proposals.length
              ? `${result.proposals.length} proposal${result.proposals.length === 1 ? '' : 's'} sent for review`
              : 'Nothing new to propose'}
          </DialogTitle>
          <DialogDescription>
            {result.proposals.length
              ? 'Each domain steward reviews the proposal for their domain under Proposals.'
              : 'Everything Nova derived is already official or waiting for review.'}
          </DialogDescription>
        </DialogHeader>
        {result.proposals.length ? (
          <ul className='grid gap-1 text-sm'>
            {result.proposals.map((proposal) => (
              <li key={proposal.id}>
                <span className='font-medium'>{proposal.title}</span>{' '}
                <span className='text-muted-foreground'>
                  · {proposal.changes.length} change{proposal.changes.length === 1 ? '' : 's'}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {reasons.size ? (
          <section className='grid gap-2 border-t pt-3 text-sm'>
            <h3 className='font-medium'>Left out</h3>
            {[...reasons].map(([reason, labels]) => (
              <div key={reason}>
                <p className='text-muted-foreground'>{reason}</p>
                <p>{labels.join(', ')}</p>
              </div>
            ))}
          </section>
        ) : null}
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Done</Button>
        </DialogFooter>
      </>
    )
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Propose to stewards</DialogTitle>
        <DialogDescription>
          Nova drafts one proposal per domain with the metrics, entities and links it proved,
          each with its evidence. A Semantic View belongs to the domain whose approved concepts
          it computes. Stewards approve or reject; nothing becomes official before that.
        </DialogDescription>
      </DialogHeader>
      <div className='grid gap-1.5'>
        <Label htmlFor='derived-home'>Domain for shared entities</Label>
        <Select value={home} onValueChange={setHome}>
          <SelectTrigger id='derived-home' className='w-full'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_HOME}>Leave shared entities out</SelectItem>
            {(domains.data ?? [])
              .filter((item) => item.status === 'ACTIVE')
              .map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.qualified_path}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
        <p className='text-xs text-muted-foreground'>
          An entity several domains use, such as Outlet, needs one home domain.
        </p>
      </div>
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button disabled={propose.isPending} onClick={() => propose.mutate()}>
          {propose.isPending ? 'Drafting…' : 'Draft proposals'}
        </Button>
      </DialogFooter>
    </>
  )
}
