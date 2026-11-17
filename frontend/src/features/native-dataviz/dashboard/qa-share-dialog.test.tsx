import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import type { DashboardDefinition } from '../contracts'
import { ShareDialog } from './share-dialog'
import '@/styles/index.css'

// Release QA for sharing (docs/reports/native-dataviz/QA_MATRIX.csv).

const DASHBOARD = 'aaaaaaaa-0000-4000-8000-0000000000d1'
const WORKSPACE = 'team-space'
const definition = { dashboard_id: DASHBOARD, title: 'Orders', pages: [] } as unknown as DashboardDefinition
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
const grant = (subject_id: string) => ({ subject_type: 'role', subject_id, permission: 'view', expires_at: null,
  granted_by: 'owner', granted_at: '2026-11-17T00:00:00Z' })

let requests: string[] = []
beforeEach(() => {
  requests = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input instanceof Request ? input.url : input)
    requests.push(url)
    if (url.includes(`/workspaces/spaces/${WORKSPACE}/shares`)) return json({ shares: [{ role: 'VIEWER', permission: 'view' }] })
    if (url.includes(`/viz/dashboards/${DASHBOARD}/grants`)) return json({ revision: 1, grants: [grant('VIEWER'), grant('OUTSIDERS')] })
    return json({})
  }))
})
afterEach(async () => { await cleanup(); vi.unstubAllGlobals() })

describe('NDV-ACL-017 sharing with a role outside the workspace', () => {
  it('says the role cannot open the dashboard until the workspace is shared with it', async () => {
    const screen = await render(<QueryClientProvider client={new QueryClient()}>
      <ShareDialog open onOpenChange={() => undefined} dashboardId={DASHBOARD} workspaceId={WORKSPACE} definition={definition} />
    </QueryClientProvider>)
    const list = screen.getByRole('list', { name: 'People and roles with access' })
    await expect.element(list.getByText('OUTSIDERS can’t open this dashboard yet', { exact: false })).toBeVisible()
    expect(list.getByText('VIEWER can’t open this dashboard yet', { exact: false }).elements()).toHaveLength(0)
    await expect.element(screen.getByText('They also need access to its workspace.', { exact: false })).toBeVisible()
    // A role added in the dialog is checked the same way before saving.
    await screen.getByRole('textbox', { name: 'Role name' }).fill('STRANGERS')
    await screen.getByRole('button', { name: 'Add' }).click()
    await expect.element(list.getByText('STRANGERS can’t open this dashboard yet', { exact: false })).toBeVisible()
    expect(requests.some(url => url.includes(`/workspaces/spaces/${WORKSPACE}/shares`))).toBe(true)
  })

  it('shows no workspace note when the workspace roles cannot be read', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input instanceof Request ? input.url : input)
      if (url.includes('/workspaces/spaces/')) return new Response('{}', { status: 403, headers: { 'Content-Type': 'application/json' } })
      return json({ revision: 1, grants: [grant('OUTSIDERS')] })
    }))
    const screen = await render(<QueryClientProvider client={new QueryClient()}>
      <ShareDialog open onOpenChange={() => undefined} dashboardId={DASHBOARD} workspaceId={WORKSPACE} definition={definition} />
    </QueryClientProvider>)
    await expect.element(screen.getByRole('button', { name: 'Remove OUTSIDERS' })).toBeVisible()
    expect(screen.getByText('can’t open this dashboard yet', { exact: false }).elements()).toHaveLength(0)
  })
})
