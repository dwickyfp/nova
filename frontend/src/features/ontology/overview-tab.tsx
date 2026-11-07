import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Gauge } from 'lucide-react'
import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { ontologyApi, type CalibrationBin, type JoinSummary, type Measured, type OntologyMetrics } from './api'
import { formatDate, useOntologyKey } from './model'

type Open = (tab: 'evaluation' | 'checks' | 'proposals' | 'concepts') => void

function pct(value: number | null | undefined, digits = 0) {
  return value == null ? '–' : `${(value * 100).toFixed(digits)}%`
}

function interval(measured: Measured | null | undefined) {
  if (!measured?.interval_95) return null
  const [low, high] = measured.interval_95
  return `${pct(low)}–${pct(high)}`
}

const STRATUM_LABELS: Record<string, string> = {
  entity: 'Entities',
  dimension: 'Dimensions',
  metric: 'Metrics',
  measures: 'Metric → entity',
  related: 'Entity links',
  related_role: 'Entity roles',
  derives: 'Built from',
  equivalent: 'Same formula',
  alias: 'Official names',
}

const OUTCOMES: { key: string; label: string; color: string }[] = [
  { key: 'correct', label: 'Correct', color: 'var(--success)' },
  { key: 'clarified', label: 'Asked to clarify', color: 'var(--info)' },
  { key: 'abstained', label: 'Declined', color: 'var(--chart-tone-3)' },
  { key: 'wrong', label: 'Wrong answer', color: 'var(--destructive)' },
  { key: 'answered', label: 'Answered the unanswerable', color: 'var(--chart-5)' },
  { key: 'failed', label: 'Failed', color: 'var(--warning)' },
]

/** The ontology as numbers: every figure is measured, with its n and interval. */
export function OverviewTab({ onOpen }: { onOpen?: Open }) {
  const metrics = useQuery({ queryKey: useOntologyKey('metrics'), queryFn: ontologyApi.metrics })

  if (metrics.isLoading) {
    return (
      <div className='grid gap-4 md:grid-cols-2 xl:grid-cols-4'>
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton key={index} className='h-36 rounded-xl' />
        ))}
      </div>
    )
  }
  if (metrics.isError || !metrics.data) {
    return (
      <EmptyState
        variant='error'
        icon={Gauge}
        title='The measurements could not be loaded'
        description={metrics.error?.message ?? 'Try again.'}
        action={<Button variant='outline' onClick={() => metrics.refetch()}>Try again</Button>}
      />
    )
  }
  return <Overview data={metrics.data} onOpen={onOpen} />
}

