import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { AssistantPanel } from './assistant-panel'
import { MessageList } from './message-list'
import { useAssistantTurn } from './use-assistant-turn'

const resetGrant = vi.fn()

vi.mock('./thread-client', () => ({
  resetGrant: (...args: unknown[]) => resetGrant(...args),
}))

type Turn = ReturnType<typeof useAssistantTurn>

function Harness({
  holder,
  ensureThread = async () => 't-1',
  context,
  onError,
}: {
  holder: { current: Turn | null }
  ensureThread?: () => Promise<string | null>
  context?: { database?: string | null; schema?: string | null; role?: string | null }
  onError?: (message: string) => void
}) {
  const turn = useAssistantTurn({ ensureThread, context, onError })
  holder.current = turn
  return <MessageList messages={turn.messages} statusMessage={turn.statusMessage} />
}

function PanelHarness({
  holder,
  onError,
}: {
  holder: { current: Turn | null }
  onError?: (message: string) => void
}) {
  const turn = useAssistantTurn({ ensureThread: async () => 't-1', onError })
  holder.current = turn
  return (
    <AssistantPanel
      open
      onOpenChange={() => {}}
      messages={turn.messages}
      statusMessage={turn.statusMessage}
      grantActive={turn.grantActive}
      onResetPermissions={turn.resetPermissions}
      resettingPermissions={turn.resettingGrant}
    />
  )
}

function sseResponse(frames: string[]) {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

afterEach(() => {
  resetGrant.mockReset()
  vi.restoreAllMocks()
})

describe('useAssistantTurn', () => {
  it('creates a thread lazily and streams deltas into the transcript', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        sseResponse([
          'event: text_delta\ndata: {"text":"Hello "}\n\n',
          'event: text_delta\ndata: {"text":"world"}\n\n',
          'event: done\ndata: {"message_id":"m1","finish_reason":"stop"}\n\n',
        ])
      )
    const ensureThread = vi.fn(async () => 't-created')
    const holder: { current: Turn | null } = { current: null }
    const { getByText } = await render(<Harness holder={holder} ensureThread={ensureThread} />)

    await holder.current!.sendMessage('hi')

    expect(ensureThread).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/assistant/threads/t-created/messages',
      expect.anything()
    )
    await expect.element(getByText('Hello world')).toBeInTheDocument()
  })

  it('sends the active worksheet context with the turn', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(sseResponse(['event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n']))
    const holder: { current: Turn | null } = { current: null }
    await render(
      <Harness
        holder={holder}
        context={{ database: 'analytics', schema: 'public', role: 'analyst' }}
      />
    )

    await holder.current!.sendMessage('hi')
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body).toEqual({
      content: 'hi',
      database: 'analytics',
      schema: 'public',
      role: 'analyst',
    })
  })

  it('does not open a stream when no thread can be resolved', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    const onError = vi.fn()
    const holder: { current: Turn | null } = { current: null }
    await render(<Harness holder={holder} ensureThread={async () => null} onError={onError} />)

    await holder.current!.sendMessage('hi')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalled()
  })

  it('stops a running turn, keeps the partial answer and marks it cancelled', async () => {
    let streamClosed = false
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode('event: text_delta\ndata: {"text":"partial"}\n\n')
          )
          init?.signal?.addEventListener('abort', () => {
            streamClosed = true
            controller.close()
          })
        },
      })
      return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    })
    const holder: { current: Turn | null } = { current: null }
    const { getByText } = await render(<Harness holder={holder} />)

    const pending = holder.current!.sendMessage('hi')
    await vi.waitFor(async () => {
      await expect.element(getByText('partial')).toBeInTheDocument()
    })
    holder.current!.stop()
    await pending

    expect(streamClosed).toBe(true)
    await expect.element(getByText('partial')).toBeInTheDocument()
    await expect.element(getByText('Stopped before the answer finished.')).toBeInTheDocument()
  })

  it('records a transport error without throwing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'provider unavailable' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      })
    )
    const holder: { current: Turn | null } = { current: null }
    const { getByText } = await render(<Harness holder={holder} />)

    await holder.current!.sendMessage('hi')
    await expect.element(getByText('provider unavailable')).toBeInTheDocument()
  })
})

/** Routes fetch: the SSE turn to a done frame, the decision to a grant response. */
function mockTurnThenDecision(grantActive: boolean) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/messages')) {
      return sseResponse(['event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n'])
    }
    if (url.endsWith('/decision')) {
      const body = JSON.parse((init?.body as string) ?? '{}')
      return new Response(
        JSON.stringify({
          tool_call_id: 'call-1',
          status: 'approved',
          grant_active: body.decision === 'allow_session' ? grantActive : false,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    return new Response(null, { status: 204 })
  })
}

describe('useAssistantTurn grant reset', () => {
  it('tracks the grant from an allow_session decision in the panel', async () => {
    mockTurnThenDecision(true)
    const holder: { current: Turn | null } = { current: null }
    const { getByRole } = await render(<PanelHarness holder={holder} />)
    await holder.current!.sendMessage('hi')

    expect(holder.current!.grantActive).toBe(false)
    await holder.current!.decide({ toolCallId: 'call-1', decision: 'approve', alwaysAllow: true })

    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true))
    await expect.element(getByRole('button', { name: 'Reset permissions' })).toBeInTheDocument()
  })

  it('revokes the grant through the panel control and stops claiming it is active', async () => {
    mockTurnThenDecision(true)
    resetGrant.mockResolvedValue(undefined)
    const holder: { current: Turn | null } = { current: null }
    const { getByRole, getByText, container } = await render(<PanelHarness holder={holder} />)
    await holder.current!.sendMessage('hi')
    await holder.current!.decide({ toolCallId: 'call-1', decision: 'approve', alwaysAllow: true })
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true))

    await getByRole('button', { name: 'Reset permissions' }).click()

    expect(resetGrant).toHaveBeenCalledWith('t-1')
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(false))
    await expect
      .element(getByText('Read-only queries are allowed in this conversation.'))
      .not.toBeInTheDocument()
    expect(container.textContent).not.toContain('Reset permissions')
  })

  it('surfaces a failed reset instead of swallowing it', async () => {
    mockTurnThenDecision(true)
    resetGrant.mockRejectedValue(new Error('Thread not found'))
    const onError = vi.fn()
    const holder: { current: Turn | null } = { current: null }
    const { getByRole, getByText } = await render(
      <PanelHarness holder={holder} onError={onError} />
    )
    await holder.current!.sendMessage('hi')
    await holder.current!.decide({ toolCallId: 'call-1', decision: 'approve', alwaysAllow: true })
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true))

    await getByRole('button', { name: 'Reset permissions' }).click()

    await expect.element(getByText('Thread not found')).toBeInTheDocument()
    expect(onError).toHaveBeenCalledWith('Thread not found')
    expect(holder.current!.grantActive).toBe(true)
  })
})
