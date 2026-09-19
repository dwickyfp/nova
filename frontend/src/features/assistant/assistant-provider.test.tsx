import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { AssistantProvider, useAssistant } from './assistant-provider'

function Probe() {
  const { open, toggle, initialiseOpen, collapsedToPersist, setBinding, conversation } = useAssistant()
  return (
    <div>
      <span data-testid='open'>{String(open)}</span>
      <span data-testid='collapsed'>{String(collapsedToPersist())}</span>
      <span data-testid='thread'>{conversation.threadId ?? 'none'}</span>
      <button type='button' onClick={toggle}>
        toggle
      </button>
      <button type='button' onClick={() => initialiseOpen({ assistant_collapsed: false })}>
        init-open
      </button>
      <button
        type='button'
        onClick={() =>
          setBinding({
            key: 'file-1',
            context: { database: 'analytics', schema: null, role: null },
            ensureThread: async () => 'thread-a',
          })
        }
      >
        bind
      </button>
      <button type='button' onClick={() => setBinding(null)}>
        unbind
      </button>
    </div>
  )
}

function SendProbe() {
  const { conversation } = useAssistant()
  return (
    <div>
      <span data-testid='thread'>{conversation.threadId ?? 'none'}</span>
      <button type='button' onClick={() => void conversation.sendMessage('hi')}>
        send
      </button>
    </div>
  )
}

function renderProbe() {
  return render(
    <AssistantProvider>
      <Probe />
    </AssistantProvider>
  )
}

describe('AssistantProvider', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('owns the open state and toggles it', async () => {
    const { getByTestId, getByRole } = await renderProbe()
    await expect.element(getByTestId('open')).toHaveTextContent('false')

    await getByRole('button', { name: 'toggle' }).click()
    await expect.element(getByTestId('open')).toHaveTextContent('true')

    await getByRole('button', { name: 'toggle' }).click()
    await expect.element(getByTestId('open')).toHaveTextContent('false')
  })

  it('translates the open state into the persisted polarity', async () => {
    const { getByTestId, getByRole } = await renderProbe()
    await expect.element(getByTestId('collapsed')).toHaveTextContent('true')

    await getByRole('button', { name: 'toggle' }).click()
    await expect.element(getByTestId('collapsed')).toHaveTextContent('false')
  })

  it('applies the persisted value only once, so a later call cannot clobber a toggle', async () => {
    const { getByTestId, getByRole } = await renderProbe()

    await getByRole('button', { name: 'init-open' }).click()
    await expect.element(getByTestId('open')).toHaveTextContent('true')

    await getByRole('button', { name: 'toggle' }).click()
    await expect.element(getByTestId('open')).toHaveTextContent('false')

    await getByRole('button', { name: 'init-open' }).click()
    await expect.element(getByTestId('open')).toHaveTextContent('false')
  })

  it('sends through the registered binding thread and drops it on unbind', async () => {
    function BoundSend() {
      const { setBinding, conversation } = useAssistant()
      return (
        <div>
          <span data-testid='thread'>{conversation.threadId ?? 'none'}</span>
          <button
            type='button'
            onClick={() =>
              setBinding({
                key: 'file-1',
                context: { database: null, schema: null, role: null },
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

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('event: done\ndata: {"message_id":"m"}\n\n'))
            controller.close()
          },
        }),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
      )
    )

    const { getByTestId, getByRole } = await render(
      <AssistantProvider>
        <BoundSend />
      </AssistantProvider>
    )

    await getByRole('button', { name: 'bind', exact: true }).click()
    await getByRole('button', { name: 'send' }).click()
    await expect.element(getByTestId('thread')).toHaveTextContent('thread-a')

    await getByRole('button', { name: 'unbind', exact: true }).click()
    await getByRole('button', { name: 'send' }).click()
    // With no binding, ensureThread resolves null and the turn aborts.
    await expect.element(getByTestId('thread')).toHaveTextContent('none')
  })

  it('refuses to send with no binding instead of throwing', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const { getByTestId, getByRole } = await render(
      <AssistantProvider>
        <SendProbe />
      </AssistantProvider>
    )

    await getByRole('button', { name: 'send' }).click()
    await expect.element(getByTestId('thread')).toHaveTextContent('none')
    expect(fetchSpy).not.toHaveBeenCalled()
    fetchSpy.mockRestore()
  })
})