function Overview({ data, onOpen }: { data: OntologyMetrics; onOpen?: Open }) {
  const { answers, baseline, effect, claims, gold, joins } = data.measured
  const live = data.live
  return (
    <div className='flex flex-col gap-4'>
      <div className='grid gap-4 md:grid-cols-2 xl:grid-cols-4'>
        <Kpi
          title='Answer accuracy'
          value={pct(answers?.answer_accuracy.value)}
          detail={
            answers
              ? `${interval(answers.answer_accuracy)} · n=${answers.answer_accuracy.n} questions`
              : null
          }
          delta={
            effect?.difference != null
              ? {
                  text: `${effect.difference >= 0 ? '+' : ''}${(effect.difference * 100).toFixed(0)} pts`,
                  good: effect.difference >= 0,
                  hint: `vs ${pct(effect.without.value)} without the ontology, McNemar p=${effect.mcnemar.p_value.toPrecision(2)}`,
                }
              : undefined
          }
          missing='Run the gold set with the ontology to measure it.'
          footer={answers ? `${answers.set} · ${answers.release_id ?? 'no release'} · ${formatDate(answers.at)}` : undefined}
          action={onOpen ? () => onOpen('evaluation') : undefined}
          actionLabel='View questions'
        />
        <Kpi
          title='Reliability'
          value={answers?.reliability.value == null ? '–' : answers.reliability.value.toFixed(2)}
          detail={
            answers
              ? `${pct(answers.wrong_answers.value, 1)} confidently wrong · ${pct(answers.abstention.value)} correct refusals`
              : null
          }
          hint='Rewards correct answers and correct refusals, penalises wrong ones (−1 to 1).'
          missing='Measured together with answer accuracy.'
        />
        <Kpi
          title='Claim accuracy'
          value={pct(claims?.claim_accuracy.value)}
          detail={
            claims
              ? `${claims.claim_accuracy.interval_95 ? `${pct(claims.claim_accuracy.interval_95[0])}–${pct(claims.claim_accuracy.interval_95[1])}` : ''} · n=${claims.claim_accuracy.n} claims`
              : null
          }
          hint='Stratified sample of what the ontology states, judged against the published views.'
          missing='Run a claim audit to measure it.'
          footer={claims ? `judged ${formatDate(claims.at)}` : undefined}
        />
        <Kpi
          title='Calibration error'
          value={claims?.calibration.ece == null ? '–' : claims.calibration.ece.toFixed(3)}
          detail={
            claims?.calibration.ece != null
              ? claims.calibration.bins.length < 2
                ? `n=${claims.calibration.n} · every claim has the same confidence band, so this cannot tell good from bad calibration yet`
                : `Brier ${claims.calibration.brier?.toFixed(3)} · n=${claims.calibration.n}`
              : null
          }
          hint='Expected calibration error of the stored confidence: 0 means "90%" is right 90% of the time.'
          missing='Measured by the claim audit.'
          tone={claims?.calibration.ece != null && claims.calibration.ece > 0.05 ? 'warning' : undefined}
        />
      </div>

      <div className='grid gap-4 xl:grid-cols-3'>
        <Panel title='With and without the ontology' subtitle={effect ? `Same ${effect.n} answerable questions` : undefined}>
          {effect ? (
            <div className='flex flex-col gap-4 py-2'>
              <IntervalBar label='Without ontology' measured={effect.without} color='var(--viz-mark)' />
              <IntervalBar label='With ontology' measured={effect.with} color='var(--chart-4)' />
              <p className='text-xs text-muted-foreground'>
                {effect.mcnemar.gained} {effect.mcnemar.gained === 1 ? 'question' : 'questions'} fixed,{' '}
                {effect.mcnemar.lost} broken by the ontology
              </p>
            </div>
          ) : (
            <NotMeasured text='Needs runs with and without the ontology on the same gold set.' />
          )}
        </Panel>
        <Panel title='Where answers end up' subtitle={answers ? `${Object.values(answers.outcomes).reduce((a, b) => a + b, 0)} questions` : undefined}>
          {answers ? <OutcomeBar outcomes={answers.outcomes} /> : <NotMeasured text='Needs a completed answer run.' />}
        </Panel>
        <Panel title='Is the confidence honest?' subtitle='Stated confidence against judged accuracy'>
          {claims && claims.calibration.bins.length ? (
            <Reliability bins={claims.calibration.bins} />
          ) : (
            <NotMeasured text='Needs a claim audit.' />
          )}
        </Panel>
      </div>

      <JoinPanels joins={joins ?? null} onOpen={onOpen} />

      <div className='grid gap-4 xl:grid-cols-3'>
        <Panel title='Claim accuracy by kind' subtitle='Lowest first, with 95% intervals'>
          {claims ? (
            <div className='flex flex-col gap-3 py-1'>
              {Object.entries(claims.by_stratum)
                .sort(([, a], [, b]) => (a.value ?? 0) - (b.value ?? 0))
                .map(([name, measured]) => (
                  <IntervalBar key={name} label={STRATUM_LABELS[name] ?? name} measured={measured} color='var(--chart-4)' compact />
                ))}
            </div>
          ) : (
            <NotMeasured text='Needs a claim audit.' />
          )}
        </Panel>
        <Panel title='Coverage by domain' subtitle={`${pct(live.metric_coverage.value)} of derived metrics are official`}>
          <div className='flex flex-col gap-3 py-1'>
            {live.domains.map((domain) => (
              <IntervalBar
                key={domain.domain}
                label={domain.domain}
                measured={{ value: domain.metrics ? domain.bound / domain.metrics : null, successes: domain.bound, n: domain.metrics, interval_95: null }}
                color='var(--chart-2)'
                compact
                suffix={`${domain.bound}/${domain.metrics} metrics bound`}
              />
            ))}
            {live.question_grounding ? (
              <p className='text-xs text-muted-foreground'>
                {pct(live.question_grounding.value)} of gold questions resolved to an official concept
              </p>
            ) : null}
          </div>
        </Panel>
        <Panel title='Health'>
          <dl className='grid grid-cols-2 gap-x-4 gap-y-3 py-1 text-sm'>
            <Fact label='Stale bindings' value={live.stale_bindings} warn={live.stale_bindings > 0} />
            <Fact
              label='Meaning checks'
              value={`${live.checks.error ?? 0} errors · ${live.checks.warning ?? 0} warnings`}
              warn={(live.checks.error ?? 0) > 0}
              onClick={onOpen ? () => onOpen('checks') : undefined}
            />
            <Fact
              label='Nova proposals accepted'
              value={
                live.engine_precision.n
                  ? `${pct(live.engine_precision.value)} of ${live.engine_precision.n}`
                  : '–'
              }
              onClick={onOpen ? () => onOpen('proposals') : undefined}
            />
            <Fact
              label='Gold set'
              value={
                gold
                  ? `${gold.set_name} v${gold.version} · ${Object.values(gold.active).reduce((a, b) => a + b, 0)} cases`
                  : 'none'
              }
            />
            <Fact
              label='Judge agreement'
              value={judgeAgreement(gold?.agreement, claims?.agreement)}
            />
            <Fact
              label='Data drift'
              value={
                data.measured.joins?.drift
                  ? `${data.measured.joins.drift.findings.length} change${data.measured.joins.drift.findings.length === 1 ? '' : 's'} since the last profile`
                  : 'one profile so far'
              }
              warn={(data.measured.joins?.drift?.findings.length ?? 0) > 0}
              onClick={onOpen ? () => onOpen('evaluation') : undefined}
            />
            <Fact
              label='Without-ontology baseline'
              value={baseline ? formatDate(baseline.at) : 'not run'}
            />
          </dl>
        </Panel>
      </div>
      {data.not_measured.length ? (
        <p className='text-xs text-muted-foreground'>Not measured yet: {data.not_measured.join('; ')}.</p>
      ) : null}
    </div>
  )
}

