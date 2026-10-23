import { useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from '@/context/theme-provider'
import { SmartReportRenderer } from './renderer'
import type { ReportSpec, ViewState, RenderResult } from './contracts'
import '@/styles/index.css'
import './snapshot.css'

type Snapshot = { spec: ReportSpec; pages: { state: ViewState; result: RenderResult }[] }
export function SnapshotView({ snapshot }: { snapshot: Snapshot }) {
  useEffect(() => {
    document.querySelectorAll('details').forEach(detail => { detail.open = true })
    document.documentElement.dataset.snapshotReady = 'true'
  }, [])
  return <ThemeProvider defaultTheme="light"><main className="report-snapshot bg-background text-foreground">{snapshot.pages.map(page => <section key={page.state.active_page_id} data-snapshot-page className="smart-report p-6"><header className="mb-4 border-b pb-3"><h1 className="text-lg font-semibold">{snapshot.spec.title}</h1><p className="text-sm text-muted-foreground">{snapshot.spec.pages.find(p => p.id === page.state.active_page_id)?.title} · Revision {page.state.spec_revision}</p></header><SmartReportRenderer spec={snapshot.spec} state={page.state} result={page.result} /></section>)}</main></ThemeProvider>
}

const token = new URLSearchParams(location.search).get('token') ?? ''
void fetch(`/snapshot-data?token=${encodeURIComponent(token)}`).then(async response => {
  if (!response.ok) throw new Error('Snapshot unavailable')
  const snapshot = await response.json() as Snapshot
  createRoot(document.getElementById('root')!).render(<SnapshotView snapshot={snapshot} />)
}).catch(() => { document.getElementById('root')!.textContent = 'Snapshot unavailable'; document.documentElement.dataset.snapshotError = 'true' })
