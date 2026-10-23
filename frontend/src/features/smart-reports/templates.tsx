import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { operationId, reportsPath } from './api'
import type { ViewState } from './contracts'

export function SaveReportTemplate({ state }: { state: ViewState }) {
  const [busy, setBusy] = useState(false)
  return <Button size="sm" variant="ghost" disabled={busy} onClick={async () => {
    setBusy(true)
    try { await api.post(`${reportsPath}/${state.report_id}/templates`, { operation_id: operationId(), view_session_id: state.session_id, expected_state_revision: state.state_revision }); toast.success('Template saved to your Reports library') }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }}>Save template</Button>
}

export function ReportTemplates() {
  const epoch = useAuthStore(s => s.securityEpoch)
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const templates = useInfiniteQuery({ queryKey: ['report-templates', epoch], initialPageParam: '', queryFn: ({ pageParam, signal }) => api.get<{ templates: { template_id: string; title: string }[]; next_cursor: string | null }>(`${reportsPath}/templates?after=${encodeURIComponent(pageParam)}`, signal), getNextPageParam: page => page.next_cursor })
  return <details className="max-w-3xl rounded-md border p-3"><summary className="cursor-pointer text-sm font-medium">Your templates</summary><div className="mt-3 space-y-2">{templates.isError ? <p role="alert">{templates.error.message}</p> : templates.isPending ? <p role="status">Loading templates…</p> : <>{!templates.data.pages.some(p => p.templates.length) ? <p className="text-sm text-muted-foreground">Open a report and save its validated definition as a template.</p> : null}{templates.data.pages.flatMap(p => p.templates).map(template => <div key={template.template_id} className="flex items-center justify-between gap-2 text-sm"><span>{template.title}</span><Button size="sm" variant="outline" disabled={busy} onClick={async () => {
    setBusy(true)
    try { const created = await api.post<{ report_id: string }>(`${reportsPath}/templates/${template.template_id}/instantiate`, { operation_id: operationId() }); await navigate({ to: '/studio/reports/$reportId', params: { reportId: created.report_id }, search: { edit: true } }) }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }}>Create draft</Button></div>)}{templates.hasNextPage ? <Button size="sm" variant="outline" onClick={() => void templates.fetchNextPage()}>More templates</Button> : null}</>}</div></details>
}
