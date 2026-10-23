import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, apiBase, authHeaders, errorDetail } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { operationId, reportsPath } from './api'
import type { ViewState } from './contracts'

export function ReportExport({ state }: { state: ViewState }) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const [busy, setBusy] = useState(false)
  const [format, setFormat] = useState('pdf')
  const active = useRef<AbortController | null>(null)
  useEffect(() => () => active.current?.abort(), [epoch])
  const download = async () => {
    const controller = new AbortController()
    active.current?.abort(); active.current = controller; setBusy(true)
    try {
      const result = await api.post<{ export_id: string }>(`${reportsPath}/${state.report_id}/exports`, { operation_id: operationId(), view_session_id: state.session_id, expected_state_revision: state.state_revision, format }, controller.signal)
      const response = await fetch(`${apiBase()}${reportsPath}/${state.report_id}/exports/${result.export_id}/download`, { headers: authHeaders(), signal: controller.signal })
      if (!response.ok) { const body = await response.json(); throw new Error(errorDetail(body.detail)) }
      const blob = await response.blob()
      if (controller.signal.aborted || useAuthStore.getState().securityEpoch !== epoch) return
      const url = URL.createObjectURL(blob), link = document.createElement('a')
      link.href = url; link.download = `report.${format}`
      document.body.append(link); link.click(); link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { if (!controller.signal.aborted) toast.error((error as Error).message) }
    finally { if (active.current === controller) setBusy(false) }
  }
  return <div className="flex gap-1"><select aria-label="Report export format" className="min-h-9 rounded-md border bg-background px-2 text-xs" value={format} onChange={e => setFormat(e.target.value)} disabled={busy}>{['pdf', 'xlsx', 'pptx'].map(fmt => <option key={fmt} value={fmt}>{fmt.toUpperCase()}</option>)}</select><Button size="sm" variant="outline" disabled={busy} onClick={() => void download()}>{busy ? 'Preparing all pages…' : 'Export'}</Button></div>
}
