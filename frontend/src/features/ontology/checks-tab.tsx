import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ListChecks, Sparkles } from 'lucide-react'
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
import { Label } from '@/components/ui/label'
import { LoadingLines } from '@/components/ui/loading-overlay'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { StatusBadge } from '@/components/ui/status-badge'
import { ontologyApi, operationId, type Enrichment, type LintFinding } from './api'
import { useDomains, useOntologyKey } from './model'

const RULE_LABELS: Record<LintFinding['rule'], string> = {
  definition_formula: 'Definition and formula disagree',
  adjustment_ignored: 'Returns may be counted',
  ambiguous_name: 'Name used in two domains',
  unbound_metric: 'No formula computes it',
}

export function ChecksTab() {
  const report = useQuery({ queryKey: useOntologyKey('lint'), queryFn: ontologyApi.lint })
  const [suggesting, setSuggesting] = useState(false)
  const findings = report.data?.findings ?? []

  return (
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-start justify-between gap-3'>
        <p className='max-w-2xl text-sm text-muted-foreground'>
          Nova compares each approved definition with the formula Studio runs, and the names
          people use with the concepts they mean. These are advice for stewards; releases list
          them as warnings and never block on them.
        </p>
        <Button size='sm' onClick={() => setSuggesting(true)}>
          <Sparkles className='size-3.5' />
          Suggest names
        </Button>
      </div>

      {report.isLoading ? (
        <LoadingLines rows={4} />
      ) : report.isError ? (
        <EmptyState
          variant='error'
          icon={ListChecks}
          title='The checks could not run'
          description={report.error.message}
          action={<Button variant='outline' onClick={() => report.refetch()}>Try again</Button>}
        />
      ) : findings.length === 0 ? (
        <EmptyState
          icon={ListChecks}
          title='No meaning problems found'
          description='Every approved definition you can see matches its formula, and no name means two concepts.'
        />
      ) : (
        <ul className='grid gap-3'>
          {findings.map((finding, index) => (
            <li key={`${finding.rule}:${finding.concept_id}:${index}`} className='rounded-lg border bg-card p-4'>
              <div className='flex flex-wrap items-center gap-2'>
                <StatusBadge tone={finding.severity === 'error' ? 'danger' : 'warning'}>
                  {finding.severity === 'error' ? 'Mismatch' : 'Check'}
                </StatusBadge>
                <span className='font-medium break-all'>{finding.concept}</span>
                <span className='text-xs text-muted-foreground'>{RULE_LABELS[finding.rule]}</span>
              </div>
              <p className='mt-2 text-sm'>{finding.message}</p>
              {finding.evidence.length ? (
                <ul className='mt-2 grid gap-0.5 text-xs text-muted-foreground'>
                  {finding.evidence.map((line) => (
                    <li key={line} className='break-words'>{line}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <Dialog open={suggesting} onOpenChange={setSuggesting}>
        <DialogContent className='max-h-[85dvh] overflow-y-auto sm:max-w-lg'>
          {suggesting ? <SuggestForm onOpenChange={setSuggesting} /> : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}

const ALL = 'all'

function SuggestForm({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const client = useQueryClient()
  const prefix = useOntologyKey()
  const domains = useDomains()
  const [domain, setDomain] = useState(ALL)
  const [key] = useState(operationId)
  const [result, setResult] = useState<Enrichment | null>(null)
  const suggest = useMutation({
    mutationFn: () =>
      ontologyApi.enrich({ operation_id: key, domain_id: domain === ALL ? null : domain }),
    onSuccess: (value) => {
      setResult(value)
      void client.invalidateQueries({ queryKey: prefix })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (result) {
    const reasons = new Map<string, string[]>()
    for (const item of result.rejected) {
      const value = item.value || item.concept
      reasons.set(item.reason, [...(reasons.get(item.reason) ?? []), value])
    }
    const count = result.proposals.length
    return (
      <>
        <DialogHeader>
          <DialogTitle>
            {count ? `${count} proposal${count === 1 ? '' : 's'} sent for review` : 'No new names to propose'}
          </DialogTitle>
          <DialogDescription>
            {count
              ? 'A steward checks that each word means exactly that concept before approving.'
              : 'Every concept already has the names suggested, or an open proposal changes it.'}
          </DialogDescription>
        </DialogHeader>
        {count ? (
          <ul className='grid gap-1 text-sm'>
            {result.proposals.map((proposal) => (
              <li key={proposal.id}>
                <span className='font-medium'>{proposal.title}</span>{' '}
                <span className='text-muted-foreground'>
                  · {proposal.changes.length} concept{proposal.changes.length === 1 ? '' : 's'}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {reasons.size ? (
          <section className='grid gap-2 border-t pt-3 text-sm'>
            <h3 className='font-medium'>Dropped by the validator</h3>
            {[...reasons].map(([reason, values]) => (
              <div key={reason}>
                <p className='text-muted-foreground'>{reason}</p>
                <p className='break-words'>{values.join(', ')}</p>
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
        <DialogTitle>Suggest names</DialogTitle>
        <DialogDescription>
          Relation roles such as driver become names directly. A language model suggests the
          everyday Indonesian and English words people use, and a definition where only a
          placeholder exists. It sees names, definitions and formulas, never data. Nova keeps
          only words no other concept uses, and sends one proposal per domain.
        </DialogDescription>
      </DialogHeader>
      <div className='grid gap-1.5'>
        <Label htmlFor='suggest-domain'>Domain</Label>
        <Select value={domain} onValueChange={setDomain}>
          <SelectTrigger id='suggest-domain' className='w-full'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Every domain you can see</SelectItem>
            {(domains.data ?? [])
              .filter((item) => item.status === 'ACTIVE')
              .map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.qualified_path}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </div>
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button disabled={suggest.isPending} onClick={() => suggest.mutate()}>
          {suggest.isPending ? 'Suggesting…' : 'Suggest names'}
        </Button>
      </DialogFooter>
    </>
  )
}
