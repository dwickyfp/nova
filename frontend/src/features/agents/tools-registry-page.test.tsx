import { afterEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { signInNavigation } from '@/lib/api-client'
import { ToolsRegistryPage } from './tools-registry-page'
import '@/styles/index.css'

vi.mock('@/components/layout/header', () => ({ Header: () => null }))

const tool = {
  tool_id: 'tool-1', owner_name: '__nova__', name: 'fixture_total',
  description: 'Synthetic arithmetic', source: 'mcp:server-1', is_enabled: true,
}
const server = {
  server_id: 'server-1', name: 'Fixture', transport: 'http',
  description: 'Synthetic QA', is_active: true, last_status: 'connected',
}
const clients: QueryClient[] = []
const screens: Awaited<ReturnType<typeof render>>[] = []

afterEach(async () => {
  for (const screen of screens.splice(0)) await screen.unmount()
  clients.splice(0).forEach((client) => client.clear())
  useAuthStore.getState().auth.setUser(null)
  vi.restoreAllMocks()
})

async function setup(activeRole: string | null = 'ANALYST') {
  vi.spyOn(signInNavigation, 'open').mockImplementation(() => {})
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ACCOUNTADMIN', 'ANALYST'], activeRole })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const screen = await render(<QueryClientProvider client={client}><ToolsRegistryPage /></QueryClientProvider>)
  screens.push(screen)
  return screen
}

function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}

it.each(['ANALYST', null])('keeps platform tool controls read-only with active role %s', async (activeRole) => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
    response(String(input).endsWith('/tools') ? { tools: [tool] } : { servers: [server] }))
  const screen = await setup(activeRole)
  await expect.element(screen.getByRole('switch', { name: 'Enable fixture_total' })).toBeDisabled()
  await expect.element(screen.getByText('MCP', { exact: true })).toBeVisible()
  await screen.getByRole('tab', { name: 'MCP servers' }).click()
  await expect.element(screen.getByText('Managing MCP connectors requires the active ACCOUNTADMIN role.')).toBeVisible()
  await expect.element(screen.getByRole('button', { name: 'Add MCP server' })).not.toBeInTheDocument()
})

it('lets the active administrator toggle a discovered platform tool', async () => {
  const mutations: unknown[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    if (init?.method === 'PATCH') {
      mutations.push(JSON.parse(init.body as string))
      return response({ ...tool, is_enabled: false })
    }
    return response(String(input).endsWith('/tools')
      ? { tools: [{ ...tool, is_enabled: mutations.length === 0 }] } : { servers: [server] })
  })
  const screen = await setup('ACCOUNTADMIN')
  const toggle = screen.getByRole('switch', { name: 'Enable fixture_total' })
  await expect.element(toggle).toBeEnabled()
  await toggle.click()
  await vi.waitFor(() => expect(mutations).toEqual([{ is_enabled: false }]))
  await expect.element(toggle).not.toBeChecked()
  await screen.getByRole('tab', { name: 'MCP servers' }).click()
  await expect.element(screen.getByRole('button', { name: 'Add MCP server' })).toBeVisible()
})

it.each(['tools', 'MCP servers'])('shows a recoverable error for %s instead of an empty registry', async (tab) => {
  let recover = false
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const data = String(input).endsWith('/tools') ? { tools: [tool] } : { servers: [server] }
    return recover ? response(data) : response({ detail: 'Fixture unavailable' }, 503)
  })
  const screen = await setup('ACCOUNTADMIN')
  if (tab === 'MCP servers') await screen.getByRole('tab', { name: tab }).click()
  await expect.element(screen.getByRole('alert')).toBeVisible()
  recover = true
  await screen.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect.element(screen.getByText(tab === 'tools' ? 'fixture_total' : 'Fixture', { exact: true })).toBeVisible()
  await expect.element(screen.getByRole('alert')).not.toBeInTheDocument()
})

it('shows the hints the MCP server declares, as information only', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
    response(String(input).endsWith('/tools')
      ? { tools: [
          { ...tool, annotations: { readOnlyHint: true, destructiveHint: false } },
          { ...tool, tool_id: 'tool-2', name: 'wipe_targets', annotations: { destructiveHint: true } },
          { ...tool, tool_id: 'tool-3', name: 'plain_tool', annotations: {} },
        ] }
      : { servers: [server] }))
  const screen = await setup('ACCOUNTADMIN')
  await expect.element(screen.getByText('Only reads')).toBeVisible()
  await expect.element(screen.getByText('May delete or overwrite data')).toBeVisible()
  await expect.element(screen.getByText('Not described')).toBeVisible()
  await expect.element(screen.getByRole('button', { name: 'Confirm read-only' })).not.toBeInTheDocument()
})

it('sends auth headers write-only and shows only their names', async () => {
  const created: Record<string, unknown>[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    if (init?.method === 'POST' && String(input).endsWith('/mcp-servers')) {
      created.push(JSON.parse(init.body as string))
      return response({ ...server, server_id: 'server-2', auth_header_names: ['Authorization'] }, 201)
    }
    return response(String(input).endsWith('/tools') ? { tools: [] }
      : { servers: created.length ? [{ ...server, endpoint: 'http://127.0.0.1:8299/mcp', auth_header_names: ['Authorization'] }] : [] })
  })
  const screen = await setup('ACCOUNTADMIN')
  await screen.getByRole('tab', { name: 'MCP servers' }).click()
  await screen.getByRole('button', { name: 'Add MCP server' }).first().click()
  await screen.getByLabelText('Name').fill('Targets')
  await screen.getByLabelText('Endpoint URL').fill('http://127.0.0.1:8299/mcp')
  await screen.getByRole('button', { name: 'Add header' }).click()
  await screen.getByLabelText('Header 1 name').fill('Authorization')
  const value = screen.getByLabelText('Header 1 value')
  await expect.element(value).toHaveAttribute('type', 'password')
  await value.fill('Bearer fixture-secret')
  await screen.getByRole('button', { name: 'Add server' }).click()
  await vi.waitFor(() => expect(created).toHaveLength(1))
  expect(created[0].auth_headers).toEqual({ Authorization: 'Bearer fixture-secret' })
  await expect.element(screen.getByText('Auth headers: Authorization (values hidden)')).toBeVisible()
  expect(document.body.textContent).not.toContain('fixture-secret')
})
