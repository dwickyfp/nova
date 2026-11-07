import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FlaskConical, Play } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines } from '@/components/ui/loading-overlay'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ontologyApi, type EvaluationCase, type EvaluationRun } from './api'
import { formatDate, useIsAdmin, useOntologyKey } from './model'

const KIND_LABEL: Record<EvaluationRun['kind'], string> = {
  gold: 'Gold set',
  answers: 'Answers',
  claims: 'Claim audit',
}

const OUTCOME_TONE: Record<string, 'success' | 'danger' | 'info' | 'warning' | 'neutral'> = {
  correct: 'success',
  yes: 'success',
  wrong: 'danger',
  no: 'danger',
  answered: 'danger',
  clarified: 'info',
  abstained: 'info',
  failed: 'warning',
  unsure: 'neutral',
}

function headline(run: EvaluationRun): string {
  const summary = run.summary as Record<string, { value?: number | null; n?: number } | number | undefined>
  const pick = (key: string) => summary[key] as { value?: number | null; n?: number } | undefined
  if (run.kind === 'answers' && pick('answer_accuracy')?.value != null) {
    return `${Math.round((pick('answer_accuracy')?.value ?? 0) * 100)}% correct of ${pick('answer_accuracy')?.n}`
  }
  if (run.kind === 'claims' && pick('claim_accuracy')?.value != null) {
    return `${Math.round((pick('claim_accuracy')?.value ?? 0) * 100)}% of ${pick('claim_accuracy')?.n} claims`
  }
  if (run.kind === 'gold' && typeof summary.active === 'number') return `${summary.active} active cases`
  return run.status === 'RUNNING' ? `${run.done}/${run.total || '…'} · ${run.stage}` : '–'
}