/** What Nova learned from the data itself: joins, their probabilities, and what it decided. */
function JoinPanels({ joins, onOpen }: { joins: JoinSummary | null; onOpen?: Open }) {
  const score = joins?.joins_reviewed ?? joins?.joins
  const reviewed = Boolean(joins?.joins_reviewed)
  return (
    <div className='grid gap-4 xl:grid-cols-3'>
      <Panel
        title='Joins found in the data'
        subtitle={
          joins
            ? `${joins.profiled_columns} columns profiled in ${Math.round(joins.profile_seconds + joins.discover_seconds)} s`
            : undefined
        }
      >
        {joins && score ? (
          <div className='flex flex-col gap-4 py-2'>
            <IntervalBar label='Precision' measured={score.precision} color='var(--chart-4)' />
            <IntervalBar label='Recall' measured={score.recall} color='var(--chart-4)' />
            <p className='text-xs text-muted-foreground'>
              F1 {score.f1.toFixed(2)} against {reviewed ? 'declared and reviewer-confirmed joins' : 'the joins the views declare'}
              {reviewed ? ` (${joins.undeclared_reviewed} undeclared joins reviewed)` : ''}. Recall counts only joins someone has declared or confirmed.
            </p>
            {onOpen ? (
              <Button variant='link' size='sm' className='self-start px-0' onClick={() => onOpen('evaluation')}>
                See every join <ArrowRight className='size-3.5' />
              </Button>
            ) : null}
          </div>
        ) : (
          <NotMeasured text='Run "Find joins" in Evaluation to measure it.' />
        )}
      </Panel>
      <Panel
        title='Decided by Nova'
        subtitle={joins?.decisions ? `${joins.decisions.labelled} labelled column pairs, judged on unseen databases` : undefined}
      >
        {joins?.decisions ? (
          <div className='flex flex-col gap-4 py-2'>
            <IntervalBar label='Decided without a person' measured={joins.decisions.automatic} color='var(--chart-2)' />
            <IntervalBar label='Accepted and right' measured={joins.decisions.accepted_precision} color='var(--chart-4)' />
            <p className='text-xs text-muted-foreground'>
              {joins.decisions.rejected_misses.successes} of {joins.decisions.rejected_misses.n} rejected pairs were real joins ·{' '}
              {joins.decisions.review_queue} left for review
            </p>
          </div>
        ) : (
          <NotMeasured text='Needs at least ten labelled joins in two databases.' />
        )}
      </Panel>
      <Panel
        title='Is the join probability honest?'
        subtitle={
          joins?.calibration
            ? `ECE ${joins.calibration.ece.toFixed(3)} · AUC ${joins.calibration.auc?.toFixed(3) ?? '–'} · n=${joins.calibration.n}`
            : undefined
        }
      >
        {joins?.calibration?.bins.length ? (
          <Reliability bins={joins.calibration.bins} />
        ) : (
          <NotMeasured text='Measured with the join discovery.' />
        )}
      </Panel>
    </div>
  )
}

