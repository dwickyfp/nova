import { ReportTemplates } from './templates'
import { useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'
import { StudioDashboards } from '@/features/studio/studio-dashboards'
import { api } from '@/lib/api-client'
import { reportApi, reportsPath } from './api'
import type { ReportSpec } from './contracts'

export function ReportsLibrary() {
  const epoch = useAuthStore(s => s.securityEpoch)
  const navigate = useNavigate()
  const [prompt, setPrompt] = useState('')
  const [search, setSearch] = useState('')
  const [proposal, setProposal] = useState<ReportSpec | null>(null)
  const [clarification, setClarification] = useState('')
  const [busy, setBusy] = useState(false)
  const [legacy, setLegacy] = useState(false)
  const capabilities = useQuery({ queryKey: ['report-capabilities', epoch], queryFn: ({ signal }) => api.get<{ default_timezone: string }>(`${reportsPath}/capabilities`, signal) })
  const reports = useInfiniteQuery({ queryKey: ['reports-library', epoch], initialPageParam: '', queryFn: ({ pageParam, signal }) => reportApi.list(pageParam, signal), getNextPageParam: page => page.next_cursor })
  const create = async (spec: ReportSpec) => {
    setBusy(true)
    try { const report = await reportApi.create(spec); await navigate({ to: '/studio/reports/$reportId', params: { reportId: report.report_id }, search: { edit: true } }) }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <div className="flex min-h-0 flex-1 flex-col">
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-3"><h1 className="text-lg font-semibold">Reports</h1><div className="flex gap-2"><Button variant={legacy ? 'secondary' : 'ghost'} size="sm" onClick={() => setLegacy(v => !v)}>{legacy ? 'Smart Reports' : 'Legacy dashboards'}</Button><Button variant="outline" size="sm" disabled={busy || !capabilities.data} onClick={() => void create({ schema_version: '1.0.0', title: 'Untitled report', description: '', timezone: capabilities.data!.default_timezone, locale: 'id-ID', sources: [], filters: [], interactions: [], pages: [{ id: 'overview', title: 'Overview', sections: [{ id: 'main', title: '', widgets: [] }] }] })}>New report</Button></div></header>
    {legacy ? <StudioDashboards /> : <div className="min-h-0 flex-1 space-y-6 overflow-auto p-4 md:p-6">
      <form className="max-w-3xl space-y-2" onSubmit={async e => {
        e.preventDefault(); setBusy(true); setClarification(''); setProposal(null)
        try { const result = await reportApi.plan(prompt); setProposal(result.spec ?? null); setClarification(result.clarification ?? '') }
        catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
      }}><label htmlFor="report-prompt" className="text-sm font-medium">What do you want to understand?</label><div className="flex gap-2"><Input id="report-prompt" value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Revenue by city, with monthly trends and a city filter" minLength={3} maxLength={4000} required /><Button disabled={busy} type="submit">{busy ? 'Preparing…' : 'Plan report'}</Button></div></form>
      {clarification ? <p role="status" className="max-w-3xl text-sm">{clarification}</p> : null}
      {proposal ? <section className="max-w-3xl space-y-3 rounded-lg border p-4"><h2 className="font-medium">{proposal.title}</h2><p className="text-sm text-muted-foreground">{proposal.description}</p><ul className="list-inside list-disc text-sm">{proposal.pages.flatMap(p => p.sections.flatMap(s => s.widgets)).map(w => <li key={w.id}>{w.title} · {w.kind}</li>)}</ul><Button disabled={busy} onClick={() => void create(proposal)}>Create private draft</Button></section> : null}
      <ReportTemplates />
      <Input aria-label="Find a report" className="max-w-sm" placeholder="Find a report" value={search} onChange={e => setSearch(e.target.value)} />
      {reports.isPending ? <p role="status" className="text-sm text-muted-foreground">Loading reports…</p> : reports.isError ? <div role="alert" className="space-y-2"><p>{reports.error.message}</p><Button variant="outline" onClick={() => void reports.refetch()}>Retry</Button></div> : <>
        {!reports.data.pages.some(p => p.reports.length) ? <p className="text-sm text-muted-foreground">No reports yet. Describe a report or start with a blank draft.</p> : null}
        <ul className="divide-y rounded-lg border">{reports.data.pages.flatMap(p => p.reports).filter(r => r.title.toLowerCase().includes(search.toLowerCase())).map(report => <li key={report.report_id}><Link to="/studio/reports/$reportId" params={{ reportId: report.report_id }} className="flex flex-wrap items-center justify-between gap-2 p-4 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><div><h2 className="text-sm font-medium">{report.title}</h2><p className="text-xs text-muted-foreground">{report.owner} · {report.permission}{report.migration_status === 'source_changed' ? ' · Legacy source changed; draft preserved' : ''}</p></div><span className="text-xs text-muted-foreground">{report.requires_mapping ? 'Requires mapping' : report.published_revision ? `Published v${report.published_revision}` : 'Private draft'}</span></Link></li>)}</ul>
        {reports.hasNextPage ? <Button variant="outline" disabled={reports.isFetchingNextPage} onClick={() => void reports.fetchNextPage()}>Load more</Button> : null}
      </>}
    </div>}
  </div>
}
