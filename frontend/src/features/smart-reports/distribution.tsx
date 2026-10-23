import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { operationId, reportsPath } from './api'
import type { ReportSpec, ViewState, RenderResult } from './contracts'
import { SmartReportRenderer } from './renderer'

type Subscription = { id: string; revision: number; configuration: { title: string; enabled: boolean; [key: string]: unknown } }
type Activity = { id: string; title: string; status: string; evaluated_at: string; thread_id: string | null }
export function ReportDistribution({ state, spec }: { state: ViewState; spec: ReportSpec }) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false)
  const [threshold, setThreshold] = useState('')
  const [selectedActivity, setSelectedActivity] = useState<string | null>(null)
  const snapshot = useQuery({ queryKey: ['report-activity-snapshot', epoch, state.report_id, selectedActivity], enabled: open && !!selectedActivity, queryFn: ({ signal }) => api.get<{ spec: ReportSpec; pages: { state: ViewState; result: RenderResult }[] }>(`${reportsPath}/${state.report_id}/activity/${selectedActivity}/snapshot`, signal) })
  const subscriptions = useQuery({ queryKey: ['report-subscriptions', epoch, state.report_id], enabled: open, queryFn: () => api.get<{ items: Subscription[] }>(`${reportsPath}/${state.report_id}/subscriptions`) })
  const activity = useQuery({ queryKey: ['report-activity', epoch, state.report_id], enabled: open, queryFn: () => api.get<{ items: Activity[] }>(`${reportsPath}/${state.report_id}/activity`) })
  const kpis = spec.pages.flatMap(p => p.sections.flatMap(s => s.widgets)).filter(w => w.kind === 'kpi' && w.query?.time && !w.query.time.compare)
  const write = async (value: Record<string, unknown>) => {
    setBusy(true)
    try { await api.post(`${reportsPath}/${state.report_id}/subscriptions`, { ...value, operation_id: operationId(), view_session_id: state.session_id, expected_state_revision: state.state_revision }); await subscriptions.refetch(); toast.success('Report schedule saved') }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <><Button size="sm" variant="outline" onClick={() => setOpen(true)}>Activity & schedules</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-h-[85dvh] overflow-auto"><DialogHeader><DialogTitle>Report activity and schedules</DialogTitle><DialogDescription>Results stay in Nova. Scheduled queries use your explicitly authorized user and active role.</DialogDescription></DialogHeader>
    {!state.draft_id ? <form className="space-y-3" onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); const selected = kpis.find(w => w.id === threshold); void write({ title: data.get('title'), schedule_kind: 'cron', schedule_expr: data.get('cron'), timezone: data.get('timezone'), enabled: true, consent_execution: data.get('consent') === 'on', threshold: selected ? { widget_id: selected.id, metric: selected.query!.metrics[0], comparator: data.get('comparator'), value: data.get('value'), window: data.get('window'), cooldown_minutes: Number(data.get('cooldown')) } : null }) }}>
      <label className="block space-y-1 text-sm">Schedule name<Input name="title" defaultValue={spec.title} required maxLength={256} /></label><label className="block space-y-1 text-sm">Schedule (cron)<Input name="cron" defaultValue="0 9 * * 1-5" required /></label><p className="text-xs text-muted-foreground">Default: weekdays at 09:00. Minimum interval: 15 minutes.</p><label className="block space-y-1 text-sm">Timezone<Input name="timezone" defaultValue={spec.timezone} required /></label>
      <label className="block space-y-1 text-sm">Delivery condition<select className="min-h-9 w-full rounded-md border bg-background px-2" value={threshold} onChange={e => setThreshold(e.target.value)}><option value="">Every scheduled run</option>{kpis.map(w => <option value={w.id} key={w.id}>Threshold: {w.title}</option>)}</select></label>
      {threshold ? <div className="grid grid-cols-2 gap-2"><label className="text-sm">Comparison<select name="comparator" className="min-h-9 w-full rounded-md border bg-background px-2">{['>', '>=', '<', '<=', '=', '!='].map(op => <option key={op}>{op}</option>)}</select></label><label className="text-sm">Threshold<Input name="value" inputMode="decimal" required /></label><label className="text-sm">Window<select name="window" className="min-h-9 w-full rounded-md border bg-background px-2">{['last_7_days', 'last_30_days', 'this_month', 'this_year'].map(window => <option key={window} value={window}>{window.replace(/_/g, ' ')}</option>)}</select></label><label className="text-sm">Cooldown (minutes)<Input name="cooldown" type="number" min={15} max={525600} defaultValue={60} required /></label></div> : null}
      <label className="flex items-start gap-2 text-sm"><input name="consent" type="checkbox" required className="mt-1" />I authorize scheduled execution as my current user and active role.</label><Button type="submit" disabled={busy}>Create schedule</Button>
    </form> : <p className="text-sm text-muted-foreground">Publish the report before creating a schedule.</p>}
    <section className="space-y-2 border-t pt-3"><h3 className="text-sm font-medium">Your schedules</h3>{subscriptions.isError ? <p role="alert">{subscriptions.error.message}</p> : subscriptions.isPending ? <p role="status">Loading schedules…</p> : !subscriptions.data.items.length ? <p className="text-sm text-muted-foreground">No schedules yet.</p> : subscriptions.data.items.map(item => <div key={item.id} className="flex items-center justify-between gap-2 text-sm"><span>{item.configuration.title} · {item.configuration.enabled ? 'Active' : 'Paused'}</span><Button variant="ghost" size="sm" disabled={busy} onClick={() => void write({ ...item.configuration, subscription_id: item.id, expected_revision: item.revision, enabled: !item.configuration.enabled, consent_execution: true })}>{item.configuration.enabled ? 'Pause' : 'Resume'}</Button></div>)}</section>
    <section className="space-y-2 border-t pt-3"><h3 className="text-sm font-medium">Recent activity</h3>{activity.isError ? <p role="alert">{activity.error.message}</p> : activity.isPending ? <p role="status">Loading activity…</p> : !activity.data.items.length ? <p className="text-sm text-muted-foreground">Completed runs appear here and in your Studio history.</p> : activity.data.items.map(item => <div key={item.id} className="space-y-1 text-sm"><p>{item.title} · {item.status.replace(/_/g, ' ')}</p><p className="text-xs text-muted-foreground">{new Date(item.evaluated_at).toLocaleString(spec.locale, { timeZone: spec.timezone })}</p>{item.status === 'delivered' ? <Button variant="outline" size="sm" onClick={() => setSelectedActivity(item.id)}>View saved result</Button> : null}{item.thread_id ? <Link to="/studio" search={{ agent: '__smart__', thread: item.thread_id }} className="underline">Open result</Link> : null}</div>)}</section>
    {selectedActivity ? <section aria-label="Saved report result" className="space-y-4 border-t pt-3"><Button size="sm" variant="ghost" onClick={() => setSelectedActivity(null)}>Close saved result</Button>{snapshot.isError ? <p role="alert">{snapshot.error.message}</p> : snapshot.isPending ? <p role="status">Checking access to saved result…</p> : snapshot.data.pages.map(page => <section className="smart-report" key={page.state.active_page_id}><h3 className="mb-3 text-sm font-medium">{snapshot.data.spec.pages.find(p => p.id === page.state.active_page_id)?.title}</h3><SmartReportRenderer spec={snapshot.data.spec} state={page.state} result={page.result} /></section>)}</section> : null}
  </DialogContent></Dialog></>
}
