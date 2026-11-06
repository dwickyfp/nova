import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { semanticViewsApi } from '@/features/intelligence/semantic-views-api'
import {
  ontologyApi,
  operationId,
  type Change,
  type Classification,
  type Concept,
  type ConceptBody,
  type ConceptKind,
  type Proposal,
  type RelationType,
} from './api'
import {
  KIND_LABELS,
  RELATION_LABELS,
  splitList,
  useDomains,
  useOntologyKey,
  useWorkingConcepts,
} from './model'

const KINDS = Object.keys(KIND_LABELS) as ConceptKind[]
const RELATIONS = Object.keys(RELATION_LABELS) as RelationType[]
const CLASSIFICATIONS: Classification[] = [
  'INTERNAL',
  'CONFIDENTIAL',
  'RESTRICTED',
  'HIGHLY_SENSITIVE',
]
const BINDABLE: ConceptKind[] = ['METRIC', 'MEASURE']
const SLUG = /^[A-Za-z][A-Za-z0-9_]{0,63}$/
const NO_VIEWS =
  'No published Semantic View is available to your role. Publish one under Semantic Views first.'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The request failed'
}

/** Submits a proposal once; a retry of the same form reuses its idempotency key. */
function useSubmitProposal(onDone: (proposal: Proposal) => void) {
  const queryClient = useQueryClient()
  const prefix = useOntologyKey()
  const [key] = useState(operationId)
  return useMutation({
    mutationFn: (body: { title: string; rationale: string; changes: Change[] }) =>
      ontologyApi.propose({ ...body, operation_id: key }),
    onSuccess: (proposal) => {
      toast.success('Proposal sent for review')
      queryClient.invalidateQueries({ queryKey: prefix })
      onDone(proposal)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
}

function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className='grid content-start gap-1.5'>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint ? <p className='text-xs text-muted-foreground'>{hint}</p> : null}
    </div>
  )
}

function useMetricOptions(viewId: string) {
  return useQuery({
    queryKey: useOntologyKey('view-metrics', viewId),
    enabled: Boolean(viewId),
    queryFn: async () => {
      const view = await semanticViewsApi.get(viewId)
      const active = view.versions.find((item) => item.version === view.active_version)
      return (active?.definition.metrics ?? [])
        .filter((metric) => (metric.visibility ?? 'public') === 'public')
        .map((metric) => ({ name: metric.name, unit: metric.unit ?? null }))
    },
  })
}

function usePublishedViews() {
  return useQuery({
    queryKey: useOntologyKey('semantic-views'),
    queryFn: async () =>
      (await semanticViewsApi.list()).filter((view) => view.active_version !== null),
  })
}

type ConceptDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Revise this concept; without it the dialog proposes a new one. */
  concept?: Concept
  onProposed?: (proposal: Proposal) => void
}

export function ConceptDialog(props: ConceptDialogProps) {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className='max-h-[90vh] overflow-y-auto sm:max-w-2xl'>
        {props.open ? <ConceptForm {...props} /> : null}
      </DialogContent>
    </Dialog>
  )
}

