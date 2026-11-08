import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Inbox } from 'lucide-react'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines } from '@/components/ui/loading-overlay'
import { StatusBadge } from '@/components/ui/status-badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ontologyApi, type Domain, type Proposal, type ProposalStatus } from './api'
import {
  PROPOSAL_TONE,
  conceptNames,
  describeChange,
  formatDate,
  useDomains,
  useIsAdmin,
  useOntologyKey,
  usePublishedViews,
  useWorkingConcepts,
} from './model'
import { ReviewDialog } from './proposal-forms'

type Filter = 'review' | 'approved' | 'applied' | 'closed'

const FILTERS: Record<Filter, { label: string; statuses: ProposalStatus[]; empty: string }> = {
  review: {
    label: 'In review',
    statuses: ['PENDING'],
    empty: 'Nothing is waiting for review. New definitions and changes appear here.',
  },
  approved: {
    label: 'Approved',
    statuses: ['APPROVED'],
    empty: 'Approved proposals are applied right away; one appears here only if applying stopped.',
  },
  applied: {
    label: 'Applied',
    statuses: ['APPLIED'],
    empty: 'No proposal has been applied yet.',
  },
  closed: {
    label: 'Closed',
    statuses: ['REJECTED', 'WITHDRAWN'],
    empty: 'No rejected or withdrawn proposals.',
  },
}

const STATUS_LABEL: Record<ProposalStatus, string> = {
  PENDING: 'In review',
  APPROVED: 'Approved, not applied',
  APPLIED: 'Applied',
  REJECTED: 'Rejected',
  WITHDRAWN: 'Withdrawn',
}

const SOURCE_LABEL: Record<Proposal['source'], string> = {
  USER: 'Proposed',
  BOOTSTRAP: 'Drafted from a Semantic View',
  AGENT: 'Suggested by an agent',
  AUTOPILOT: 'Suggested by Autopilot',
  NEWS: 'Suggested from News',
  ENGINE: 'Derived by Nova, sent',
}

