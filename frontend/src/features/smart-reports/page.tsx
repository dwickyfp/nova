import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { agentsApi, AUTO_AGENT_ID } from '@/features/agents/api'
import { SmartReport, SmartReportRenderer } from './renderer'
import { ReportEditor } from './editor'
import { useReport } from './use-report'
import { reportApi, reportsPath, operationId } from './api'
import type { ReportBlock, ViewState } from './contracts'

export function ReportPage({ reportId, handoff, edit }: { reportId: string; handoff?: string; edit?: boolean }) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const client = useQueryClient()
  const navigate = useNavigate()
  const report = useQuery({ queryKey: ['report-summary', epoch, reportId], queryFn: ({ signal }) => reportApi.get(reportId, signal) })
  const [editing, setEditing] = useState(!!edit)
  const [revision, setRevision] = useState<number>()
  const [sharing, setSharing] = useState(false)
  const [busy, setBusy] = useState(false)
  const ask = async (state: ViewState) => {
    try {
      if (!state.origin) {
        const thread = await agentsApi.createThread(AUTO_AGENT_ID, report.data?.title ?? 'Report analysis')
        state = await api.post<ViewState>(`${reportsPath}/${reportId}/view-sessions/${state.session_id}/conversation`, { expected_state_revision: state.state_revision, origin: { agent_id: AUTO_AGENT_ID, thread_id: thread.thread_id } })
      }
      await api.post(`${reportsPath}/${reportId}/context`, { report_id: reportId, view_session_id: state.session_id, state_revision: state.state_revision, selected_widget_id: state.selected_widget_id })
      await navigate({ to: '/studio', search: { agent: state.origin!.agent_id, thread: state.origin!.thread_id } })
      toast.message('Report context is attached. Ask your follow-up in this conversation.')
    } catch (error) { toast.error((error as Error).message) }
  }
  if (report.isPending) return <p role="status" className="p-4">Loading report…</p>
  if (report.isError) return <p role="alert" className="p-4">{report.error.message}</p>
  const summary = report.data
  const block: ReportBlock = { report_id: reportId, title: summary.title, fallback: 'Open the report to view current authorized data.', draft_id: editing || !summary.published_revision ? summary.draft_id : undefined, draft_revision: revision, published_revision: summary.published_revision || undefined }
  const startEdit = async () => {
    setBusy(true)
    try { const draft = await reportApi.draft(reportId); await report.refetch(); setRevision(draft.revision); setEditing(true) }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <div className="flex min-h-0 min-w-0 flex-1 flex-col">
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-2"><Link to="/studio/reports" className="text-sm text-muted-foreground hover:underline">Reports</Link><div className="flex flex-wrap gap-1"><Button size="sm" variant="ghost" onClick={async () => { await navigator.clipboard.writeText(`${location.origin}/studio/reports/${reportId}`); toast.message('Report link copied. Recipients need report and data access.') }}>Copy link</Button>{summary.permission === 'owner' ? <Button size="sm" variant="outline" onClick={() => setSharing(true)}>Share</Button> : null}{summary.permission !== 'viewer' ? <Button size="sm" variant="outline" disabled={busy} onClick={() => editing && summary.draft_id ? setEditing(false) : void startEdit()}>{editing && summary.draft_id ? 'Close editor' : 'Edit draft'}</Button> : null}</div></div>
    {!summary.published_revision && !summary.draft_id ? <div className="space-y-3 p-6"><p className="text-sm text-muted-foreground">This report has not been published. Create your draft to review and edit it.</p><Button disabled={busy} onClick={() => void startEdit()}>Create my draft</Button></div> : editing && block.draft_id ? <EditorWorkspace key={`${block.draft_id}:${revision ?? 'latest'}:${epoch}`} block={block} onApplied={setRevision} onAsk={ask} canPublish={['owner', 'publisher'].includes(summary.permission)} publishedRevision={summary.published_revision} onPublished={() => { void report.refetch(); void client.invalidateQueries({ queryKey: ['reports-library'] }); setEditing(false); setRevision(undefined) }} /> : <SmartReport key={`${reportId}:${epoch}`} block={block} handoff={handoff} full onAsk={ask} />}
    <SharingDialog reportId={reportId} open={sharing} onClose={() => setSharing(false)} />
  </div>
}

