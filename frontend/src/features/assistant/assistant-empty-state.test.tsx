import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import {
  ASSISTANT_TOUR_PROMPT,
  AssistantEmptyState,
} from './assistant-empty-state'
import type { ThreadView } from './thread-client'

function thread(overrides: Partial<ThreadView> = {}): ThreadView {
  return {
    thread_id: 't1',
    title: 'Working with Custom AI Functions',
    workspace_file_id: null,
    created_at: '2026-09-19T00:00:00Z',
    updated_at: new Date().toISOString(),
    message_count: 3,
    ...overrides,
  }
}

describe('AssistantEmptyState', () => {
  it('greets the user by name and asks how it can help', async () => {
    const { getByText } = await render(<AssistantEmptyState userName='Dwicky' />)
    await expect.element(getByText('Hi Dwicky,')).toBeInTheDocument()
    await expect.element(getByText('How can I help?')).toBeInTheDocument()
  })

  it('falls back to a neutral salutation without a name', async () => {
    const { getByText } = await render(<AssistantEmptyState userName={null} />)
    await expect.element(getByText('Hi there,')).toBeInTheDocument()
  })

  it('sends the tour prompt from the starter action', async () => {
    const onSendMessage = vi.fn()
    const { getByRole } = await render(
      <AssistantEmptyState onSendMessage={onSendMessage} />
    )
    await getByRole('button', { name: ASSISTANT_TOUR_PROMPT }).click()
    expect(onSendMessage).toHaveBeenCalledWith(ASSISTANT_TOUR_PROMPT)
  })

  it('omits the starter action when the panel cannot send', async () => {
    const { container } = await render(<AssistantEmptyState />)
    expect(
      container.querySelector(`button[type="button"]`)
    ).toBeNull()
  })

  it('lists recent chats and opens one on click', async () => {
    const onOpenThread = vi.fn()
    const older = thread({
      thread_id: 't2',
      title: 'Set up an automation',
      updated_at: '2026-09-01T00:00:00Z',
    })
    const { getByText } = await render(
      <AssistantEmptyState
        recentThreads={[thread(), older]}
        onOpenThread={onOpenThread}
      />
    )
    await expect.element(getByText('Recent chats')).toBeInTheDocument()
    await getByText('Set up an automation').click()
    expect(onOpenThread).toHaveBeenCalledWith('t2')
  })

  it('hides the recent list when there is nothing to show', async () => {
    const { container } = await render(<AssistantEmptyState recentThreads={[]} />)
    expect(container.textContent).not.toContain('Recent chats')
  })

  it('renders recent chats as inert when no open handler is given', async () => {
    const { getByRole } = await render(
      <AssistantEmptyState recentThreads={[thread()]} />
    )
    await expect
      .element(getByRole('button', { name: /Working with Custom AI Functions/ }))
      .toBeDisabled()
  })
})
