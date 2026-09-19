import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import type { AssistantEvent } from './types'
import { useAssistantConversation } from './use-assistant-conversation'

const streamAssistantTurn = vi.fn()
const resetGrant = vi.fn()

vi.mock('./thread-client', () => ({
  createThread: vi.fn(),
  resetGrant: (...args: unknown[]) => resetGrant(...args),
}))

vi.mock('./stream-client', async () => {
  const actual = await vi.importActual<typeof import('./stream-client')>('./stream-client')
  return {
    ...actual,
    streamAssistantTurn: (...args: unknown[]) => streamAssistantTurn(...args),
  }
})

type Conversation = ReturnType<typeof useAssistantConversation>

function Harness({
  holder,
  bindingKey,
  ensureThread,
  retainOnLeave,
}: {
  holder: { current: Conversation | null }
  bindingKey: string | null
  ensureThread: () => Promise<string | null>
  retainOnLeave?: (key: string | null) => boolean
}) {
  const conversation = useAssistantConversation({
    ensureThread,
    context: { database: null, schema: null, role: null },
    bindingKey,
    retainOnLeave,
  })
  holder.current = conversation
  return <span data-testid='count'>{conversation.messages.length}</span>
}

function echoStream() {
  streamAssistantTurn.mockImplementation(
    async (_t: string, _c: string, opts: { onEvent: (e: AssistantEvent) => void }) => {
      opts.onEvent({ type: 'text_delta', text: 'ok' })
      opts.onEvent({ type: 'done', message_id: 'm', finish_reason: 'stop' })
    }
  )
}

afterEach(() => {
  streamAssistantTurn.mockReset()
  resetGrant.mockReset()
})

describe('useAssistantConversation', () => {
  it('resolves the thread through the supplied ensureThread', async () => {
    echoStream()
    const ensureThread = vi.fn(async () => 'thread-a')
    const holder: { current: Conversation | null } = { current: null }
    await render(<Harness holder={holder} bindingKey='file-1' ensureThread={ensureThread} />)

    await holder.current!.sendMessage('hi')

    expect(ensureThread).toHaveBeenCalledTimes(1)
    expect(streamAssistantTurn).toHaveBeenCalledWith('thread-a', 'hi', expect.anything())
  })

  it('aborts the turn when ensureThread resolves null', async () => {
    const holder: { current: Conversation | null } = { current: null }
    await render(<Harness holder={holder} bindingKey={null} ensureThread={async () => null} />)

    await holder.current!.sendMessage('hi')

    expect(streamAssistantTurn).not.toHaveBeenCalled()
  })

  it('gives each binding key its own transcript', async () => {
    echoStream()
    const holder: { current: Conversation | null } = { current: null }
    const view = await render(<Harness holder={holder} bindingKey='file-1' ensureThread={async () => 'thread-a'} />)

    await holder.current!.sendMessage('hi')
    await expect.element(view.getByTestId('count')).toHaveTextContent('2')

    await view.rerender(<Harness holder={holder} bindingKey='file-2' ensureThread={async () => 'thread-b'} />)
    await expect.element(view.getByTestId('count')).toHaveTextContent('0')
  })

  it('restores a retained binding\'s transcript when the key changes back', async () => {
    echoStream()
    const retainGlobal = (key: string | null) => key === '__global__'
    const holder: { current: Conversation | null } = { current: null }
    const view = await render(
      <Harness
        holder={holder}
        bindingKey='__global__'
        ensureThread={async () => 'global-thread'}
        retainOnLeave={retainGlobal}
      />
    )

    await holder.current!.sendMessage('hi')
    await expect.element(view.getByTestId('count')).toHaveTextContent('2')

    await view.rerender(
      <Harness holder={holder} bindingKey='file-1' ensureThread={async () => 'thread-a'} retainOnLeave={retainGlobal} />
    )
    await expect.element(view.getByTestId('count')).toHaveTextContent('0')

    await view.rerender(
      <Harness
        holder={holder}
        bindingKey='__global__'
        ensureThread={async () => 'global-thread'}
        retainOnLeave={retainGlobal}
      />
    )
    await expect.element(view.getByTestId('count')).toHaveTextContent('2')
  })

  it('discards a non-retained binding\'s transcript when the key changes', async () => {
    echoStream()
    const holder: { current: Conversation | null } = { current: null }
    const view = await render(<Harness holder={holder} bindingKey='file-1' ensureThread={async () => 'thread-a'} />)

    await holder.current!.sendMessage('hi')
    await expect.element(view.getByTestId('count')).toHaveTextContent('2')

    await view.rerender(<Harness holder={holder} bindingKey='file-2' ensureThread={async () => 'thread-b'} />)
    await expect.element(view.getByTestId('count')).toHaveTextContent('0')

    await view.rerender(<Harness holder={holder} bindingKey='file-1' ensureThread={async () => 'thread-a'} />)
    await expect.element(view.getByTestId('count')).toHaveTextContent('0')
  })

  it('does not reset the transcript when only the context changes', async () => {
    echoStream()
    const holder: { current: Conversation | null } = { current: null }
    const view = await render(<Harness holder={holder} bindingKey='file-1' ensureThread={async () => 'thread-a'} />)

    await holder.current!.sendMessage('hi')
    await expect.element(view.getByTestId('count')).toHaveTextContent('2')

    await view.rerender(<Harness holder={holder} bindingKey='file-1' ensureThread={async () => 'thread-a'} />)
    await expect.element(view.getByTestId('count')).toHaveTextContent('2')
  })
})