function EditorWorkspace({ block, publishedRevision, canPublish, onApplied, onPublished, onAsk }: { block: ReportBlock; publishedRevision: number; canPublish: boolean; onApplied: (revision: number) => void; onPublished: () => void; onAsk: (state: ViewState) => void }) {
  const report = useReport(block)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const publish = async () => {
    if (!report.state?.draft_id) return
    setBusy(true)
    try { await reportApi.publish(block.report_id, report.state.draft_id, report.state.spec_revision, publishedRevision); setConfirm(false); onPublished(); toast.success('Report published') }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row lg:overflow-hidden">
    <section className="smart-report flex min-h-0 min-w-0 flex-1 flex-col"><header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-3"><div><h1 className="text-base font-semibold">{report.spec?.title ?? block.title}</h1><p className="text-xs text-muted-foreground">Private draft · revision {report.state?.spec_revision}</p></div><div className="flex gap-2"><Button size="sm" variant="outline" disabled={!report.state} onClick={() => report.state && onAsk(report.state)}>Ask Nova</Button>{canPublish ? <Button size="sm" disabled={!report.state || busy} onClick={() => setConfirm(true)}>Publish</Button> : null}</div></header><div className="min-h-0 flex-1 overflow-auto p-4">{report.error ? <p role="alert">{report.error.message}</p> : !report.spec || !report.state ? <p role="status">Loading draft…</p> : <SmartReportRenderer spec={report.spec} state={report.state} result={report.result} busy={report.busy} querying={report.querying} onEvent={event => { void report.event(event).catch(error => toast.error((error as Error).message)) }} />}</div></section>
    {report.spec && report.state ? <ReportEditor spec={report.spec} state={report.state} onApplied={onApplied} publishedRevision={publishedRevision} canUndo={report.canUndo} canRedo={report.canRedo} canPublish={canPublish} /> : null}
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>Publish this draft?</DialogTitle><DialogDescription>This creates published version {publishedRevision + 1}. Existing report access stays in place.</DialogDescription></DialogHeader><Button disabled={busy} onClick={() => void publish()}>{busy ? 'Validating and publishing…' : 'Publish report'}</Button></DialogContent></Dialog>
  </div>
}

function SharingDialog({ reportId, open, onClose }: { reportId: string; open: boolean; onClose: () => void }) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const [busy, setBusy] = useState(false)
  const grants = useQuery({ queryKey: ['report-grants', epoch, reportId], enabled: open, queryFn: () => api.get<{ grants: { grant_id: string; revision: number; payload: { target_type: 'user' | 'role'; target_name: string; permission: 'viewer' | 'editor' | 'publisher'; revoked: boolean } }[] }>(`${reportsPath}/${reportId}/grants`) })
  const write = async (body: Record<string, unknown>) => {
    setBusy(true)
    try { await api.post(`${reportsPath}/${reportId}/grants`, { operation_id: operationId(), ...body }); await grants.refetch() }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <Dialog open={open} onOpenChange={value => !value && onClose()}><DialogContent><DialogHeader><DialogTitle>Report access</DialogTitle><DialogDescription>Data permissions still apply. Role grants use the recipient’s active role.</DialogDescription></DialogHeader>
    <form className="space-y-3" onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); const target_type = String(data.get('type')); const target_name = String(data.get('name')); const existing = grants.data?.grants.find(g => g.payload.target_type === target_type && g.payload.target_name === target_name); void write({ target_type, target_name, permission: data.get('permission'), expected_revision: existing?.revision ?? 0 }) }}><div className="flex gap-2"><select name="type" aria-label="Grant target type" className="rounded-md border bg-background px-2 text-sm"><option value="user">User</option><option value="role">Role</option></select><Input name="name" aria-label="User or role name" required maxLength={128} /></div><select name="permission" aria-label="Report permission" className="min-h-9 w-full rounded-md border bg-background px-2 text-sm"><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="publisher">Publisher</option></select><Button type="submit" disabled={busy}>Grant access</Button></form>
    {grants.isError ? <p role="alert">{grants.error.message}</p> : <ul className="space-y-2">{grants.data?.grants.filter(g => !g.payload.revoked).map(grant => <li key={grant.grant_id} className="flex items-center justify-between gap-2 text-sm"><span>{grant.payload.target_name} · {grant.payload.permission}</span><Button size="sm" variant="ghost" disabled={busy} onClick={() => void write({ ...grant.payload, expected_revision: grant.revision, revoked: true })}>Revoke</Button></li>)}</ul>}
  </DialogContent></Dialog>
}
