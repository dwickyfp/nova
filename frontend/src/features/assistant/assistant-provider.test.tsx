import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AssistantProvider, useAssistant } from './assistant-provider'
import type { WorkspaceTreeResponse } from '@/features/workspaces/types'

function makeTree(overrides: Partial<WorkspaceTreeResponse> = {}): WorkspaceTreeResponse {
  return {
    root_name: 'workspace',
    entries: [],
    open_tabs: [],
    active_tab: null,
    sidebar_collapsed: false,
    assistant_collapsed: false,
    defaults: { database: 'analytics', schema: 'public', role: 'ACCOUNTADMIN' },
    ...overrides,
  }
}

/** Routes /workspaces/tree to the supplied tree and records every request. */
function mockTree(tree: WorkspaceTreeResponse) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('/workspaces/tree')) {
      return new Response(JSON.stringify(tree), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(null, { status: 204 })
  })
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function Probe() {
  const { open, toggle, collapsedToPersist, setBinding, conversation } = useAssistant()
  return (
    <div>
      <span data-testid='open'>{String(open)}</span>
      <span data-testid='collapsed'>{String(collapsedToPersist())}</span>
      <span data-testid='thread'>{conversation.threadId ?? 'none'}</span>
      <button type='button' onClick={toggle}>
        toggle
      </button>
      <button
        type='button'
        onClick={() =>
          setBinding({
            key: 'file-1',
            context: { database: 'tab_db', schema: null, role: null },
            ensureThread: async () => 'thread-a',
          })
        }
      >
        bind
      </button>
      <button type='button' onClick={() => setBinding(null)}>
        unbind
      </button>
      <button type='button' onClick={() => void conversation.sendMessage('hi')}>
        send
      </button>
    </div>
  )
}

function renderProbe(tree: WorkspaceTreeResponse) {
  mockTree(tree)
  return render(
    <QueryClientProvider client={makeClient()}>
      <AssistantProvider>
        <Probe />
      </AssistantProvider>
    </QueryClientProvider>
  )
}

describe('AssistantProvider', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('restores the open panel from the fetched tree without WorkspacesPage', async () => {
    const { getByTestId } = await renderProbe(makeTree({ assistant_collapsed: false }))

    await expect.element(getByTestId('open')).toHaveTextContent('true')
    await expect.element(getByTestId('collapsed')).toHaveTextContent('false')
  })

  it('keeps a collapsed panel closed when the tree says so', async () => {
    const { getByTestId } = await renderProbe(makeTree({ assistant_collapsed: true }))

    await expect.element(getByTestId('open')).toHaveTextContent('false')
  })

  it('fetches the tree with the shared workspace-tree key exactly once', async () => {
    const fetchSpy = mockTree(makeTree())
    await render(
      <QueryClientProvider client={makeClient()}>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
      </QueryClientProvider>
    )

    await vi.waitFor(() => {
      const treeCalls = fetchSpy.mock.calls.filter(([input]) =>
        String(input).includes('/workspaces/tree')
      )
      expect(treeCalls).toHaveLength(1)
    })
  })

  it('applies the persisted value only once, so a later refetch cannot clobber a toggle', async () => {
    const client = makeClient()
    const fetchSpy = mockTree(makeTree({ assistant_collapsed: false }))
    const { getByTestId, getByRole } = await render(
      <QueryClientProvider client={client}>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
      </QueryClientProvider>
    )
    await expect.element(getByTestId('open')).toHaveTextContent('true')

    await getByRole('button', { name: 'toggle' }).click()
    await expect.element(getByTestId('open')).toHaveTextContent('false')

    const before = fetchSpy.mock.calls.filter(([input]) =>
      String(input).includes('/workspaces/tree')
    ).length
    await client.refetchQueries({ queryKey: ['workspace-tree'] })
    await vi.waitFor(() => {
      const after = fetchSpy.mock.calls.filter(([input]) =>
        String(input).includes('/workspaces/tree')
      ).length
      expect(after).toBeGreaterThan(before)
    })
    await expect.element(getByTestId('open')).toHaveTextContent('false')
  })

  it('keeps the composer live off the workspace by creating a file-less thread', async () => {
    const fetchSpy = mockTree(makeTree())
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/workspaces/tree')) {
        return new Response(JSON.stringify(makeTree()), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/assistant/threads') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            thread_id: 'global-thread',
            title: 'Global',
            workspace_file_id: null,
            created_at: '2026-09-19T00:00:00Z',
            updated_at: '2026-09-19T00:00:00Z',
            message_count: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      }
      if (url.endsWith('/messages')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode('event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n')
            )
            controller.close()
          },
        })
        return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      return new Response(null, { status: 204 })
    })

    const { getByTestId, getByRole } = await render(
      <QueryClientProvider client={makeClient()}>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
      </QueryClientProvider>
    )

    await getByRole('button', { name: 'send' }).click()
    await expect.element(getByTestId('thread')).toHaveTextContent('global-thread')

    const created = fetchSpy.mock.calls.find(([input]) => String(input).endsWith('/assistant/threads'))
    expect(created).toBeDefined()
    expect(JSON.parse(String((created?.[1] as RequestInit).body))).toEqual({ workspace_file_id: null })
  })

  it('prefers a registered workspace binding over the default', async () => {
    const fetchSpy = mockTree(makeTree())
    fetchSpy.mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/workspaces/tree')) {
        return new Response(JSON.stringify(makeTree()), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/messages')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode('event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n')
            )
            controller.close()
          },
        })
        return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      return new Response(null, { status: 204 })
    })

    const { getByTestId, getByRole } = await render(
      <QueryClientProvider client={makeClient()}>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
      </QueryClientProvider>
    )

    await getByRole('button', { name: 'bind', exact: true }).click()
    await getByRole('button', { name: 'send' }).click()
    await expect.element(getByTestId('thread')).toHaveTextContent('thread-a')

    const messages = fetchSpy.mock.calls.find(([input]) => String(input).endsWith('/messages'))
    expect(JSON.parse(String((messages?.[1] as RequestInit).body))).toMatchObject({
      database: 'tab_db',
    })
  })
})
