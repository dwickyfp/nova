import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { reportsPath } from './api'
import type { Scalar, ViewState } from './contracts'

type Insight = { widget_id: string; metric: string; query_fingerprint: string; kind: 'period_change' | 'largest_value' | 'contribution' | 'unusual_value'; current?: string; previous?: string; change?: string; relative_change?: string | null; dimension?: string; categories?: Scalar[]; value?: string; group_count?: number; total?: string; share?: string; median?: string; median_absolute_deviation?: string; score?: string; method?: string }

export function ReportInsights({ state }: { state: ViewState }) {
  const [open, setOpen] = useState(false)
  const epoch = useAuthStore(s => s.securityEpoch)
  const insights = useQuery({ queryKey: ['report-insights', epoch, state.session_id, state.state_revision], enabled: open, retry: false, gcTime: 0,
    queryFn: ({ signal }) => api.post<{ insights: Insight[]; excluded_widgets: string[]; as_of: string }>(`${reportsPath}/${state.report_id}/insights`, { report_id: state.report_id, view_session_id: state.session_id, state_revision: state.state_revision, selected_widget_id: state.selected_widget_id }, signal),
  })
  return <><Button size="sm" variant="ghost" onClick={() => setOpen(true)}>Insights</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-h-[85dvh] overflow-auto"><DialogHeader><DialogTitle>Report insights</DialogTitle><DialogDescription>Observations use your current filters and access. Select a widget to focus this analysis.</DialogDescription></DialogHeader>{insights.isPending ? <p role="status">Evaluating current data…</p> : insights.isError ? <p role="alert">{insights.error.message}</p> : <>
    <ul className="space-y-4">{insights.data.insights.map((insight, index) => <li key={index} className="space-y-1 text-sm"><h3 className="font-medium">{insight.metric}</h3>{insight.kind === 'period_change' ? <p>Current period: {insight.current}. Previous period: {insight.previous}. Change: {insight.change}.{insight.relative_change === null ? ' Relative change is undefined because the previous value is zero.' : null}</p> : insight.kind === 'contribution' ? <p>Each of {insight.categories?.map(String).join(', ')} contributes {insight.value} of the independently queried total {insight.total}.</p> : insight.kind === 'unusual_value' ? <p>{insight.categories?.map(String).join(', ')}: {insight.value} is unusual relative to the median {insight.median} across {insight.group_count} periods. This statistical signal does not establish a cause or forecast.</p> : <p>{insight.categories?.map(value => String(value ?? '(Blank)')).join(', ')} {insight.categories?.length === 1 ? 'has' : 'have'} the highest {insight.metric}: {insight.value}, across {insight.group_count} groups.</p>}<details className="text-xs text-muted-foreground"><summary>Data evidence</summary><p>{insight.method}</p><p className="break-all">Widget: {insight.widget_id} · Query: {insight.query_fingerprint}</p></details></li>)}</ul>
    {!insights.data.insights.length ? <p className="text-sm text-muted-foreground">No supported observation is available for this view.</p> : null}{insights.data.excluded_widgets.length ? <p className="text-xs text-muted-foreground">Paged, limited, empty, or unavailable widgets were excluded. Insights require complete results.</p> : null}<p className="text-xs text-muted-foreground">Evaluated {new Date(insights.data.as_of).toLocaleString()}</p>
  </>}</DialogContent></Dialog></>
}