function judgeAgreement(...groups: (Record<string, { kappa: number | null; n: number }> | undefined)[]) {
  const entries = groups.flatMap((group) => Object.entries(group ?? {}))
  if (!entries.length) return 'no second judge yet'
  return entries
    .map(([reviewer, k]) => `κ ${k.kappa == null ? '–' : k.kappa.toFixed(2)} (${reviewer}, n=${k.n})`)
    .join(' · ')
}

function Kpi({
  title,
  value,
  detail,
  delta,
  hint,
  missing,
  footer,
  action,
  actionLabel,
  tone,
}: {
  title: string
  value: string
  detail: string | null
  delta?: { text: string; good: boolean; hint: string }
  hint?: string
  missing: string
  footer?: string
  action?: () => void
  actionLabel?: string
  tone?: 'warning'
}) {
  const measured = detail !== null
  return (
    <section className='flex min-h-[148px] min-w-0 flex-col rounded-xl border bg-card p-4'>
      <div className='flex items-start justify-between gap-2'>
        <h3 className='text-sm font-medium'>{title}</h3>
        {hint ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button type='button' aria-label={`About ${title}`} className='text-xs text-muted-foreground underline decoration-dotted underline-offset-2'>
                how
              </button>
            </TooltipTrigger>
            <TooltipContent className='max-w-64'>{hint}</TooltipContent>
          </Tooltip>
        ) : null}
      </div>
      {measured ? (
        <>
          <div className='mt-2 flex flex-wrap items-baseline gap-2'>
            <span className={cn('text-3xl tabular-nums', tone === 'warning' && 'text-warning-strong')}>{value}</span>
            {delta ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    tabIndex={0}
                    className={cn(
                      'rounded-md px-1.5 py-0.5 text-xs font-medium tabular-nums',
                      delta.good ? 'bg-success/10 text-success-strong' : 'bg-destructive/10 text-destructive'
                    )}
                  >
                    {delta.text}
                  </span>
                </TooltipTrigger>
                <TooltipContent>{delta.hint}</TooltipContent>
              </Tooltip>
            ) : null}
          </div>
          <p className='mt-1 text-xs text-muted-foreground tabular-nums'>{detail}</p>
        </>
      ) : (
        <p className='mt-2 text-sm text-muted-foreground'>
          <span className='block text-2xl text-foreground/40'>Not measured</span>
          {missing}
        </p>
      )}
      <div className='mt-auto flex items-center justify-between gap-2 pt-3 text-xs text-muted-foreground'>
        <span className='min-w-0 truncate' title={footer}>{footer}</span>
        {action && measured ? (
          <button type='button' onClick={action} className='inline-flex shrink-0 items-center gap-1 font-medium text-foreground hover:underline'>
            {actionLabel} <ArrowRight className='size-3' />
          </button>
        ) : null}
      </div>
    </section>
  )
}

function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className='flex min-w-0 flex-col rounded-xl border bg-card p-4'>
      <h3 className='text-sm font-medium'>{title}</h3>
      {subtitle ? <p className='text-xs text-muted-foreground'>{subtitle}</p> : null}
      <div className='mt-3 flex-1'>{children}</div>
    </section>
  )
}

function NotMeasured({ text }: { text: string }) {
  return <p className='py-6 text-center text-sm text-muted-foreground'>{text}</p>
}

