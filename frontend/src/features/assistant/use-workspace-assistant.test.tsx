import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { AssistantPanel } from './assistant-panel'
import { MessageList } from './message-list'
import type { AssistantEvent } from './types'
import { useWorkspaceAssistant } from './use-workspace-assistant'

const createThread = vi.fn()
const streamAssistantTurn = vi.fn()
const resetGrant = vi.fn()

vi.mock('./thread-client', () => ({
  createThread: (...args: unknown[]) => createThread(...args),
  resetGrant: (...args: unknown[]) => resetGrant(...args),
}))

vi.mock('./stream-client', async () => {
  const actual = await vi.importActual<typeof import('./stream-client')>('./stream-client')
  return {
    ...actual,
    streamAssistantTurn: (...args: unknown[]) => streamAssistantTurn(...args),
  }
})

type Assistant = ReturnType<typeof useWorkspaceAssistant>

function Harness({
  holder,
  fileId,
  database,
}: {
  holder: { current: Assistant | null }
  fileId: string | null
  database?: string
}) {
  const assistant = useWorkspaceAssistant({
    fileId,
    context: { database: database ?? null, schema: null, role: null },
  })
  holder.current = assistant
  return <MessageList messages={assistant.messages} statusMessage={assistant.statusMessage} />
}

function PanelHarness({
  holder,
  fileId,
  onError,
}: {
  holder: { current: Assistant | null }
  fileId: string | null
  onError?: (message: string) => void
}) {
  const assistant = useWorkspaceAssistant({
    fileId,
    context: { database: null, schema: null, role: null },
    onError,
  })
  holder.current = assistant
  return (
    <AssistantPanel
      open
      onOpenChange={() => {}}
      messages={assistant.messages}
      statusMessage={assistant.statusMessage}
      grantActive={assistant.grantActive}
      onResetPermissions={assistant.resetPermissions}
      resettingPermissions={assistant.resettingGrant}
    />
  )
}

