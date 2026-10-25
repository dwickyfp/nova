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