/** A proportion as a bar from 0 to 100%, with its 95% interval as a whisker. */
function IntervalBar({
  label,
  measured,
  color,
  compact,
  suffix,
}: {
  label: string
  measured: Measured
  color: string
  compact?: boolean
  suffix?: string
}) {
  const value = measured.value ?? 0
  const [low, high] = measured.interval_95 ?? [value, value]
  const text = suffix ?? `${pct(measured.value)} · ${interval(measured) ?? ''} · n=${measured.n}`
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div tabIndex={0} className='flex flex-col gap-1 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring'>
          <div className='flex items-baseline justify-between gap-2 text-sm'>
            <span>{label}</span>
            <span className='text-xs tabular-nums text-muted-foreground'>
              {compact ? suffix ?? pct(measured.value) : text}
            </span>
          </div>
          <div className={cn('relative w-full rounded-sm bg-muted', compact ? 'h-2' : 'h-3')}>
            <div className='absolute inset-y-0 left-0 rounded-sm' style={{ width: `${value * 100}%`, background: color }} />
            {measured.interval_95 ? (
              <div
                className='absolute top-1/2 h-px -translate-y-1/2 bg-foreground/70'
                style={{ left: `${low * 100}%`, width: `${Math.max(0.5, (high - low) * 100)}%` }}
              >
                <span className='absolute -top-1 left-0 h-2 w-px bg-foreground/70' />
                <span className='absolute -top-1 right-0 h-2 w-px bg-foreground/70' />
              </div>
            ) : null}
          </div>
        </div>
      </TooltipTrigger>
      <TooltipContent>{`${label}: ${text}`}</TooltipContent>
    </Tooltip>
  )
}

function OutcomeBar({ outcomes }: { outcomes: Record<string, number> }) {
  const totals = new Map<string, number>()
  for (const [key, count] of Object.entries(outcomes)) {
    const outcome = key.split(':')[1] ?? key
    totals.set(outcome, (totals.get(outcome) ?? 0) + count)
  }
  const all = [...totals.values()].reduce((a, b) => a + b, 0) || 1
  const shown = OUTCOMES.filter((item) => totals.get(item.key))
  return (
    <div className='flex flex-col gap-4 py-2'>
      <div className='flex h-3 w-full gap-0.5 overflow-hidden rounded-sm'>
        {shown.map((item) => (
          <Tooltip key={item.key}>
            <TooltipTrigger asChild>
              <div
                tabIndex={0}
                aria-label={`${item.label}: ${totals.get(item.key)}`}
                className='h-full first:rounded-l-sm last:rounded-r-sm'
                style={{ width: `${((totals.get(item.key) ?? 0) / all) * 100}%`, background: item.color }}
              />
            </TooltipTrigger>
            <TooltipContent>{`${item.label}: ${totals.get(item.key)} (${pct((totals.get(item.key) ?? 0) / all)})`}</TooltipContent>
          </Tooltip>
        ))}
      </div>
      <ul className='grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm'>
        {shown.map((item) => (
          <li key={item.key} className='flex items-center justify-between gap-2'>
            <span className='flex items-center gap-2'>
              <span aria-hidden='true' className='size-2 rounded-full' style={{ background: item.color }} />
              {item.label}
            </span>
            <span className='tabular-nums text-muted-foreground'>{totals.get(item.key)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Reliability({ bins }: { bins: CalibrationBin[] }) {
  const points = bins.map((bin) => ({ x: bin.confidence, y: bin.accuracy, n: bin.n }))
  return (
    <div className='h-48 w-full'>
      <ResponsiveContainer width='100%' height='100%'>
        <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: -16 }}>
          <CartesianGrid stroke='var(--viz-grid)' strokeDasharray='3 3' />
          <XAxis type='number' dataKey='x' domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} axisLine={false} tickLine={false} name='Stated confidence' />
          <YAxis type='number' dataKey='y' domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} axisLine={false} tickLine={false} name='Judged accuracy' />
          <ZAxis type='number' dataKey='n' range={[60, 260]} name='Claims' />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke='var(--viz-reference)' strokeDasharray='4 4' />
          <ChartTooltip
            cursor={false}
            contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 12, fontSize: 12 }}
            formatter={(value, name) => [
              name === 'Claims' ? String(value) : `${Math.round(Number(value) * 100)}%`,
              String(name),
            ]}
          />
          <Scatter data={points} fill='var(--chart-4)' stroke='var(--card)' strokeWidth={2} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

function Fact({
  label,
  value,
  warn,
  onClick,
}: {
  label: string
  value: React.ReactNode
  warn?: boolean
  onClick?: () => void
}) {
  return (
    <div className='min-w-0'>
      <dt className='text-xs text-muted-foreground'>{label}</dt>
      <dd
        className={cn('truncate tabular-nums', warn && 'text-warning-strong')}
        title={typeof value === 'string' ? value : undefined}
      >
        {onClick ? (
          <button type='button' onClick={onClick} className='truncate text-left hover:underline'>
            {value}
          </button>
        ) : (
          value
        )}
      </dd>
    </div>
  )
}