function ConceptForm({ concept, onOpenChange, onProposed }: ConceptDialogProps) {
  const domains = useDomains()
  const views = usePublishedViews()
  const [body, setBody] = useState<ConceptBody>(() => ({
    domain_id: concept?.domain_id ?? '',
    kind: concept?.kind ?? 'METRIC',
    slug: concept?.slug ?? '',
    label: concept?.label ?? '',
    definition: concept?.definition ?? '',
    aliases: concept?.aliases ?? [],
    unit: concept?.unit ?? null,
    grain: concept?.grain ?? null,
    time_semantics: concept?.time_semantics ?? null,
    exclusions: concept?.exclusions ?? [],
    classification: concept?.classification ?? 'INTERNAL',
  }))
  const [aliases, setAliases] = useState((concept?.aliases ?? []).join(', '))
  const [exclusions, setExclusions] = useState((concept?.exclusions ?? []).join('\n'))
  const [rationale, setRationale] = useState('')
  const [viewId, setViewId] = useState('')
  const [metric, setMetric] = useState('')
  const metrics = useMetricOptions(viewId)
  const submit = useSubmitProposal((proposal) => {
    onOpenChange(false)
    onProposed?.(proposal)
  })
  const set = <K extends keyof ConceptBody>(key: K, value: ConceptBody[K]) =>
    setBody((current) => ({ ...current, [key]: value }))

  const domainPath = domains.data?.find((item) => item.id === body.domain_id)?.qualified_path
  const slugValid = SLUG.test(body.slug)
  const bindable = BINDABLE.includes(body.kind)
  const ready =
    body.domain_id && slugValid && body.label.trim() && body.definition.trim() && rationale.trim()

  const send = () => {
    const full: ConceptBody = {
      ...body,
      label: body.label.trim(),
      definition: body.definition.trim(),
      aliases: splitList(aliases),
      exclusions: splitList(exclusions),
      unit: body.unit?.trim() || null,
      grain: body.grain?.trim() || null,
      time_semantics: body.time_semantics?.trim() || null,
    }
    const changes: Change[] = concept
      ? [
          {
            target: 'concept',
            op: 'update',
            concept_id: concept.id,
            base_revision: concept.revision,
            body: full,
          },
        ]
      : [{ target: 'concept', op: 'create', body: full }]
    if (bindable && viewId && metric) {
      changes.push({
        target: 'representation',
        op: 'create',
        concept_id: concept ? concept.id : '@0',
        view_id: viewId,
        metric,
      })
    }
    submit.mutate({
      title: concept
        ? `Revise ${concept.qualified_name}`
        : `Add ${domainPath ?? 'concept'}.${full.slug}`,
      rationale: rationale.trim(),
      changes,
    })
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{concept ? `Revise ${concept.qualified_name}` : 'Propose a concept'}</DialogTitle>
        <DialogDescription>
          A steward of the domain reviews the definition before it becomes official. It is used
          by Studio after the next release is published.
        </DialogDescription>
      </DialogHeader>
      <div className='grid gap-4 sm:grid-cols-2'>
        <Field id='concept-domain' label='Domain'>
          <Select
            value={body.domain_id}
            onValueChange={(value) => set('domain_id', value)}
            disabled={Boolean(concept)}
          >
            <SelectTrigger id='concept-domain' className='w-full'>
              <SelectValue placeholder='Choose a domain' />
            </SelectTrigger>
            <SelectContent>
              {(domains.data ?? [])
                .filter((item) => item.status === 'ACTIVE')
                .map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.qualified_path}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        </Field>
        <Field id='concept-kind' label='Kind'>
          <Select value={body.kind} onValueChange={(value) => set('kind', value as ConceptKind)}>
            <SelectTrigger id='concept-kind' className='w-full'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map((kind) => (
                <SelectItem key={kind} value={kind}>
                  {KIND_LABELS[kind]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field
          id='concept-slug'
          label='Name'
          hint={
            body.slug && !slugValid
              ? 'Start with a letter; use letters, digits and underscores only.'
              : domainPath && body.slug
                ? `Official name: ${domainPath}.${body.slug}`
                : 'For example NetRevenue. It is part of the official name.'
          }
        >
          <Input
            id='concept-slug'
            value={body.slug}
            onChange={(event) => set('slug', event.target.value.trim())}
            aria-invalid={Boolean(body.slug) && !slugValid}
          />
        </Field>
        <Field id='concept-label' label='Label'>
          <Input
            id='concept-label'
            value={body.label}
            onChange={(event) => set('label', event.target.value)}
          />
        </Field>
        <div className='sm:col-span-2'>
          <Field id='concept-definition' label='Definition'>
            <Textarea
              id='concept-definition'
              rows={4}
              value={body.definition}
              onChange={(event) => set('definition', event.target.value)}
            />
          </Field>
        </div>
        <Field
          id='concept-aliases'
          label='Other names'
          hint='Separate with commas. Add the words people use in questions, in every language.'
        >
          <Input
            id='concept-aliases'
            value={aliases}
            onChange={(event) => setAliases(event.target.value)}
          />
        </Field>
        <Field id='concept-unit' label='Unit'>
          <Input
            id='concept-unit'
            value={body.unit ?? ''}
            onChange={(event) => set('unit', event.target.value)}
          />
        </Field>
        <Field id='concept-grain' label='Grain'>
          <Input
            id='concept-grain'
            value={body.grain ?? ''}
            placeholder='Customer per day'
            onChange={(event) => set('grain', event.target.value)}
          />
        </Field>
        <Field id='concept-time' label='Time semantics'>
          <Input
            id='concept-time'
            value={body.time_semantics ?? ''}
            placeholder='Recognised on invoice date, Asia/Jakarta'
            onChange={(event) => set('time_semantics', event.target.value)}
          />
        </Field>
        <Field id='concept-exclusions' label='Exclusions' hint='One per line.'>
          <Textarea
            id='concept-exclusions'
            rows={2}
            value={exclusions}
            onChange={(event) => setExclusions(event.target.value)}
          />
        </Field>
        <Field id='concept-classification' label='Classification'>
          <Select
            value={body.classification}
            onValueChange={(value) => set('classification', value as Classification)}
          >
            <SelectTrigger id='concept-classification' className='w-full'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CLASSIFICATIONS.map((item) => (
                <SelectItem key={item} value={item}>
                  {item.replace('_', ' ').toLowerCase()}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        {bindable && views.data?.length === 0 ? (
          <p className='text-sm text-muted-foreground sm:col-span-2'>
            {NO_VIEWS} You can bind a metric later with a revision.
          </p>
        ) : null}
        {bindable && views.data?.length !== 0 ? (
          <fieldset className='grid gap-4 rounded-md border p-3 sm:col-span-2 sm:grid-cols-2'>
            <legend className='px-1 text-sm font-medium'>Computed by (optional)</legend>
            <Field id='binding-view' label='Semantic View'>
              <Select
                value={viewId}
                onValueChange={(value) => {
                  setViewId(value)
                  setMetric('')
                }}
              >
                <SelectTrigger id='binding-view' className='w-full'>
                  <SelectValue
                    placeholder={views.isLoading ? 'Loading views' : 'Choose a published view'}
                  />
                </SelectTrigger>
                <SelectContent>
                  {(views.data ?? []).map((view) => (
                    <SelectItem key={view.id} value={view.id}>
                      {view.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field id='binding-metric' label='Metric'>
              <Select value={metric} onValueChange={setMetric} disabled={!viewId}>
                <SelectTrigger id='binding-metric' className='w-full'>
                  <SelectValue placeholder='Choose a metric' />
                </SelectTrigger>
                <SelectContent>
                  {(metrics.data ?? []).map((item) => (
                    <SelectItem key={item.name} value={item.name}>
                      {item.unit ? `${item.name} (${item.unit})` : item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </fieldset>
        ) : null}
        <div className='sm:col-span-2'>
          <Field
            id='concept-rationale'
            label='Why this change'
            hint='Reviewers read this before they approve.'
          >
            <Textarea
              id='concept-rationale'
              rows={2}
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
          </Field>
        </div>
      </div>
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button onClick={send} disabled={!ready || submit.isPending}>
          {submit.isPending ? 'Sending' : 'Send for review'}
        </Button>
      </DialogFooter>
    </>
  )
}

export function RelationDialog({
  source,
  open,
  onOpenChange,
}: {
  source: Concept
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-lg'>
        {open ? <RelationForm source={source} onOpenChange={onOpenChange} /> : null}
      </DialogContent>
    </Dialog>
  )
}

function RelationForm({
  source,
  onOpenChange,
}: {
  source: Concept
  onOpenChange: (open: boolean) => void
}) {
  const concepts = useWorkingConcepts()
  const [type, setType] = useState<RelationType>('RELATED_TO')
  const [targetId, setTargetId] = useState('')
  const [justification, setJustification] = useState('')
  const submit = useSubmitProposal(() => onOpenChange(false))
  const targets = (concepts.data?.concepts ?? []).filter((item) => item.id !== source.id)
  const target = targets.find((item) => item.id === targetId)
  return (
    <>
      <DialogHeader>
        <DialogTitle>Link {source.qualified_name}</DialogTitle>
        <DialogDescription>
          A link records meaning between concepts. Related means relevant, never caused by.
        </DialogDescription>
      </DialogHeader>
      <div className='grid gap-4'>
        <Field id='relation-type' label='Relation'>
          <Select value={type} onValueChange={(value) => setType(value as RelationType)}>
            <SelectTrigger id='relation-type' className='w-full'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RELATIONS.map((item) => (
                <SelectItem key={item} value={item}>
                  {RELATION_LABELS[item]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field id='relation-target' label='Concept'>
          <Select value={targetId} onValueChange={setTargetId}>
            <SelectTrigger id='relation-target' className='w-full'>
              <SelectValue
                placeholder={concepts.isLoading ? 'Loading concepts' : 'Choose a concept'}
              />
            </SelectTrigger>
            <SelectContent>
              {targets.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.qualified_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field id='relation-justification' label='Justification'>
          <Textarea
            id='relation-justification'
            rows={3}
            value={justification}
            onChange={(event) => setJustification(event.target.value)}
          />
        </Field>
      </div>
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          disabled={!target || !justification.trim() || submit.isPending}
          onClick={() =>
            target &&
            submit.mutate({
              title: `Link ${source.qualified_name} to ${target.qualified_name}`,
              rationale: justification.trim(),
              changes: [
                {
                  target: 'relation',
                  op: 'create',
                  source_id: source.id,
                  type,
                  target_id: target.id,
                  justification: justification.trim(),
                },
              ],
            })
          }
        >
          {submit.isPending ? 'Sending' : 'Send for review'}
        </Button>
      </DialogFooter>
    </>
  )
}

export function DeprecateDialog({
  concept,
  open,
  onOpenChange,
}: {
  concept: Concept
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-lg'>
        {open ? <DeprecateForm concept={concept} onOpenChange={onOpenChange} /> : null}
      </DialogContent>
    </Dialog>
  )
}

function DeprecateForm({
  concept,
  onOpenChange,
}: {
  concept: Concept
  onOpenChange: (open: boolean) => void
}) {
  const related = useQuery({
    queryKey: useOntologyKey('related', concept.id, 'working'),
    queryFn: () => ontologyApi.related(concept.id, 'working'),
  })
  const bindings = useQuery({
    queryKey: useOntologyKey('bindings', concept.id, 'working'),
    queryFn: () => ontologyApi.bindings(concept.id, 'working'),
  })
  const [rationale, setRationale] = useState('')
  const submit = useSubmitProposal(() => onOpenChange(false))
  const links = (related.data?.relations ?? []).filter(
    (item) => item.source_id === concept.id || item.target_id === concept.id
  )
  const live = (bindings.data?.representations ?? []).filter((item) => item.status !== 'REVOKED')
  const loading = related.isLoading || bindings.isLoading
  const changes: Change[] = [
    { target: 'concept', op: 'deprecate', concept_id: concept.id, base_revision: concept.revision },
    ...links.map(
      (item): Change => ({
        target: 'relation',
        op: 'retire',
        source_id: item.source_id,
        type: item.type,
        target_id: item.target_id,
      })
    ),
    ...live.map(
      (item): Change => ({
        target: 'representation',
        op: 'revoke',
        representation_id: item.id,
        base_revision: item.revision,
      })
    ),
  ]
  return (
    <>
      <DialogHeader>
        <DialogTitle>Deprecate {concept.qualified_name}</DialogTitle>
        <DialogDescription>
          {loading
            ? 'Checking what depends on this concept.'
            : `The proposal also removes ${links.length} link${links.length === 1 ? '' : 's'} and revokes ${live.length} metric binding${live.length === 1 ? '' : 's'}. Released answers keep their history.`}
        </DialogDescription>
      </DialogHeader>
      <Field id='deprecate-rationale' label='Why this change'>
        <Textarea
          id='deprecate-rationale'
          rows={3}
          value={rationale}
          onChange={(event) => setRationale(event.target.value)}
        />
      </Field>
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          variant='destructive'
          disabled={loading || !rationale.trim() || submit.isPending}
          onClick={() =>
            submit.mutate({
              title: `Deprecate ${concept.qualified_name}`,
              rationale: rationale.trim(),
              changes,
            })
          }
        >
          {submit.isPending ? 'Sending' : 'Send for review'}
        </Button>
      </DialogFooter>
    </>
  )
}

export function BootstrapDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-lg'>
        {open ? <BootstrapForm onOpenChange={onOpenChange} /> : null}
      </DialogContent>
    </Dialog>
  )
}

function BootstrapForm({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const domains = useDomains()
  const views = usePublishedViews()
  const queryClient = useQueryClient()
  const prefix = useOntologyKey()
  const [domainId, setDomainId] = useState('')
  const [viewId, setViewId] = useState('')
  const [key] = useState(operationId)
  const draft = useMutation({
    mutationFn: () => ontologyApi.bootstrap({ view_id: viewId, domain_id: domainId, operation_id: key }),
    onSuccess: (proposal) => {
      const count = proposal.changes.filter((item) => item.target === 'concept').length
      toast.success(`${count} draft concept${count === 1 ? '' : 's'} sent for review`)
      queryClient.invalidateQueries({ queryKey: prefix })
      onOpenChange(false)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  return (
    <>
      <DialogHeader>
        <DialogTitle>Draft from a Semantic View</DialogTitle>
        <DialogDescription>
          Every public metric not yet named in the domain becomes a draft concept bound to that
          metric. A steward reviews and edits the definitions before approving.
        </DialogDescription>
      </DialogHeader>
      {views.data?.length === 0 ? (
        <p className='text-sm text-muted-foreground'>{NO_VIEWS}</p>
      ) : null}
      <div className={views.data?.length === 0 ? 'hidden' : 'grid gap-4'}>
        <Field id='bootstrap-view' label='Semantic View'>
          <Select value={viewId} onValueChange={setViewId}>
            <SelectTrigger id='bootstrap-view' className='w-full'>
              <SelectValue
                placeholder={views.isLoading ? 'Loading views' : 'Choose a published view'}
              />
            </SelectTrigger>
            <SelectContent>
              {(views.data ?? []).map((view) => (
                <SelectItem key={view.id} value={view.id}>
                  {view.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field id='bootstrap-domain' label='Domain'>
          <Select value={domainId} onValueChange={setDomainId}>
            <SelectTrigger id='bootstrap-domain' className='w-full'>
              <SelectValue placeholder='Choose a domain' />
            </SelectTrigger>
            <SelectContent>
              {(domains.data ?? [])
                .filter((item) => item.status === 'ACTIVE')
                .map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.qualified_path}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        </Field>
      </div>
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button disabled={!viewId || !domainId || draft.isPending} onClick={() => draft.mutate()}>
          {draft.isPending ? 'Drafting' : 'Draft concepts'}
        </Button>
      </DialogFooter>
    </>
  )
}

export function ReviewDialog({
  proposal,
  decision,
  onOpenChange,
}: {
  proposal: Proposal | null
  decision: 'APPROVE' | 'REJECT'
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={proposal !== null} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-lg'>
        {proposal ? (
          <ReviewForm proposal={proposal} decision={decision} onOpenChange={onOpenChange} />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function ReviewForm({
  proposal,
  decision,
  onOpenChange,
}: {
  proposal: Proposal
  decision: 'APPROVE' | 'REJECT'
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const prefix = useOntologyKey()
  const [rationale, setRationale] = useState('')
  const [key] = useState(operationId)
  const review = useMutation({
    mutationFn: () =>
      ontologyApi.review(proposal.id, { decision, rationale: rationale.trim(), operation_id: key }),
    onSuccess: (result) => {
      toast.success(
        result.status === 'APPLIED'
          ? 'Approved and applied'
          : result.status === 'PENDING'
            ? 'Approved; other domains still need a steward'
            : 'Proposal rejected'
      )
      queryClient.invalidateQueries({ queryKey: prefix })
      onOpenChange(false)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const approve = decision === 'APPROVE'
  return (
    <>
      <DialogHeader>
        <DialogTitle>{approve ? 'Approve' : 'Reject'} {proposal.title}</DialogTitle>
        <DialogDescription>
          {approve
            ? 'Your approval covers the domains your active role stewards. The change is applied when every affected domain has approved.'
            : 'A rejection closes the proposal. The submitter can send a corrected one.'}
        </DialogDescription>
      </DialogHeader>
      <Field id='review-rationale' label={approve ? 'What you checked' : 'Why it is rejected'}>
        <Textarea
          id='review-rationale'
          rows={3}
          value={rationale}
          onChange={(event) => setRationale(event.target.value)}
        />
      </Field>
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          variant={approve ? 'default' : 'destructive'}
          disabled={!rationale.trim() || review.isPending}
          onClick={() => review.mutate()}
        >
          {review.isPending ? 'Saving' : approve ? 'Approve' : 'Reject'}
        </Button>
      </DialogFooter>
    </>
  )
}
