import { WidgetSettings } from './widget-settings'
import { PageEditor } from './page-editor'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { reportApi, reportsPath, operationId } from './api'
import { DraftDataPreview } from './draft-preview'
import { ReportReviews } from './reviews'
import type { Patch, PatchRequest, ReportSpec, Source, ViewState, Widget } from './contracts'

type SourceCatalog = Source & { name: string; metrics: { name: string }[]; dimensions: { name: string }[] }
const control = 'min-h-9 w-full rounded-md border border-input bg-background px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring'

export function ReportEditor({ spec, state, onApplied, publishedRevision, canUndo, canRedo, canPublish }: { canUndo: boolean; canRedo: boolean; canPublish: boolean; spec: ReportSpec; state: ViewState; onApplied: (revision: number) => void; publishedRevision: number }) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const [prompt, setPrompt] = useState('')
  const [pending, setPending] = useState<PatchRequest | null>(null)
  const [rebase, setRebase] = useState<{ operation_id: string; expected_revision: number; expected_published_revision: number; restore_revision?: number; review_id?: string } | null>(null)
  const [preview, setPreview] = useState<ReportSpec | null>(null)
  const [busy, setBusy] = useState(false)
  const [sourceId, setSourceId] = useState('')
  const [metric, setMetric] = useState('')
  const [dimension, setDimension] = useState('')
  const [secondDimension, setSecondDimension] = useState('')
  const [kind, setKind] = useState<Widget['kind']>('bar')
  const [tab, setTab] = useState<'inspector' | 'history'>('inspector')
  const sources = useQuery({ queryKey: ['report-sources', epoch], queryFn: ({ signal }) => api.get<{ sources: SourceCatalog[] }>(`${reportsPath}/sources`, signal) })
  const history = useQuery({ queryKey: ['report-history', epoch, state.report_id, state.draft_id, state.spec_revision], queryFn: () => api.get<{ revisions: { revision: number; payload: { updated_at: string } }[] }>(`${reportsPath}/${state.report_id}/drafts/${state.draft_id}/history`), enabled: tab === 'history' })
  const published = useQuery({ queryKey: ['report-published-history', epoch, state.report_id, publishedRevision], queryFn: () => api.get<{ revisions: { revision: number; actor: string; published_at: string }[] }>(`${reportsPath}/${state.report_id}/revisions`), enabled: tab === 'history' })
  const selected = spec.pages.flatMap(p => p.sections.flatMap(s => s.widgets)).find(w => w.id === state.selected_widget_id)
  const source = sources.data?.sources.find(s => s.id === sourceId)
  const prepare = async (operations: Patch[], supplied?: PatchRequest) => {
    if (!state.draft_id) return
    setBusy(true)
    try {
      const body = supplied ?? { operation_id: operationId(), expected_revision: state.spec_revision, operations }
      const result = await reportApi.preview(state.report_id, state.draft_id, body)
      setPending(body); setRebase(null); setPreview(result.after)
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!state.draft_id || (!pending && !rebase)) return
    setBusy(true)
    try { const result = rebase ? await api.post<{ revision: number }>(`${reportsPath}/${state.report_id}/drafts/${state.draft_id}/rebase`, rebase) : await reportApi.patch(state.report_id, state.draft_id, pending!); setPending(null); setRebase(null); setPreview(null); onApplied(result.revision) }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const comparePublished = async (revision?: number) => {
    setBusy(true)
    try {
      const current = await reportApi.get(state.report_id)
      const version = await reportApi.spec(state.report_id, null, revision ?? current.published_revision)
      setPending(null); setPreview(version.spec)
      setRebase({ operation_id: operationId(), expected_revision: state.spec_revision, expected_published_revision: current.published_revision, ...(revision ? { restore_revision: revision } : {}) })
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const addWidget = () => {
    if (!source || !metric) return
    const section = spec.pages.find(p => p.id === state.active_page_id)!.sections[0]
    const sourceRef = { id: source.id, view_id: source.view_id, version: source.version, fingerprint: source.fingerprint }
    const query = { source: source.id, metrics: [metric], dimensions: kind === 'kpi' ? [] : [dimension, ...(['pivot', 'heatmap', 'map'].includes(kind) ? [secondDimension] : [])].filter(Boolean) }
    const operations: Patch[] = spec.sources.some(s => s.id === source.id) ? [] : [{ op: 'SET_SOURCES', value: { sources: [...spec.sources, sourceRef] } }]
    operations.push(selected?.mapping_required ? { op: 'SET_WIDGET_QUERY', target: selected.id, value: query } : { op: 'ADD_WIDGET', parent: section.id, value: {
      id: `w_${operationId()}`, title: metric, kind, query,
      encoding: { x: kind === 'kpi' ? null : dimension || null, y: [metric], series: kind === 'heatmap' ? secondDimension : null, ...(kind === 'map' ? { latitude: dimension, longitude: secondDimension } : {}) }, pivot_columns: kind === 'pivot' ? [secondDimension] : [], placement: { col: 0, row: Math.max(0, ...section.widgets.map(w => w.placement.row + w.placement.height)), width: 12, height: 2 },
    } })
    void prepare(operations)
  }
  return <aside aria-label="Report editor" className="flex min-h-0 w-full shrink-0 flex-col border-t bg-background lg:w-80 lg:border-l lg:border-t-0">
    <header className="flex shrink-0 gap-1 border-b p-2"><Button size="sm" variant={tab === 'inspector' ? 'secondary' : 'ghost'} onClick={() => setTab('inspector')}>Inspector</Button><Button size="sm" variant={tab === 'history' ? 'secondary' : 'ghost'} onClick={() => setTab('history')}>History</Button></header>
    <div className="min-h-0 flex-1 space-y-5 overflow-auto p-4">
      <p className="text-xs text-muted-foreground">Changes stay in your private draft until submitted for review or published.</p>
      <div className="flex gap-2">{(['UNDO_DRAFT', 'REDO_DRAFT'] as const).map(op => <Button key={op} size="sm" variant="outline" disabled={busy || (op === 'UNDO_DRAFT' ? !canUndo : !canRedo)} onClick={() => void prepare([{ op }])}>{op === 'UNDO_DRAFT' ? 'Undo' : 'Redo'}</Button>)}</div>
      {preview && (pending || rebase) ? <section className="space-y-2 rounded-md border p-3"><h3 className="text-sm font-medium">Review draft change</h3><p className="text-sm">{preview.title}</p><ul className="space-y-1 text-xs text-muted-foreground">{pending?.operations.map((op, i) => <li key={i}>{op.op.toLowerCase().replace(/_/g, ' ')}{op.target ? ` · ${op.target}` : ''}</li>)}</ul>{rebase ? <><p className="text-xs text-muted-foreground">{rebase.review_id ? 'Copy the submitted revision into your private draft, replacing its current definition.' : rebase.restore_revision ? `Restore published version ${rebase.restore_revision} into your draft.` : `Keep your draft as the replacement for published version ${rebase.expected_published_revision}.` } Publishing is a separate action.</p><details className="text-xs"><summary className="cursor-pointer">Compare report definitions</summary><div className="mt-2 grid gap-3">{[{ label: 'Your draft', definition: spec }, { label: rebase.review_id ? 'Submitted version' : 'Published version', definition: preview }].map(({ label, definition }) => <section key={label} className="space-y-1 rounded-md border p-2"><h4 className="font-medium">{label}: {definition.title}</h4><p>{definition.timezone} · Fiscal start month {definition.calendar?.fiscal_start_month ?? 1}</p><ul className="space-y-1">{definition.pages.flatMap(page => page.sections.flatMap(section => section.widgets)).map(widget => <li key={widget.id}>{widget.title} · {widget.kind.replace(/_/g, ' ')}{widget.query ? ` · ${widget.query.metrics.join(', ')}${widget.query.dimensions.length ? ` by ${widget.query.dimensions.join(', ')}` : ''}` : ' · Requires mapping'}</li>)}</ul></section>)}</div></details></> : null}<div className="flex flex-wrap gap-2">{pending && state.draft_id ? <DraftDataPreview reportId={state.report_id} draftId={state.draft_id} patch={pending} /> : null}<Button disabled={busy} size="sm" onClick={() => void apply()}>Apply to draft</Button><Button disabled={busy} variant="ghost" size="sm" onClick={() => { setPending(null); setRebase(null); setPreview(null) }}>Discard</Button></div></section> : null}
      {tab === 'history' ? <><ReportReviews key={`${state.draft_id}:${state.spec_revision}`} state={state} canPublish={canPublish} onReview={async (review, definition) => {
        const current = await reportApi.get(state.report_id)
        setPending(null); setPreview(definition)
        setRebase({ operation_id: operationId(), expected_revision: state.spec_revision, expected_published_revision: current.published_revision, review_id: review.review_id })
      }} />{publishedRevision ? <section className="space-y-2"><h3 className="text-sm font-medium">Published versions</h3><Button size="sm" variant="outline" disabled={busy} onClick={() => void comparePublished()}>Compare latest published</Button>{published.isError ? <p role="alert">{published.error.message}</p> : <ol className="space-y-2">{published.data?.revisions.map(version => <li key={version.revision} className="flex items-center justify-between gap-2 text-sm"><span>Version {version.revision} · {version.actor}</span><Button size="sm" variant="outline" disabled={busy} onClick={() => void comparePublished(version.revision)}>Restore to draft</Button></li>)}</ol>}</section> : null}{history.isPending ? <p role="status">Loading history…</p> : history.isError ? <p role="alert">{history.error.message}</p> : <ol className="space-y-2">{history.data?.revisions.map(r => <li key={r.revision} className="flex items-center justify-between gap-2 text-sm"><span>Draft {r.revision}</span><Button size="sm" variant="outline" disabled={busy || r.revision === state.spec_revision} onClick={() => void prepare([{ op: 'RESTORE_DRAFT', value: { revision: r.revision } }])}>Restore</Button></li>)}</ol>}</> : <>
        <form className="space-y-2" onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void prepare([{ op: 'SET_REPORT_METADATA', value: { title: String(data.get('title')), description: String(data.get('description')), timezone: String(data.get('timezone')), calendar: { fiscal_start_month: Number(data.get('fiscal_start_month')), week_start: Number(data.get('week_start')) } } }]) }}>
          <Label htmlFor="report-title">Title</Label><Input id="report-title" name="title" defaultValue={spec.title} key={spec.title} required maxLength={256} />
          <Label htmlFor="report-description">Description</Label><Input id="report-description" name="description" defaultValue={spec.description} key={spec.description} maxLength={4000} /><Label htmlFor="report-timezone">Timezone</Label><Input id="report-timezone" name="timezone" defaultValue={spec.timezone} key={spec.timezone} required /><Label htmlFor="report-fiscal">Fiscal year starts</Label><select id="report-fiscal" name="fiscal_start_month" className={control} defaultValue={spec.calendar?.fiscal_start_month ?? 1}>{Array.from({ length: 12 }, (_, i) => <option key={i} value={i + 1}>{new Intl.DateTimeFormat(spec.locale, { month: 'long', timeZone: 'UTC' }).format(new Date(Date.UTC(2024, i, 1)))}</option>)}</select><Label htmlFor="report-week">Week starts</Label><select id="report-week" name="week_start" className={control} defaultValue={spec.calendar?.week_start ?? 0}>{['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((day, index) => <option key={day} value={index}>{day}</option>)}</select><Button type="submit" size="sm" variant="outline" disabled={busy}>Review changes</Button>
        </form>
        <form className="space-y-2" onSubmit={async e => { e.preventDefault(); if (!state.draft_id) return; setBusy(true); try { const result = await reportApi.proposal(state.report_id, state.draft_id, prompt); if (result.patch) await prepare(result.patch.operations, result.patch); else toast.message(result.clarification) } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) } }}><Label htmlFor="report-edit-prompt">Ask Nova to edit</Label><Input id="report-edit-prompt" value={prompt} onChange={e => setPrompt(e.target.value)} minLength={3} maxLength={4000} required placeholder="Add a monthly revenue trend" /><Button size="sm" variant="outline" type="submit" disabled={busy}>Preview proposal</Button></form>
        {selected ? <form className="space-y-2 border-t pt-4" onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void prepare([{ op: 'SET_WIDGET_VISUAL', target: selected.id, value: { title: String(data.get('title')) } }, { op: 'MOVE_WIDGET', target: selected.id, value: { col: Number(data.get('col')), row: Number(data.get('row')), width: Number(data.get('width')), height: Number(data.get('height')) } }]) }} key={`${selected.id}:${state.spec_revision}`}>
          <h3 className="text-sm font-medium">{selected.title}</h3><Label htmlFor="widget-title">Widget title</Label><Input id="widget-title" name="title" defaultValue={selected.title} required /><div className="grid grid-cols-3 gap-2">{(['col', 'row', 'width', 'height'] as const).map(field => <label key={field} className="text-xs">{field === 'col' ? 'Column' : field === 'row' ? 'Row' : field === 'width' ? 'Width' : 'Height'}<Input name={field} type="number" min={['width', 'height'].includes(field) ? 1 : 0} max={field === 'col' ? 11 : field === 'width' ? 12 : field === 'height' ? 20 : 10000} defaultValue={selected.placement[field]} /></label>)}</div><Button type="submit" variant="outline" size="sm" disabled={busy}>Review widget</Button><Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void prepare([{ op: 'REMOVE_WIDGET', target: selected.id }])}>Remove widget</Button>
        </form> : <p className="text-xs text-muted-foreground">Select a widget to change its title or layout.</p>}
        {selected ? <WidgetSettings key={`${selected.id}:${state.spec_revision}`} widget={selected} catalog={sources.data?.sources.find(s => s.view_id === spec.sources.find(source => source.id === selected.query?.source)?.view_id)} busy={busy} prepare={prepare} /> : null}
        <section className="space-y-2 border-t pt-4"><h3 className="text-sm font-medium">{selected?.mapping_required ? 'Map selected widget' : 'Add widget'}</h3>
          <Label htmlFor="widget-source">Semantic source</Label><select id="widget-source" className={control} value={sourceId} onChange={e => { setSourceId(e.target.value); setMetric(''); setDimension('') }}><option value="">Choose published source</option>{sources.data?.sources.map(s => <option key={s.id} value={s.id}>{s.name} · v{s.version}</option>)}</select>
          {sources.isError ? <p role="alert" className="text-xs">Sources unavailable.</p> : null}
          <Label htmlFor="widget-metric">Metric</Label><select id="widget-metric" className={control} value={metric} onChange={e => setMetric(e.target.value)}><option value="">Choose metric</option>{source?.metrics.map(m => <option key={m.name} value={m.name}>{m.name}</option>)}</select>
          <Label htmlFor="widget-dimension">Group by</Label><select id="widget-dimension" className={control} value={dimension} onChange={e => setDimension(e.target.value)}><option value="">No grouping</option>{source?.dimensions.map(d => <option key={d.name} value={d.name}>{d.name}</option>)}</select>
          <Label htmlFor="widget-kind">Visual</Label><select id="widget-kind" className={control} value={kind} onChange={e => setKind(e.target.value as Widget['kind'])}>{['kpi', 'bar', 'line', 'area', 'table', 'stacked_bar', 'grouped_bar', 'combo', 'scatter', 'donut', 'funnel', 'heatmap', 'waterfall', 'pivot', 'map'].map(k => <option key={k} value={k}>{k.replace(/_/g, ' ')}</option>)}</select>
          {['pivot', 'heatmap', 'map'].includes(kind) ? <><Label htmlFor="widget-second-dimension">{kind === 'map' ? 'Longitude (use latitude as Group by)' : 'Column dimension'}</Label><select id="widget-second-dimension" className={control} value={secondDimension} onChange={e => setSecondDimension(e.target.value)}><option value="">Choose dimension</option>{source?.dimensions.filter(d => d.name !== dimension).map(d => <option key={d.name}>{d.name}</option>)}</select></> : null}
          <Button size="sm" variant="outline" disabled={busy || !metric || (['pivot', 'heatmap', 'map'].includes(kind) && (!dimension || !secondDimension))} onClick={addWidget}>Review widget</Button>
          <Button size="sm" variant="ghost" disabled={busy || !source || !dimension} onClick={() => {
            if (!source) return
            const operations: Patch[] = spec.sources.some(s => s.id === source.id) ? [] : [{ op: 'SET_SOURCES', value: { sources: [...spec.sources, { id: source.id, view_id: source.view_id, version: source.version, fingerprint: source.fingerprint }] } }]
            operations.push({ op: 'ADD_FILTER', value: { id: `f_${operationId()}`, label: dimension, source: source.id, field: dimension, kind: 'single' } })
            void prepare(operations)
          }}>Use dimension as filter</Button>
        </section>
        <PageEditor spec={spec} busy={busy} prepare={prepare} />
        <Button size="sm" variant="outline" disabled={busy || spec.pages.length >= 10} onClick={() => void prepare([{ op: 'ADD_PAGE', value: { id: `p_${operationId()}`, title: `Page ${spec.pages.length + 1}`, sections: [{ id: `s_${operationId()}`, widgets: [] }] } }])}>Add page</Button>
      </>}
    </div>
  </aside>
}
