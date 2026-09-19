import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { MessageList } from './message-list'
import type { AttachedQuery } from './query-attach'
import type { TranscriptMessage } from './use-assistant-transcript'

const userMessage: TranscriptMessage = {
  message_id: 'u1',
  role: 'user',
  content: 'how many rows?',
  tool_call: null,
  created_at: '2026-09-18T00:00:00Z',
}

const attachment: AttachedQuery = {
  id: 'att-1',
  sql: 'SELECT * FROM NOVA_ANALYTICS.default.channel_performance',
  tabId: 'tab-1',
  fileName: 'Untitled-2.sql',
  database: 'NOVA_ANALYTICS',
  schema: 'default',
  role: 'ACCOUNTADMIN',
  startLine: 1,
  endLine: 2,
  createdAt: 0,
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

  it('renders an assistant answer as markdown', async () => {
    const { getByRole, container } = await render(
      <MessageList
        messages={[
          {
            message_id: 'a1',
            role: 'assistant',
            content: '## Result\n\n| region | total |\n| --- | --- |\n| EU | 10 |',
            tool_call: null,
            created_at: '2026-09-18T00:00:00Z',
            turn_state: 'done',
          },
        ]}
      />
    )
    await expect.element(getByRole('heading', { name: 'Result' })).toBeInTheDocument()
    expect(container.querySelector('table')).not.toBeNull()
  })

  it('shows a streaming cursor while the answer is still arriving', async () => {
    const { container } = await render(
      <MessageList
        messages={[
          {
            message_id: 'a1',
            role: 'assistant',
            content: 'Partial',
            tool_call: null,
            created_at: '2026-09-18T00:00:00Z',
            turn_state: 'streaming',
          },
        ]}
      />
    )
    expect(container.querySelector('.animate-pulse')).not.toBeNull()
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

  it('renders a sent attachment as a card and hides the serialized preamble', async () => {
    const { getByText, container } = await render(
      <MessageList
        messages={[
          {
            ...userMessage,
            content: '[Attached query (file: Untitled-2.sql)]\n```sql\nSELECT * FROM t\n```\n\n---\n\nchange this to a cte',
            display_text: 'change this to a cte',
            attachments: [attachment],
          },
        ]}
      />
    )
    await expect.element(getByText('Untitled-2.sql')).toBeInTheDocument()
    await expect.element(getByText('change this to a cte')).toBeInTheDocument()
    expect(container.textContent).not.toContain('[Attached query')
    expect(container.textContent).not.toContain('```sql')
  })

  it('falls back to the wire text when a restored message has no attachment metadata', async () => {
    const { getByText } = await render(
      <MessageList
        messages={[
          {
            ...userMessage,
            content: 'plain restored message',
          },
        ]}
      />
    )
    await expect.element(getByText('plain restored message')).toBeInTheDocument()
  })
})
