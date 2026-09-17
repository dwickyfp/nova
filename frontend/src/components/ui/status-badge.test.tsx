import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { StatusBadge } from './status-badge'

describe('StatusBadge', () => {
  it('renders its label', async () => {
    const { getByText } = await render(<StatusBadge>Running</StatusBadge>)
    await expect.element(getByText('Running')).toBeInTheDocument()
  })

  it('uses semantic tokens per tone, never a raw palette class', async () => {
    const { container } = await render(
      <StatusBadge tone='success'>Loaded</StatusBadge>
    )
    const badge = container.querySelector('[data-slot="status-badge"]')
    expect(badge?.className).toContain('success')
    expect(badge?.className).not.toMatch(
      /(emerald|teal|green|sky|amber)-\d{2,3}/
    )
  })

  it('omits the dot by default and shows it when asked', async () => {
    const plain = await render(<StatusBadge tone='info'>Queued</StatusBadge>)
    expect(
      plain.container.querySelector('[data-slot="status-badge-dot"]')
    ).toBeNull()

    const dotted = await render(
      <StatusBadge tone='info' dot>
        Queued
      </StatusBadge>
    )
    expect(
      dotted.container.querySelector('[data-slot="status-badge-dot"]')
    ).not.toBeNull()
  })

  it('marks a decorative dot as hidden from assistive tech', async () => {
    const { container } = await render(
      <StatusBadge tone='success' dot>
        Active
      </StatusBadge>
    )
    const dot = container.querySelector('[data-slot="status-badge-dot"]')
    expect(dot?.getAttribute('aria-hidden')).toBe('true')
  })
})
