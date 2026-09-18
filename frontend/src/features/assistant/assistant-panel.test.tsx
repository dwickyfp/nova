import { page } from 'vitest/browser'
import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { AssistantPanel } from './assistant-panel'

describe('AssistantPanel', () => {
  it('renders nothing inline when closed', async () => {
    const { container } = await render(
      <AssistantPanel open={false} onOpenChange={() => {}} />
    )
    expect(container.textContent).toBe('')
  })

  it('renders inline as a labelled aside on a wide viewport', async () => {
    await page.viewport(1440, 900)
    try {
      const { getByRole } = await render(
        <AssistantPanel open onOpenChange={() => {}} />
      )
      const panel = getByRole('complementary', { name: 'Assistant' })
      await expect.element(panel).toBeInTheDocument()
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('renders the shell with the empty state when open', async () => {
    const { getByText, getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} />
    )
    await expect.element(getByText('Ask about this workspace')).toBeInTheDocument()
    await expect
      .element(getByRole('button', { name: 'Close assistant' }))
      .toBeInTheDocument()
  })

  it('renders caller-supplied transcript content in place of the empty state', async () => {
    const { getByText, container } = await render(
      <AssistantPanel open onOpenChange={() => {}}>
        <p>SELECT 1</p>
      </AssistantPanel>
    )
    await expect.element(getByText('SELECT 1')).toBeInTheDocument()
    expect(container.textContent).not.toContain('Ask about this workspace')
  })

  it('submits a trimmed message and clears the composer', async () => {
    const onSendMessage = vi.fn()
    const { getByPlaceholder, getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} onSendMessage={onSendMessage} />
    )
    const field = getByPlaceholder('Ask a question or describe a query')
    await field.fill('  how many rows?  ')
    await getByRole('button', { name: 'Send message' }).click()

    expect(onSendMessage).toHaveBeenCalledWith('how many rows?')
    await expect.element(field).toHaveValue('')
  })

  it('does not send an empty message', async () => {
    const onSendMessage = vi.fn()
    const { getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} onSendMessage={onSendMessage} />
    )
    await getByRole('button', { name: 'Send message' }).click()
    expect(onSendMessage).not.toHaveBeenCalled()
  })

  it('closes via the header control', async () => {
    const onOpenChange = vi.fn()
    const { getByRole } = await render(
      <AssistantPanel open onOpenChange={onOpenChange} />
    )
    await getByRole('button', { name: 'Close assistant' }).click()
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('renders transcript messages instead of the empty state', async () => {
    const { getByText, container } = await render(
      <AssistantPanel
        open
        onOpenChange={() => {}}
        messages={[
          {
            message_id: 'm1',
            role: 'assistant',
            content: 'Revenue is grouped by region.',
            tool_call: null,
            created_at: '2026-09-18T00:00:00Z',
            turn_state: 'done',
          },
        ]}
      />
    )
    await expect.element(getByText('Revenue is grouped by region.')).toBeInTheDocument()
    expect(container.textContent).not.toContain('Ask about this workspace')
  })

  it('swaps Send for Stop while streaming and fires onStop', async () => {
    const onStop = vi.fn()
    const { getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} streaming onStop={onStop} />
    )
    await getByRole('button', { name: 'Stop generating' }).click()
    expect(onStop).toHaveBeenCalled()
  })

  it('states that the assistant backend is not connected instead of offering a dead send', async () => {
    const { getByText, getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} disabled />
    )
    await expect
      .element(getByText('The assistant backend is not connected yet. This panel is read-only until it is.'))
      .toBeInTheDocument()
    await expect.element(getByRole('button', { name: 'Send message' })).toBeDisabled()
  })
})
