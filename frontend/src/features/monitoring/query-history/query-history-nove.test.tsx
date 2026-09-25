import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AssistantProvider } from '@/features/assistant'
import { MonitoringQueryHistory } from './index'

describe('Query History Nove action', () => {
  afterEach(() => vi.restoreAllMocks())

  it('maps the copilot failed status action to the history API ERROR filter', async () => {
    const historyUrls: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/monitoring/queries/history')) {
        historyUrls.push(url)
        return new Response(JSON.stringify({ items: [], total: 0 }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/assistant/threads') && init?.method === 'POST') {
        return new Response(JSON.stringify({ thread_id: 'history-thread' }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/assistant/threads/history-thread/messages') && init?.method === 'POST') {
        return new Response(
          'event: client_action\ndata: {"capability":"surface.set_filter","args":{"filter":"status","value":"FAILED"},"correlation_id":"filter-1","surface_id":"monitoring.query_history"}\n\nevent: done\ndata: {"message_id":"answer-1","finish_reason":"stop"}\n\n',
          { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
        )
      }
      if (url.endsWith('/assistant/threads/history-thread/application-events')) {
        return new Response(JSON.stringify({ recorded: true, verification: 'verified' }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(null, { status: 204 })
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const screen = await render(
      <QueryClientProvider client={client}>
        <AssistantProvider loadWorkspaceDefaults={false}>
          <MonitoringQueryHistory />
        </AssistantProvider>
      </QueryClientProvider>,
    )
    await screen.getByRole('button', { name: 'Ask Nove' }).click()
    await vi.waitFor(() => expect(historyUrls.some((url) => url.includes('status=ERROR'))).toBe(true))
  })
})
