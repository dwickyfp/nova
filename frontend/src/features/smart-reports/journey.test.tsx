import { afterEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router'
import { userEvent } from 'vitest/browser'
import { SmartReport } from './renderer'
import { ReportPage } from './page'
import { reportSpecSchema, viewStateSchema, type ReportBlock } from './contracts'
import fixture from './fixtures/basic.json'

afterEach(() => {
  vi.restoreAllMocks()
  window.history.replaceState({}, '', window.location.href)
})

it('keeps Jakarta → full → Bandung → Ask Nova → chat on the same server session', async () => {
  const spec = reportSpecSchema.parse(fixture.spec)
  const origin = { agent_id: 'agent-a', thread_id: 'thread-a' }
  const block: ReportBlock = { report_id: 'report-a', draft_id: 'draft-a', draft_revision: 1, title: spec.title, fallback: 'Open report' }
  let state = viewStateSchema.parse({ ...fixture.state, draft_id: 'draft-a', origin })
  let consumed = false
  const attached: string[] = []
  const requests: string[] = []
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), window.location.origin)
    const path = url.pathname.replace('/api/v1/agents/studio/reports', '')
    requests.push(path)
    const body = init?.body ? JSON.parse(String(init.body)) : {}
    let data: unknown
    if (path === '/report-a') data = { report_id: 'report-a', title: spec.title, owner: 'alice', published_revision: 0, draft_id: 'draft-a', permission: 'owner', revision: 1 }
    else if (path.includes('/drafts/')) data = { spec, revision: 1 }
    else if (path.endsWith('/events')) {
      expect(body.expected_state_revision).toBe(state.state_revision)
      expect(body.type).toBe('FILTER_SET')
      state = { ...state, state_revision: state.state_revision + 1, filters: { ...state.filters, [body.target]: body.value } }
      data = { state }
    } else if (path.includes('/view-sessions')) data = state
    else if (path.includes('/filter-options/')) data = { values: ['Jakarta', 'Bandung'], next_offset: null }
    else if (path.endsWith('/render')) data = {
      report_id: 'report-a', spec_revision: 1, state_revision: state.state_revision,
      state_fingerprint: `state-${state.state_revision}`, as_of: '2026-10-23T00:00:00Z', status: 'success',
      widgets: [{ widget_id: 'revenue', status: 'success', columns: ['total_revenue'], rows: [['100.00']], completeness: 'complete', not_applied: [], totals: {}, has_more: false },
        { widget_id: 'cities', status: 'empty', columns: [], rows: [], completeness: 'complete', not_applied: [], totals: {}, has_more: false }],
    }
    else if (path.endsWith('/handoffs')) data = { target_path: '/studio/reports/report-a?handoff=single-use-token' }
    else if (path === '/handoffs/consume') {
      expect(consumed).toBe(false)
      consumed = true
      data = state
    } else if (path.endsWith('/context')) {
      expect(body.view_session_id).toBe(state.session_id)
      expect(body.state_revision).toBe(state.state_revision)
      attached.push(String(state.filters.city.values[0]))
      data = { reference: body }
    } else throw new Error(`Unexpected report request ${path}`)
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  const root = createRootRoute({ component: Outlet })
  const chat = createRoute({ getParentRoute: () => root, path: '/studio', component: () => <SmartReport block={block} origin={origin} /> })
  const full = createRoute({ getParentRoute: () => root, path: '/studio/reports/$reportId', validateSearch: (value: Record<string, unknown>) => ({ handoff: typeof value.handoff === 'string' ? value.handoff : undefined }), component: () => {
    const { reportId } = full.useParams()
    const { handoff } = full.useSearch()
    return <ReportPage reportId={reportId} handoff={handoff} />
  } })
  const router = createRouter({ routeTree: root.addChildren([chat, full]), history: createMemoryHistory({ initialEntries: ['/studio'] }) })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const screen = await render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
  await screen.getByRole('button', { name: 'Expand', exact: true }).click()
  await expect.element(screen.getByRole('combobox', { name: 'City', exact: true })).toHaveValue('"Jakarta"')
  await screen.getByRole('button', { name: 'Open full' }).click()
  await expect.poll(() => consumed).toBe(true)
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'City', exact: true }), '"Bandung"')
  await expect.poll(() => state.filters.city.values[0]).toBe('Bandung')
  await screen.getByRole('button', { name: 'More actions', exact: true }).click()
  screen.getByRole('button', { name: 'Save template', exact: true }).element().focus()
  await userEvent.keyboard('{Escape}')
  await expect.element(screen.getByRole('button', { name: 'Activity & schedules', exact: true })).not.toBeInTheDocument()
  await expect.element(screen.getByRole('button', { name: 'More actions', exact: true })).toHaveFocus()
  await screen.getByRole('button', { name: 'Ask Nova', exact: true }).click()
  await expect.poll(() => attached).toEqual(['Bandung'])
  await screen.getByRole('button', { name: 'Expand', exact: true }).click()
  await expect.element(screen.getByRole('combobox', { name: 'City', exact: true })).toHaveValue('"Bandung"')
  expect(spec.filters[0].default.values).toEqual(['Jakarta'])
  expect(requests.filter(path => path === '/handoffs/consume')).toHaveLength(1)
  expect(requests.some(path => path.includes('/plan') || path.includes('/publish'))).toBe(false)
  await screen.unmount()
  client.clear()
})
