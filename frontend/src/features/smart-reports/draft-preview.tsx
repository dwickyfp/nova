import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { reportsPath } from './api'
import { reportSpecSchema, viewStateSchema, type PatchRequest, type RenderResult } from './contracts'
import { SmartReportRenderer } from './renderer'

export function DraftDataPreview({ reportId, draftId, patch }: { reportId: string; draftId: string; patch: PatchRequest }) {
  const [open, setOpen] = useState(false)
  return <><Button size="sm" variant="outline" onClick={() => setOpen(true)}>Preview data</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="flex max-h-[90dvh] min-h-0 flex-col sm:max-w-5xl">
    <DialogHeader><DialogTitle>Draft data preview</DialogTitle><DialogDescription>Uses the proposed definition and its default filters with your current data access. Your draft and report view are unchanged.</DialogDescription></DialogHeader>
    {open ? <PreviewContents reportId={reportId} draftId={draftId} patch={patch} /> : null}
  </DialogContent></Dialog></>
}

function PreviewContents({ reportId, draftId, patch }: { reportId: string; draftId: string; patch: PatchRequest }) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const [pageId, setPageId] = useState<string | null>(null)
  const preview = useQuery({
    queryKey: ['report-draft-preview', epoch, reportId, draftId, patch, pageId],
    queryFn: async ({ signal }) => {
      const response = await api.post<{ spec: unknown; state: unknown; result: RenderResult }>(`${reportsPath}/${reportId}/drafts/${draftId}/preview-render`, { patch, page_id: pageId }, signal)
      return { spec: reportSpecSchema.parse(response.spec), state: viewStateSchema.parse(response.state), result: response.result }
    },
    retry: false, gcTime: 0,
  })
  return <div className="min-h-0 space-y-4 overflow-auto">
    {preview.isError ? <p role="alert" className="text-sm">{preview.error.message}</p> : preview.isPending ? <p role="status" className="text-sm">Querying proposed report…</p> : <>
      <h2 className="text-base font-semibold">{preview.data.spec.title}</h2>
      {preview.data.spec.pages.length > 1 ? <label className="flex items-center gap-2 text-sm">Preview page<select className="min-h-9 min-w-0 rounded-md border bg-background px-2" value={preview.data.state.active_page_id} onChange={event => setPageId(event.target.value)}>{preview.data.spec.pages.map(page => <option key={page.id} value={page.id}>{page.title}</option>)}</select></label> : null}
      <SmartReportRenderer spec={preview.data.spec} state={preview.data.state} result={preview.data.result} />
    </>}
  </div>
}
