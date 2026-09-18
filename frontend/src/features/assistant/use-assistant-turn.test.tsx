import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { MessageList } from './message-list'
import { useAssistantTurn } from './use-assistant-turn'

type Turn = ReturnType<typeof useAssistantTurn>

function Harness({ holder }: { holder: { current: Turn | null } }) {
  const turn = useAssistantTurn({ threadId: 't-1' })
  holder.current = turn
  return <MessageList messages={turn.messages} statusMessage={turn.statusMessage} />
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
  vi.restoreAllMocks()
})

describe('useAssistantTurn', () => {
  it('streams deltas into the transcript and ends the turn', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([
        'event: text_delta\ndata: {"text":"Hello "}\n\n',
        'event: text_delta\ndata: {"text":"world"}\n\n',
        'event: done\ndata: {"message_id":"m1","finish_reason":"stop"}\n\n',
      ])
    )
    const holder: { current: Turn | null } = { current: null }
    const { getByText } = await render(<Harness holder={holder} />)

    await holder.current!.sendMessage('hi')
    await expect.element(getByText('Hello world')).toBeInTheDocument()
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
