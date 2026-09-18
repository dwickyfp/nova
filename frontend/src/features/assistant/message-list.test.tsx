import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { MessageList } from './message-list'
import type { TranscriptMessage } from './use-assistant-transcript'

const userMessage: TranscriptMessage = {
  message_id: 'u1',
  role: 'user',
  content: 'how many rows?',
  tool_call: null,
  created_at: '2026-09-18T00:00:00Z',
}

describe('MessageList', () => {
  it('announces status transitions through a single polite live region', async () => {
    const { getByRole } = await render(
      <MessageList messages={[userMessage]} statusMessage='Running query' />
    )
    await expect.element(getByRole('status')).toHaveTextContent('Running query')
  })

  it('renders plain text without interpreting markup', async () => {
    const { getByText, container } = await render(
      <MessageList
        messages={[
          {
            message_id: 'a1',
            role: 'assistant',
            content: '<script>alert(1)</script>',
            tool_call: null,
            created_at: '2026-09-18T00:00:00Z',
          },
        ]}
      />
    )
    await expect.element(getByText('<script>alert(1)</script>')).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
  })

  it('marks a cancelled partial answer instead of hiding it', async () => {
    const { getByText } = await render(
      <MessageList
        messages={[
          {
            message_id: 'a1',
            role: 'assistant',
            content: 'Partial ans',
            tool_call: null,
            created_at: '2026-09-18T00:00:00Z',
            turn_state: 'cancelled',
          },
        ]}
      />
    )
    await expect.element(getByText('Partial ans')).toBeInTheDocument()
    await expect.element(getByText('Stopped before the answer finished.')).toBeInTheDocument()
  })
})
