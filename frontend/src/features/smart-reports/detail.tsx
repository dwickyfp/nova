import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { reportsPath } from './api'
import { ReportTable } from './table'
import type { RenderResult, Scalar, ViewState, Widget } from './contracts'

export function ReportDetail({ widget, state, field, values }: { widget: Widget; state: ViewState; field: string; values: Scalar[] }) {
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(0)
  const epoch = useAuthStore(s => s.securityEpoch)
  const result = useQuery({ queryKey: ['report-detail', epoch, state.session_id, state.state_revision, widget.id, field, values, page], enabled: open,
    queryFn: () => api.post<RenderResult>(`${reportsPath}/${state.report_id}/widgets/${widget.id}/detail`, { view_session_id: state.session_id, expected_state_revision: state.state_revision, field, values, page_index: page }),
  })
  if (!widget.detail_fields?.length) return null
  const data = result.data?.widgets[0]
  return <><Button size="sm" variant="outline" disabled={!values.length} onClick={() => { setPage(0); setOpen(true) }}>Open row details</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-h-[85dvh] overflow-auto sm:max-w-3xl"><DialogHeader><DialogTitle>{widget.title} · row details</DialogTitle><DialogDescription>Selected {field}: {values.map(v => String(v ?? '(Blank)')).join(', ')}. Only configured, permitted fields are shown. Maximum 2,000 rows.</DialogDescription></DialogHeader>
    {result.isError ? <p role="alert">{result.error.message}</p> : result.isPending ? <p role="status">Loading authorized rows…</p> : data && ['success', 'empty'].includes(data.status) ? <><ReportTable result={data} /><div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>Page {page + 1}</span><Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous rows</Button><Button variant="outline" size="sm" disabled={!data.has_more || page >= 19} onClick={() => setPage(p => p + 1)}>Next rows</Button></div>{page === 19 && data.has_more ? <p className="text-sm text-muted-foreground">Row limit reached. Refine the report filters to inspect a smaller selection.</p> : null}</> : <p role="alert">{data?.message ?? 'Row details unavailable'}</p>}
  </DialogContent></Dialog></>
}
