import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { operationId, reportsPath } from './api'
import type { ViewState } from './contracts'

export function SavedViews({ state, onApply }: { state: ViewState; onApply: (id: string) => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const epoch = useAuthStore(s => s.securityEpoch)
  const views = useQuery({ queryKey: ['report-saved-views', epoch, state.report_id], enabled: open, queryFn: ({ signal }) => api.get<{ views: { preset_id: string; name: string; draft_id: string | null; spec_revision: number }[] }>(`${reportsPath}/${state.report_id}/saved-views`, signal) })
  const save = async () => {
    setBusy(true)
    try { await api.post(`${reportsPath}/${state.report_id}/saved-views`, { operation_id: operationId(), view_session_id: state.session_id, name }); setName(''); await views.refetch() } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <><Button size="sm" variant="ghost" onClick={() => setOpen(true)}>Saved views</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>Personal saved views</DialogTitle><DialogDescription>Save filters, sorting, and exploration for this revision and your active role.</DialogDescription></DialogHeader>
    <form className="flex gap-2" onSubmit={e => { e.preventDefault(); void save() }}><Input aria-label="Saved view name" value={name} onChange={e => setName(e.target.value)} required minLength={1} maxLength={128} /><Button disabled={busy} type="submit">Save current</Button></form>
    {views.isPending ? <p role="status">Loading saved views…</p> : views.isError ? <p role="alert">{views.error.message}</p> : <ul className="max-h-64 space-y-2 overflow-auto">{views.data.views.map(view => <li key={view.preset_id} className="flex items-center justify-between gap-2 text-sm"><span>{view.name} · {view.draft_id ? 'draft' : 'published'} revision {view.spec_revision}</span><Button size="sm" variant="outline" disabled={busy || view.draft_id !== state.draft_id || view.spec_revision !== state.spec_revision} onClick={async () => { setBusy(true); try { await onApply(view.preset_id); setOpen(false) } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) } }}>Apply</Button></li>)}</ul>}
  </DialogContent></Dialog></>
}