export function ProposalsTab() {
  const [filter, setFilter] = useState<Filter>('review')
  const [reviewing, setReviewing] = useState<{
    proposal: Proposal
    decision: 'APPROVE' | 'REJECT'
  } | null>(null)
  const proposals = useQuery({
    queryKey: useOntologyKey('proposals', 'all'),
    queryFn: () => ontologyApi.proposals(),
  })
  const domains = useDomains()
  const views = usePublishedViews()
  const concepts = useWorkingConcepts()
  const domainMap = useMemo(
    () => new Map((domains.data ?? []).map((item) => [item.id, item])),
    [domains.data]
  )
  const all = proposals.data ?? []
  const shown = all.filter((item) => FILTERS[filter].statuses.includes(item.status))
  const counts = (key: Filter) =>
    all.filter((item) => FILTERS[key].statuses.includes(item.status)).length

  return (
    <div className='flex flex-col gap-4'>
      <Tabs value={filter} onValueChange={(value) => setFilter(value as Filter)}>
        <TabsList aria-label='Proposal status'>
          {(Object.keys(FILTERS) as Filter[]).map((key) => (
            <TabsTrigger key={key} value={key}>
              {FILTERS[key].label}
              {proposals.data && counts(key) ? (
                <span className='ml-1.5 text-xs text-muted-foreground'>{counts(key)}</span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {proposals.isLoading ? (
        <LoadingLines rows={4} />
      ) : proposals.isError ? (
        <EmptyState
          variant='error'
          icon={Inbox}
          title='Proposals could not be loaded'
          description={proposals.error.message}
          action={<Button variant='outline' onClick={() => proposals.refetch()}>Try again</Button>}
        />
      ) : shown.length === 0 ? (
        <EmptyState icon={Inbox} title={`No proposals ${FILTERS[filter].label.toLowerCase()}`} description={FILTERS[filter].empty} />
      ) : (
        <ul className='grid gap-3'>
          {shown.map((proposal) => (
            <li key={proposal.id}>
              <ProposalCard
                proposal={proposal}
                domains={domainMap}
                names={conceptNames(
                  proposal.changes,
                  concepts.data?.concepts ?? [],
                  domainMap,
                  views.data ?? []
                )}
                onReview={(decision) => setReviewing({ proposal, decision })}
              />
            </li>
          ))}
        </ul>
      )}

      <ReviewDialog
        proposal={reviewing?.proposal ?? null}
        decision={reviewing?.decision ?? 'APPROVE'}
        onOpenChange={(open) => !open && setReviewing(null)}
      />
    </div>
  )
}

function ProposalCard({
  proposal,
  domains,
  names,
  onReview,
}: {
  proposal: Proposal
  domains: Map<string, Domain>
  names: Map<string, string>
  onReview: (decision: 'APPROVE' | 'REJECT') => void
}) {
  const queryClient = useQueryClient()
  const prefix = useOntologyKey()
  const username = useAuthStore((state) => state.auth.user?.username)
  const isAdmin = useIsAdmin()
  const mine = proposal.submitter === username
  const stewarded = proposal.domain_ids.filter((item) => domains.get(item)?.can_review)
  const approved = new Set(
    proposal.reviews.filter((item) => item.decision === 'APPROVE').flatMap((item) => item.domain_ids)
  )
  const waiting = proposal.domain_ids.filter((item) => !approved.has(item))
  const canReview = proposal.status === 'PENDING' && !mine && stewarded.length > 0
  const pathOf = (domainId: string) => domains.get(domainId)?.qualified_path ?? 'a domain you cannot see'

  const after = (message: string) => () => {
    toast.success(message)
    queryClient.invalidateQueries({ queryKey: prefix })
  }
  const onError = (error: Error) => toast.error(error.message)
  const withdraw = useMutation({
    mutationFn: () => ontologyApi.withdraw(proposal.id),
    onSuccess: after('Proposal withdrawn'),
    onError,
  })
  const apply = useMutation({
    mutationFn: () => ontologyApi.apply(proposal.id),
    onSuccess: after('Proposal applied'),
    onError,
  })

  return (
    <article className='rounded-lg border bg-card p-4'>
      <header className='flex flex-wrap items-start justify-between gap-2'>
        <div className='min-w-0'>
          <h3 className='font-medium break-words'>{proposal.title}</h3>
          <p className='text-xs text-muted-foreground'>
            {SOURCE_LABEL[proposal.source]} by {proposal.submitter}, {formatDate(proposal.created_at)}
          </p>
        </div>
        <StatusBadge tone={PROPOSAL_TONE[proposal.status]}>{STATUS_LABEL[proposal.status]}</StatusBadge>
      </header>

      <p className='mt-3 text-sm whitespace-pre-line'>{proposal.rationale}</p>

      <ul className='mt-3 grid gap-1 text-sm'>
        {proposal.changes.map((change, index) => (
          <li key={index} className='break-words'>
            {describeChange(change, names)}
            {change.target === 'relation' && change.justification ? (
              <p className='text-xs text-muted-foreground'>{change.justification}</p>
            ) : null}
          </li>
        ))}
      </ul>

      <dl className='mt-3 grid gap-1 text-xs text-muted-foreground'>
        <div>
          <dt className='inline'>Domains: </dt>
          <dd className='inline'>{proposal.domain_ids.map(pathOf).join(', ')}</dd>
        </div>
        {proposal.status === 'PENDING' && waiting.length ? (
          <div>
            <dt className='inline'>Waiting for a steward of: </dt>
            <dd className='inline'>{waiting.map(pathOf).join(', ')}</dd>
          </div>
        ) : null}
      </dl>

      {proposal.reviews.length ? (
        <ol className='mt-3 grid gap-2 border-t pt-3 text-sm'>
          {proposal.reviews.map((review) => (
            <li key={review.operation_id}>
              <span className='font-medium'>{review.principal}</span>{' '}
              <span className='text-muted-foreground'>as {review.active_role}</span>{' '}
              {review.decision === 'APPROVE' ? 'approved' : 'rejected'} for{' '}
              {review.domain_ids.map(pathOf).join(', ')}
              <p className='text-xs text-muted-foreground'>
                {review.rationale}, {formatDate(review.at)}
              </p>
            </li>
          ))}
        </ol>
      ) : null}

      <footer className='mt-4 flex flex-wrap gap-2'>
        {canReview ? (
          <>
            <Button size='sm' onClick={() => onReview('APPROVE')}>Approve</Button>
            <Button size='sm' variant='outline' onClick={() => onReview('REJECT')}>Reject</Button>
          </>
        ) : null}
        {proposal.status === 'PENDING' && mine ? (
          <p className='self-center text-xs text-muted-foreground'>
            Another steward reviews your proposal.
          </p>
        ) : null}
        {proposal.status === 'APPROVED' && (isAdmin || stewarded.length > 0) ? (
          <Button size='sm' disabled={apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? 'Applying' : 'Finish applying'}
          </Button>
        ) : null}
        {mine && (proposal.status === 'PENDING' || proposal.status === 'APPROVED') ? (
          <Button
            size='sm'
            variant='ghost'
            disabled={withdraw.isPending}
            onClick={() => withdraw.mutate()}
          >
            Withdraw
          </Button>
        ) : null}
      </footer>
    </article>
  )
}
