import { useState, type CSSProperties, type ReactNode } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { toast } from 'sonner'
import { ArrowUpRight, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { useReport } from './use-report'
import { reportApi } from './api'
import { ReportChart } from './chart'
import { WidgetInteractions } from './interactions'
import { ReportMap } from './map'
import { ReportTable } from './table'
import { SavedViews } from './saved-views'
import { ReportInsights } from './insights'
import { ReportFilter } from './filters'
import { SaveReportTemplate } from './templates'
import { ReportExport } from './export'
import { ReportDistribution } from './distribution'
import type { Interaction, Origin, ReportBlock, ReportSpec, ViewState, Widget, WidgetResult, RenderResult } from './contracts'
import './reports.css'

type ReportController = ReturnType<typeof useReport>

export function SmartReport({ block, origin, handoff, full = false, toolbar, onAsk }: { block: ReportBlock; origin?: Origin; handoff?: string; full?: boolean; toolbar?: (controller: ReportController) => ReactNode; onAsk?: (state: ViewState) => void }) {
  const report = useReport(block, origin, handoff)
  const [expanded, setExpanded] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const navigate = useNavigate()
  const run = (event: Interaction) => { void report.event(event).catch(error => toast.error((error as Error).message)) }
  const open = async () => {
    if (!report.state) return
    try {
      const target = await reportApi.handoff(report.state)
      const url = new URL(target.target_path, window.location.origin)
      await navigate({ to: '/studio/reports/$reportId', params: { reportId: block.report_id }, search: { handoff: url.searchParams.get('handoff')! } })
    } catch (error) { toast.error((error as Error).message) }
  }
  return <section aria-label={block.title} className={cn('smart-report flex min-h-0 min-w-0 flex-col', full ? 'flex-1' : 'rounded-lg border bg-background')}>
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
      <div className="min-w-0"><h2 className="truncate text-base font-semibold">{report.spec?.title ?? block.title}</h2><p className="text-xs text-muted-foreground">{report.state?.draft_id ? 'Private draft' : 'Published'}{report.state ? ` · revision ${report.state.spec_revision}` : ''}</p></div>
      <div className="flex flex-wrap gap-1">
        <Button variant="ghost" size="sm" disabled={!report.state || report.querying} onClick={() => void report.refresh()} aria-label="Refresh report"><RefreshCw className="size-4" /></Button>
        {report.state ? <><SavedViews state={report.state} onApply={report.applySaved} /><ReportInsights state={report.state} /></> : null}
        {onAsk && report.state ? <Button size="sm" variant="outline" onClick={() => onAsk(report.state!)}>Ask Nova</Button> : null}
        {full && report.state?.origin ? <Button size="sm" variant="ghost" onClick={() => void navigate({ to: '/studio', search: { agent: report.state!.origin!.agent_id, thread: report.state!.origin!.thread_id } })}>Return to conversation</Button> : null}
        {!full ? <><Button size="sm" variant="ghost" onClick={() => setExpanded(v => !v)}>{expanded ? 'Collapse' : 'Expand'}</Button><Button variant="outline" size="sm" onClick={() => void open()} disabled={!report.state}>Open full<ArrowUpRight className="size-4" /></Button></> : null}
        {toolbar?.(report)}
        {full && report.state && report.spec ? <Popover open={moreOpen} onOpenChange={setMoreOpen}><PopoverTrigger asChild><Button size="sm" variant="outline">More actions</Button></PopoverTrigger><PopoverContent align="end" onEscapeKeyDown={event => { event.preventDefault(); setMoreOpen(false) }} className="flex max-w-[calc(100vw-2rem)] flex-wrap gap-2"><SaveReportTemplate state={report.state} /><ReportExport key={`export:${report.state.session_id}`} state={report.state} /><ReportDistribution key={`distribution:${report.state.session_id}`} state={report.state} spec={report.spec} /></PopoverContent></Popover> : null}
      </div>
    </header>
    <div className={cn('min-h-0 min-w-0 p-4', full ? 'flex-1 overflow-auto' : '')}>
      {report.error ? <div role="alert" className="space-y-2 text-sm"><p>{report.error.message}</p><Button variant="outline" size="sm" onClick={() => void report.reset()}>Reload report</Button></div> : report.loading || !report.spec || !report.state ? <p role="status" className="text-sm text-muted-foreground">Loading report…</p> : <SmartReportRenderer spec={report.spec} state={report.state} result={report.result} compact={!full && !expanded} busy={report.busy} querying={report.querying} onEvent={run} />}
    </div>
  </section>
}

export function SmartReportRenderer({ spec, state, result, compact = false, busy = false, querying = false, onEvent }: { spec: ReportSpec; state: ViewState; result?: RenderResult; compact?: boolean; busy?: boolean; querying?: boolean; onEvent?: (event: Interaction) => void }) {
  const page = spec.pages.find(p => p.id === state.active_page_id) ?? spec.pages[0]
  const activeFilters = spec.filters.filter(f => f.scope !== 'page' || f.targets.includes(page.id))
  return <div className="space-y-4">
    {spec.description ? <p className="text-sm text-muted-foreground">{spec.description}</p> : null}
    {!onEvent && activeFilters.length ? <dl aria-label="Report filters" className="flex flex-wrap gap-3 text-xs text-muted-foreground">{activeFilters.map(filter => {
      const value = state.filters[filter.id] ?? filter.default
      return <div key={filter.id}><dt className="font-medium">{filter.label}</dt><dd>{value.kind === 'unset' ? 'All values' : value.relative ?? (value.kind === 'range' || value.kind === 'date_range' ? `${value.minimum ?? 'No minimum'} – ${value.maximum ?? 'No maximum'}${value.kind === 'date_range' ? ' (end exclusive)' : ''}` : value.values.map(item => item === null ? '(Blank)' : String(item)).join(', '))}</dd></div>
    })}</dl> : null}
    {onEvent && activeFilters.length ? <details open={!compact} className="rounded-md border p-3"><summary className="cursor-pointer text-sm font-medium">Filters{Object.entries(state.filters).some(([, v]) => v.kind !== 'unset') ? ' · applied' : ''}</summary><div className="mt-3 grid grid-cols-[repeat(auto-fit,minmax(min(100%,14rem),1fr))] gap-4">{activeFilters.map(filter => <ReportFilter key={`${filter.id}:${state.spec_revision}:${JSON.stringify(state.filters[filter.id] ?? filter.default)}`} filter={filter} state={state} disabled={busy} onEvent={onEvent} />)}</div><Button className="mt-3" size="sm" variant="ghost" disabled={busy} onClick={() => onEvent({ type: 'FILTER_RESET_ALL' })}>Reset all filters</Button></details> : null}
    {spec.pages.length > 1 && onEvent ? <nav aria-label="Report pages" className="flex flex-wrap gap-1">{spec.pages.map(p => <Button key={p.id} size="sm" variant={page.id === p.id ? 'secondary' : 'ghost'} aria-current={page.id === p.id ? 'page' : undefined} disabled={busy || !onEvent} onClick={() => onEvent?.({ type: 'PAGE_SET', target: p.id })}>{p.title}</Button>)}</nav> : null}
    <div role="status" aria-live="polite" className="text-xs text-muted-foreground">{querying ? 'Updating data…' : result?.status === 'partial_success' ? 'Some widgets are unavailable. Available results are shown below.' : result ? `Evaluated ${new Date(result.as_of).toLocaleString(spec.locale, { timeZone: spec.timezone })} · ${spec.timezone}` : 'Preparing data…'}</div>
    {page.sections.map(section => <section key={section.id} className="space-y-2" aria-label={section.title || page.title}>
      {section.title ? <h3 className="text-sm font-medium">{section.title}</h3> : null}
      <div className={cn('report-grid', compact && 'report-grid-compact')}>
        {[...section.widgets].sort((a, b) => a.placement.row - b.placement.row || a.placement.col - b.placement.col).map(widget => {
          const value = result?.widgets.find(w => w.widget_id === widget.id)
          const dimensions = state.group_by[widget.id] ?? widget.query?.dimensions ?? []
          const effective = state.group_by[widget.id] && widget.query ? { ...widget, query: { ...widget.query, dimensions }, encoding: { ...widget.encoding, x: dimensions[0] ?? widget.encoding.x, series: null } } : widget
          const interactive = spec.interactions.some(rule => rule.source_widget === widget.id && dimensions.includes(rule.field))
          return <article key={widget.id} className={cn('report-widget min-w-0 rounded-lg border bg-card p-3', state.selected_widget_id === widget.id && 'ring-2 ring-ring')} style={{ '--report-col': widget.placement.col + 1, '--report-row': widget.placement.row + 1, '--report-width': widget.placement.width, '--report-height': widget.placement.height } as CSSProperties}>
            <div className="mb-3 flex flex-wrap items-start justify-between gap-2"><h4 className="text-sm font-medium">{widget.title}</h4>{onEvent ? <button className="rounded-sm text-xs text-muted-foreground underline focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onEvent({ type: 'WIDGET_FOCUS', target: widget.id })}>Select</button> : null}</div>
            {!value ? <p role="status" className="py-6 text-sm text-muted-foreground">Loading data…</p> : <WidgetView widget={effective} result={value} spec={spec} state={state} onEvent={onEvent} interactive={interactive} />}
            {state.cross_filters[widget.id] && onEvent ? <Button variant="ghost" size="sm" onClick={() => onEvent({ type: 'CROSS_FILTER_CLEAR', target: widget.id })}>Clear selection</Button> : null}
            {widget.drill_dimensions.length && onEvent ? <div className="mt-2 flex flex-wrap gap-2"><select aria-label={`Group ${widget.title} by`} className="min-h-9 min-w-0 rounded-md border bg-background px-2 text-xs" value={state.group_by[widget.id]?.[0] ?? ''} onChange={e => e.target.value && onEvent({ type: 'DRILL_BY', target: widget.id, field: e.target.value })}><option value="">Change grouping</option>{widget.drill_dimensions.map(field => <option key={field} value={field}>{field}</option>)}</select><Button variant="ghost" size="sm" onClick={() => onEvent({ type: 'DRILL_UP', target: widget.id })}>Drill up</Button></div> : null}
          </article>
        })}
      </div>
    </section>)}
  </div>
}

function WidgetView({ widget, result, spec, state, onEvent, interactive }: { widget: Widget; result: WidgetResult; spec: ReportSpec; state: ViewState; onEvent?: (event: Interaction) => void; interactive: boolean }) {
  if (!['success', 'empty'].includes(result.status)) return <div role="status" className="space-y-1 py-6 text-sm"><p className="font-medium">{result.status === 'requires_mapping' ? 'Requires mapping' : result.status === 'denied' ? 'Access unavailable' : result.status === 'timeout' ? 'Query timed out' : result.status === 'incompatible' ? 'Incompatible configuration' : 'Could not load widget'}</p><p className="text-muted-foreground">{result.message}</p></div>
  if (result.status === 'empty') return <p className="py-6 text-sm text-muted-foreground">No data matches these filters.</p>
  const selectionField = spec.interactions.find(rule => rule.source_widget === widget.id)?.field
  const selectionIndex = result.columns.findIndex(field => field === selectionField || field === selectionField?.replace(/\./g, '__'))
  const choices = [...new Map(result.rows.map(row => [JSON.stringify(row[selectionIndex]), row[selectionIndex]])).values()]
  const metric = widget.encoding.value ?? widget.query?.metrics[0] ?? ''
  const metricIndex = result.columns.findIndex(c => c === metric || c === metric.replace(/\./g, '__'))
  const comparisonIndex = result.columns.indexOf('comparison_period')
  const currentRow = comparisonIndex < 0 ? result.rows[0] : result.rows.find(row => row[comparisonIndex] === 'current_period')
  const previous = comparisonIndex < 0 ? undefined : result.rows.find(row => row[comparisonIndex] === 'previous_period')?.[metricIndex]
  const value = currentRow?.[metricIndex]
  return <>
    {widget.kind === 'kpi' ? <p className="text-3xl font-semibold tabular-nums" title={String(value ?? '')}>{value == null ? '—' : formatMetric(value, widget, spec.locale)}</p> : ['table', 'pivot'].includes(widget.kind) ? <ReportTable widget={widget} result={result} state={state} onEvent={onEvent} /> : <><ReportVisual widget={widget} result={result} onSelect={interactive && onEvent ? value => onEvent({ type: 'CROSS_FILTER_APPLY', target: widget.id, values: [value] }) : undefined} /><details className="text-xs"><summary className="cursor-pointer text-muted-foreground">View data and select values</summary><ReportTable widget={widget} result={result} />{interactive && onEvent && selectionIndex >= 0 ? <select className="mt-2 min-h-9 max-w-full rounded-md border bg-background px-2" aria-label={`Select a value from ${widget.title}`} value="" onChange={e => e.target.value && onEvent({ type: 'CROSS_FILTER_APPLY', target: widget.id, values: [JSON.parse(e.target.value)] })}><option value="">Select a value</option>{choices.map(value => <option key={JSON.stringify(value)} value={JSON.stringify(value)}>{String(value ?? '(Blank)')}</option>)}</select> : null}</details></>}
    {onEvent ? <WidgetInteractions key={`${widget.id}:${state.state_revision}`} widget={widget} result={result} state={state} field={interactive ? selectionField : undefined} onEvent={onEvent} /> : null}
    {result.completeness === 'truncated' ? <p className="mt-2 text-xs text-muted-foreground">Display limit reached. Refine filters to see more detail.</p> : null}
    {widget.kind === 'kpi' && comparisonIndex >= 0 ? <p className="mt-2 text-xs text-muted-foreground">Previous period: {previous == null ? 'No data' : formatMetric(previous, widget, spec.locale)}</p> : null}
    {result.warnings?.map(warning => <p key={warning} className="mt-2 text-xs text-muted-foreground">{warning}</p>)}
    {result.not_applied.length ? <p className="mt-2 text-xs text-muted-foreground">Not applied: {result.not_applied.map(id => spec.filters.find(f => f.id === id)?.label ?? id).join(', ')}</p> : null}
    <details className="mt-3 text-xs text-muted-foreground"><summary className="cursor-pointer">Data evidence</summary><dl className="mt-1 space-y-1"><dt>Semantic version</dt><dd>{result.semantic_version}</dd><dt>Query</dt><dd className="break-all">{result.query_id ?? result.query_fingerprint}</dd><dt>Exact value</dt><dd>{widget.kind === 'kpi' ? String(value ?? '—') : `${result.rows.length} displayed rows`}</dd></dl></details>
  </>
}

const ReportVisual = (props: Parameters<typeof ReportChart>[0]) => props.widget.kind === 'map' ? <ReportMap {...props} /> : <ReportChart {...props} />

function formatMetric(value: string | number | boolean, widget: Widget, locale: string) {
  const format = widget.format
  const options: Intl.NumberFormatOptions = { maximumFractionDigits: format.precision, minimumFractionDigits: format.precision,
    ...(format.kind === 'currency' ? { style: 'currency', currency: format.currency } : format.kind === 'percent' ? { style: 'percent' } : {}),
  }
  // Intl formats decimal strings without changing the value through a JS Number.
  const formatted = new Intl.NumberFormat(locale, options).format(value as number)
  return format.kind === 'percentage_points' ? `${formatted} pp` : formatted
}
