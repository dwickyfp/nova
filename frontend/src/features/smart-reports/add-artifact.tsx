import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { reportApi, reportsPath, operationId } from './api'

export function AddArtifactToReport({ artifactId }: { artifactId: string }) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const reports = useQuery({ queryKey: ['reports-library-picker', epoch], queryFn: () => reportApi.list(), enabled: open })
  const add = async () => {
    if (!selected) return
    setBusy(true)
    try {
      const draft = await reportApi.draft(selected)
      const { spec, revision } = await reportApi.spec(selected, draft.draft_id, draft.revision)
      const result = await api.post<{ requires_mapping: boolean }>(`${reportsPath}/${selected}/drafts/${draft.draft_id}/artifacts`, { operation_id: operationId(), expected_revision: revision, artifact_id: artifactId, section_id: spec.pages[0].sections[0].id })
      setOpen(false)
      toast.success(result.requires_mapping ? 'Added to draft. Map this artifact to a semantic source before publishing.' : 'Widget copied to draft')
      await navigate({ to: '/studio/reports/$reportId', params: { reportId: selected }, search: { edit: true } })
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <><Button variant="outline" size="sm" onClick={() => setOpen(true)}>Add to report</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>Add artifact to a report</DialogTitle><DialogDescription>A copy is added to your draft. Published reports stay unchanged. SQL-only artifacts require semantic mapping.</DialogDescription></DialogHeader>{reports.isError ? <p role="alert">{reports.error.message}</p> : <select aria-label="Destination report" className="min-h-9 w-full rounded-md border bg-background px-2 text-sm" value={selected} onChange={e => setSelected(e.target.value)}><option value="">Choose report</option>{reports.data?.reports.filter(r => r.permission !== 'viewer').map(r => <option key={r.report_id} value={r.report_id}>{r.title}</option>)}</select>}<Button disabled={!selected || busy} onClick={() => void add()}>{busy ? 'Adding…' : 'Add to draft'}</Button></DialogContent></Dialog></>
}