/** Routes fetch: the SSE turn to a done frame, the decision to a grant response. */
function mockTurnThenSessionGrant() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/messages')) {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode('event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n')
          )
          controller.close()
        },
      })
      return new Response(body, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith('/decision')) {
      const body = JSON.parse((init?.body as string) ?? '{}')
      return new Response(
        JSON.stringify({
          tool_call_id: 'call-1',
          status: 'approved',
          grant_active: body.decision === 'allow_session',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    return new Response(null, { status: 204 })
  })
}

afterEach(() => {
  createThread.mockReset()
  streamAssistantTurn.mockReset()
  resetGrant.mockReset()
})

function echoStream() {
  streamAssistantTurn.mockImplementation(
    async (_thread: string, _content: string, opts: { onEvent: (e: AssistantEvent) => void }) => {
      opts.onEvent({ type: 'text_delta', text: 'ok' })
      opts.onEvent({ type: 'done', message_id: 'm', finish_reason: 'stop' })
    }
  )
}

describe('useWorkspaceAssistant', () => {
  it('creates one thread per file and reuses it for the next send', async () => {
    createThread.mockResolvedValue({ thread_id: 'thread-a' })
    echoStream()
    const holder: { current: Assistant | null } = { current: null }
    await render(<Harness holder={holder} fileId='file-1' />)

    await holder.current!.sendMessage('first')
    await holder.current!.sendMessage('second')

    expect(createThread).toHaveBeenCalledTimes(1)
    expect(createThread).toHaveBeenCalledWith('file-1')
    expect(streamAssistantTurn).toHaveBeenNthCalledWith(
      2,
      'thread-a',
      'second',
      expect.objectContaining({ database: null })
    )
  })

  it('forwards the active file context on the turn', async () => {
    createThread.mockResolvedValue({ thread_id: 'thread-a' })
    echoStream()
    const holder: { current: Assistant | null } = { current: null }
    await render(<Harness holder={holder} fileId='file-1' database='analytics' />)

    await holder.current!.sendMessage('hi')
    expect(streamAssistantTurn).toHaveBeenCalledWith(
      'thread-a',
      'hi',
      expect.objectContaining({ database: 'analytics' })
    )
  })

  it('refuses to send when no file is open', async () => {
    const holder: { current: Assistant | null } = { current: null }
    await render(<Harness holder={holder} fileId={null} />)

    await holder.current!.sendMessage('hi')
    expect(createThread).not.toHaveBeenCalled()
    expect(streamAssistantTurn).not.toHaveBeenCalled()
  })

  it('starts a fresh transcript when the active file changes', async () => {
    createThread.mockResolvedValue({ thread_id: 'thread-a' })
    echoStream()
    const holder: { current: Assistant | null } = { current: null }
    const view = await render(<Harness holder={holder} fileId='file-1' />)

    await holder.current!.sendMessage('hi')
    await expect.element(view.getByText('ok')).toBeInTheDocument()

    await view.rerender(<Harness holder={holder} fileId='file-2' />)
    await vi.waitFor(() => {
      expect(view.container.textContent).not.toContain('ok')
    })
  })
})

describe('useWorkspaceAssistant grant lifecycle across files', () => {
  it('revokes the previous conversation grant when the active file changes', async () => {
    createThread.mockResolvedValueOnce({ thread_id: 'thread-a' })
    createThread.mockResolvedValueOnce({ thread_id: 'thread-b' })
    mockTurnThenSessionGrant()
    resetGrant.mockResolvedValue(undefined)
    const holder: { current: Assistant | null } = { current: null }
    const view = await render(<PanelHarness holder={holder} fileId='file-1' />)

    await holder.current!.sendMessage('hi')
    await holder.current!.decide({ toolCallId: 'call-1', decision: 'approve', alwaysAllow: true })
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true))

    await view.rerender(<PanelHarness holder={holder} fileId='file-2' />)

    await vi.waitFor(() => expect(resetGrant).toHaveBeenCalledWith('thread-a'))
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(false))
    await expect
      .element(view.getByText('Read-only queries are allowed in this conversation.'))
      .not.toBeInTheDocument()
  })

  it('reuses the file thread for the next send after a switch, with no grant carried over', async () => {
    createThread.mockResolvedValueOnce({ thread_id: 'thread-a' })
    createThread.mockResolvedValueOnce({ thread_id: 'thread-b' })
    mockTurnThenSessionGrant()
    resetGrant.mockResolvedValue(undefined)
    const holder: { current: Assistant | null } = { current: null }
    const view = await render(<PanelHarness holder={holder} fileId='file-1' />)

    await holder.current!.sendMessage('hi')
    await holder.current!.decide({ toolCallId: 'call-1', decision: 'approve', alwaysAllow: true })
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true))

    await view.rerender(<PanelHarness holder={holder} fileId='file-2' />)
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(false))

    await view.rerender(<PanelHarness holder={holder} fileId='file-1' />)
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(false))

    echoStream()
    await holder.current!.sendMessage('again')
    expect(streamAssistantTurn).toHaveBeenLastCalledWith('thread-a', 'again', expect.anything())
  })

  it('clears the stale conversation on switch even when the revoke fails, and the next send opens a new thread', async () => {
    createThread.mockResolvedValueOnce({ thread_id: 'thread-a' })
    createThread.mockResolvedValueOnce({ thread_id: 'thread-b' })
    mockTurnThenSessionGrant()
    resetGrant.mockRejectedValue(new Error('Network unreachable'))
    const onError = vi.fn()
    const holder: { current: Assistant | null } = { current: null }
    const view = await render(
      <PanelHarness holder={holder} fileId='file-1' onError={onError} />
    )

    await holder.current!.sendMessage('hi')
    await holder.current!.decide({ toolCallId: 'call-1', decision: 'approve', alwaysAllow: true })
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true))

    await view.rerender(<PanelHarness holder={holder} fileId='file-2' onError={onError} />)

    await vi.waitFor(() => expect(resetGrant).toHaveBeenCalledWith('thread-a'))
    await vi.waitFor(() => expect(holder.current!.threadId).toBeNull())
    expect(holder.current!.grantActive).toBe(false)
    expect(holder.current!.messages.map((message) => message.content)).toEqual(['Network unreachable'])
    expect(onError).toHaveBeenCalledWith('Network unreachable')

    echoStream()
    await holder.current!.sendMessage('sent-on-file-2')

    expect(streamAssistantTurn).toHaveBeenLastCalledWith(
      'thread-b',
      'sent-on-file-2',
      expect.anything()
    )
  })

  it('sends from the new file into the new thread while the old revoke is still in flight', async () => {
    createThread.mockResolvedValueOnce({ thread_id: 'thread-a' })
    createThread.mockResolvedValueOnce({ thread_id: 'thread-b' })
    mockTurnThenSessionGrant()
    let releaseRevoke: () => void = () => {}
    resetGrant.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          releaseRevoke = resolve
        })
    )
    const holder: { current: Assistant | null } = { current: null }
    const view = await render(<PanelHarness holder={holder} fileId='file-1' />)

    await holder.current!.sendMessage('hi')
    await holder.current!.decide({ toolCallId: 'call-1', decision: 'approve', alwaysAllow: true })
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true))

    await view.rerender(<PanelHarness holder={holder} fileId='file-2' />)
    await vi.waitFor(() => expect(resetGrant).toHaveBeenCalledWith('thread-a'))

    expect(holder.current!.threadId).toBeNull()
    expect(holder.current!.grantActive).toBe(false)

    echoStream()
    await holder.current!.sendMessage('sent-on-file-2-during-flight')

    expect(streamAssistantTurn).toHaveBeenLastCalledWith('thread-b', 'sent-on-file-2-during-flight', expect.anything())
    const lastCall = streamAssistantTurn.mock.calls[streamAssistantTurn.mock.calls.length - 1]
    expect(lastCall[0]).not.toBe('thread-a')

    releaseRevoke()
    await vi.waitFor(() => expect(holder.current!.threadId).toBe('thread-b'))
  })

  it('clears the stale conversation on switch when the revoke answers 404', async () => {
    createThread.mockResolvedValueOnce({ thread_id: 'thread-a' })
    createThread.mockResolvedValueOnce({ thread_id: 'thread-b' })
    mockTurnThenSessionGrant()
    resetGrant.mockResolvedValue(undefined)
    const onError = vi.fn()
    const holder: { current: Assistant | null } = { current: null }
    const view = await render(
      <PanelHarness holder={holder} fileId='file-1' onError={onError} />
    )

    await holder.current!.sendMessage('hi')
    await holder.current!.decide({ toolCallId: 'call-1', decision: 'approve', alwaysAllow: true })
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true))

    await view.rerender(<PanelHarness holder={holder} fileId='file-2' onError={onError} />)

    await vi.waitFor(() => expect(holder.current!.threadId).toBeNull())
    expect(holder.current!.grantActive).toBe(false)
    expect(holder.current!.messages).toHaveLength(0)

    echoStream()
    await holder.current!.sendMessage('sent-on-file-2')

    expect(streamAssistantTurn).toHaveBeenLastCalledWith(
      'thread-b',
      'sent-on-file-2',
      expect.anything()
    )
  })
})