/** Every evaluation run and its per-question evidence. Administrators only. */
export function EvaluationTab() {
  const isAdmin = useIsAdmin()
  const client = useQueryClient()
  const prefix = useOntologyKey()
  const runs = useQuery({
    queryKey: useOntologyKey('evaluation', 'runs'),
    queryFn: ontologyApi.evaluationRuns,
    enabled: isAdmin,
  })
  const [selected, setSelected] = useState<string | null>(null)
  const [progress, setProgress] = useState<string | null>(null)

  const advance = useMutation({
    mutationFn: async (run: EvaluationRun) => {
      let current = run
      // A run advances in small stored steps, so closing the page loses nothing.
      while (current.status === 'RUNNING') {
        current = await ontologyApi.stepRun(current.id)
        setProgress(`${KIND_LABEL[current.kind]}: ${current.done}/${current.total || '…'} ${current.stage}`)
      }
      return current
    },
    onSuccess: (run) => {
      setProgress(null)
      toast[run.status === 'COMPLETED' ? 'success' : 'error'](
        run.status === 'COMPLETED' ? `${KIND_LABEL[run.kind]} finished` : run.error ?? 'The run failed'
      )
      void client.invalidateQueries({ queryKey: prefix })
    },
    onError: (error: Error) => {
      setProgress(null)
      toast.error(error.message)
    },
  })
  const start = useMutation({
    mutationFn: (kind: 'with_ontology' | 'without_ontology' | 'claims') => {
      const setName = latestSet(runs.data ?? [])
      if (kind === 'claims') return ontologyApi.startClaims()
      if (!setName) throw new Error('Build a gold set first')
      return ontologyApi.startAnswers({ set_name: setName, mode: kind })
    },
    onSuccess: (run) => advance.mutate(run),
    onError: (error: Error) => toast.error(error.message),
  })

  if (!isAdmin) {
    return (
      <EmptyState
        icon={FlaskConical}
        title='Evaluation runs are for ACCOUNTADMIN'
        description='Runs execute every gold question as the person who starts them. The Overview shows their results to everyone.'
      />
    )
  }
  const busy = advance.isPending || start.isPending
  return (
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <p className='max-w-2xl text-sm text-muted-foreground'>
          Each run answers the gold set the way Studio does, or audits a sample of what the
          ontology claims. Results are stored per question; data rows never are.
        </p>
        <div className='flex flex-wrap gap-2'>
          <Button size='sm' variant='outline' disabled={busy} onClick={() => start.mutate('without_ontology')}>
            Run without ontology
          </Button>
          <Button size='sm' variant='outline' disabled={busy} onClick={() => start.mutate('claims')}>
            Audit claims
          </Button>
          <Button size='sm' disabled={busy} onClick={() => start.mutate('with_ontology')}>
            <Play className='size-3.5' />
            Run with ontology
          </Button>
        </div>
      </div>
      {progress ? (
        <p role='status' className='rounded-md border bg-muted/40 px-3 py-2 text-sm tabular-nums'>
          {progress}
        </p>
      ) : null}

      {runs.isLoading ? (
        <LoadingLines rows={5} />
      ) : runs.isError ? (
        <EmptyState
          variant='error'
          icon={FlaskConical}
          title='Runs could not be loaded'
          description={runs.error.message}
          action={<Button variant='outline' onClick={() => runs.refetch()}>Try again</Button>}
        />
      ) : !runs.data?.length ? (
        <EmptyState
          icon={FlaskConical}
          title='No evaluation yet'
          description='Build a gold set from published Semantic Views through the API, then run it here.'
        />
      ) : (
        <div className='overflow-x-auto rounded-lg border'>
          <Table className='min-w-[48rem]'>
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Gold set</TableHead>
                <TableHead>Release</TableHead>
                <TableHead>Result</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Started</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.data.map((run) => (
                <TableRow key={run.id} data-state={selected === run.id ? 'selected' : undefined}>
                  <TableCell>
                    <button
                      type='button'
                      className='font-medium hover:underline'
                      onClick={() => setSelected(run.id === selected ? null : run.id)}
                    >
                      {KIND_LABEL[run.kind]}
                      {run.mode ? ` · ${run.mode === 'with_ontology' ? 'with' : 'without'} ontology` : ''}
                    </button>
                  </TableCell>
                  <TableCell>{`${run.set_name} v${run.version}`}</TableCell>
                  <TableCell className='text-muted-foreground'>{run.release_id ?? '–'}</TableCell>
                  <TableCell className='tabular-nums'>{headline(run)}</TableCell>
                  <TableCell>
                    {run.status === 'RUNNING' ? (
                      <Button size='sm' variant='outline' disabled={busy} onClick={() => advance.mutate(run)}>
                        Continue
                      </Button>
                    ) : (
                      <StatusBadge tone={run.status === 'COMPLETED' ? 'success' : 'danger'}>
                        {run.status === 'COMPLETED' ? 'Done' : 'Failed'}
                      </StatusBadge>
                    )}
                  </TableCell>
                  <TableCell className='text-muted-foreground'>{formatDate(run.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {selected && runs.data ? (
        <RunDetail run={runs.data.find((run) => run.id === selected) as EvaluationRun} />
      ) : null}
    </div>
  )
}

function latestSet(runs: EvaluationRun[]): string | null {
  return runs.find((run) => run.kind === 'gold' && run.status === 'COMPLETED')?.set_name ?? null
}

function RunDetail({ run }: { run: EvaluationRun }) {
  const results = useQuery({
    queryKey: useOntologyKey('evaluation', 'results', run.id),
    queryFn: () => ontologyApi.evaluationResults(run.id),
  })
  const cases = useQuery({
    queryKey: useOntologyKey('evaluation', 'cases', run.set_name),
    queryFn: () => ontologyApi.evaluationCases(run.set_name),
    enabled: run.kind !== 'claims',
  })
  const [outcome, setOutcome] = useState('all')
  const byCase = useMemo(
    () => new Map((cases.data ?? []).map((item) => [item.id, item])),
    [cases.data]
  )
  if (run.kind === 'gold') return <GoldCases cases={cases.data ?? []} loading={cases.isLoading} />
  const rows = (results.data ?? []).filter((item) => outcome === 'all' || item.outcome === outcome)
  const outcomes = [...new Set((results.data ?? []).map((item) => item.outcome))].sort()
  return (
    <section className='flex flex-col gap-3'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <h3 className='text-sm font-medium'>
          {KIND_LABEL[run.kind]} · {results.data?.length ?? 0} results
        </h3>
        <Select value={outcome} onValueChange={setOutcome}>
          <SelectTrigger aria-label='Filter by outcome' className='w-44'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>All outcomes</SelectItem>
            {outcomes.map((item) => (
              <SelectItem key={item} value={item}>{item}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {results.isLoading ? (
        <LoadingLines rows={4} />
      ) : (
        <div className='overflow-x-auto rounded-lg border'>
          <Table className='min-w-[48rem]'>
            <TableHeader>
              <TableRow>
                <TableHead>{run.kind === 'claims' ? 'Claim' : 'Question'}</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead>Why</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((item) => {
                const detail = item.detail as Record<string, string | undefined>
                const question = run.kind === 'claims' ? detail.claim : byCase.get(item.item)?.question
                const why =
                  run.kind === 'claims'
                    ? item.reviews.map((review) => `${review.reviewer}: ${review.reason}`).join(' · ')
                    : detail.error || detail.mismatch || detail.grounding || ''
                return (
                  <TableRow key={item.id}>
                    <TableCell className='max-w-md whitespace-normal'>{question ?? item.item}</TableCell>
                    <TableCell className='text-muted-foreground'>{detail.kind ?? detail.stratum}</TableCell>
                    <TableCell>
                      <StatusBadge tone={OUTCOME_TONE[item.outcome] ?? 'neutral'}>{item.outcome}</StatusBadge>
                    </TableCell>
                    <TableCell className='max-w-sm whitespace-normal text-xs text-muted-foreground'>{why}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  )
}

function GoldCases({ cases, loading }: { cases: EvaluationCase[]; loading: boolean }) {
  if (loading) return <LoadingLines rows={4} />
  return (
    <div className='overflow-x-auto rounded-lg border'>
      <Table className='min-w-[48rem]'>
        <TableHeader>
          <TableRow>
            <TableHead>Question</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Meaning</TableHead>
            <TableHead>Reviews</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {cases.map((item) => (
            <TableRow key={item.id}>
              <TableCell className='max-w-sm whitespace-normal'>{item.question}</TableCell>
              <TableCell className='text-muted-foreground'>{item.kind}</TableCell>
              <TableCell className='max-w-sm whitespace-normal text-xs text-muted-foreground'>{item.meaning}</TableCell>
              <TableCell>
                <StatusBadge tone={item.status === 'ACTIVE' ? 'success' : item.status === 'REJECTED' ? 'danger' : 'neutral'}>
                  {item.status === 'ACTIVE' ? 'In the set' : item.status === 'REJECTED' ? 'Rejected' : 'Draft'}
                </StatusBadge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
