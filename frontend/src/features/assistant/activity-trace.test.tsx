import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { ActivityTrace } from './activity-trace'
import type { TranscriptMessage } from './use-assistant-transcript'

function activityMessage(overrides: Partial<TranscriptMessage> = {}): TranscriptMessage {
  return {
    message_id: 'a1',
    role: 'activity',
    content: '',
    tool_call: null,
    created_at: '2026-09-19T00:00:00Z',
    activity_steps: [],
    activity_plan: [],
    ...overrides,
  }
}

describe('ActivityTrace', () => {
  it('renders nothing when there is no plan or activity', async () => {
    const { container } = await render(<ActivityTrace message={activityMessage()} />)
    expect(container.textContent).toBe('')
  })

  it('collapses a settled trace behind a summary row', async () => {
    const { getByRole, container } = await render(
      <ActivityTrace
        message={activityMessage({
          activity_plan: [{ id: 'understand', text: 'Understand', status: 'done' }],
          activity_steps: [
            { key: 's1', phase: 'skill', text: 'Loading skill: create-table', status: 'done' },
          ],
        })}
      />
    )
    const toggle = getByRole('button')
    await expect.element(toggle).toHaveAttribute('aria-expanded', 'false')
    // The detailed lines are hidden until expanded.
    expect(container.textContent).toContain('Thought for 1 step')
    expect(container.textContent).not.toContain('Loading skill: create-table')
  })

  it('expands on click to show the plan and steps', async () => {
    const { getByRole, getByText } = await render(
      <ActivityTrace
        message={activityMessage({
          activity_plan: [{ id: 'understand', text: 'Understand the request', status: 'done' }],
          activity_steps: [
            { key: 's1', phase: 'skill', text: 'Loading skill: create-table', status: 'done' },
          ],
        })}
      />
    )
    await getByRole('button').click()
    await expect.element(getByRole('button')).toHaveAttribute('aria-expanded', 'true')
    await expect.element(getByText('Understand the request')).toBeInTheDocument()
    await expect.element(getByText('Loading skill: create-table')).toBeInTheDocument()
  })

  it('opens itself while the turn is running', async () => {
    const { getByRole, getByText } = await render(
      <ActivityTrace
        message={activityMessage({
          activity_steps: [
            { key: 's1', phase: 'act', text: 'Reasoning about the next step', status: 'running' },
          ],
        })}
      />
    )
    await expect.element(getByRole('button')).toHaveAttribute('aria-expanded', 'true')
    await expect.element(getByText('Working')).toBeInTheDocument()
    await expect.element(getByText('Reasoning about the next step')).toBeInTheDocument()
  })
})
