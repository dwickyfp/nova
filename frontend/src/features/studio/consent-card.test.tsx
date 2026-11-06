import { expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { ConsentCard } from './consent-card'

it('offers a conversation grant only for read-only tools', async () => {
  const decide = vi.fn().mockResolvedValue(undefined)
  const screen = await render(<ConsentCard call={{ tool_name: 'schedule_automation', sql_preview: 'Schedule a weekly summary', classification: 'session_change', status: 'pending' }} onDecide={decide} />)
  expect(screen.getByRole('button', { name: 'Allow for this chat' }).query()).toBeNull()
  await screen.getByRole('button', { name: 'Allow once' }).click()
  expect(decide).toHaveBeenCalledExactlyOnceWith('allow_once')
})

it('keeps conversation approval available for reads', async () => {
  const decide = vi.fn().mockResolvedValue(undefined)
  const screen = await render(<ConsentCard call={{ tool_name: 'semantic_query', sql_preview: 'Read revenue', classification: 'read_only', status: 'pending' }} onDecide={decide} />)
  await screen.getByRole('button', { name: 'Allow for this chat' }).click()
  expect(decide).toHaveBeenCalledExactlyOnceWith('allow_session')
})

it('shows a statement as code and a tool sentence as text', async () => {
  const decide = vi.fn().mockResolvedValue(undefined)
  const prose = await render(<ConsentCard call={{ tool_name: 'knowledge_propose', sql_preview: 'Propose a change to a business definition for steward review', classification: 'session_change', status: 'pending' }} onDecide={decide} />)
  const sentence = prose.getByText('Propose a change to a business definition for steward review').element()
  expect(sentence.closest('pre')).toBeNull()
  await prose.unmount()
  const sql = await render(<ConsentCard call={{ tool_name: 'query_mutate', sql_preview: 'DELETE FROM sales.orders WHERE id = 1', classification: 'destructive', status: 'pending' }} onDecide={decide} />)
  expect(sql.getByText('DELETE FROM sales.orders WHERE id = 1').element().closest('pre')).not.toBeNull()
})
