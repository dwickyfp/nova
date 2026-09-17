import { AlertTriangle, Activity } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { MetricCard } from './metric-card'

describe('MetricCard', () => {
  it('renders the label and value', async () => {
    const { getByText } = await render(
      <MetricCard label='Total Queries' value='12,480' />
    )
    await expect.element(getByText('Total Queries')).toBeInTheDocument()
    await expect.element(getByText('12,480')).toBeInTheDocument()
  })

  it('gives a primary metric a larger presence than a compact one', async () => {
    const { container } = await render(
      <>
        <MetricCard weight='primary' label='Errors' value='3' />
        <MetricCard weight='compact' label='Connections' value='12' />
      </>
    )
    const cards = container.querySelectorAll('[data-slot="metric-card"]')
    expect(cards[0]?.getAttribute('data-weight')).toBe('primary')
    expect(cards[1]?.getAttribute('data-weight')).toBe('compact')
    expect(cards[0]?.className).toContain('min-h-[128px]')
    expect(cards[1]?.className).toContain('min-h-[84px]')
  })

  it('uses a semantic tone for the icon, not the value', async () => {
    const { container } = await render(
      <MetricCard
        label='Error Count'
        value='7'
        icon={AlertTriangle}
        tone='danger'
      />
    )
    const icon = container.querySelector('[aria-hidden="true"]')
    expect(icon?.className).toContain('destructive')
    const value = container.querySelector('p.text-foreground')
    expect(value?.className).not.toMatch(/destructive|danger/)
  })

  it('renders a comparison hint when given', async () => {
    const { getByText } = await render(
      <MetricCard
        label='Success Rate'
        value='99.2%'
        icon={Activity}
        tone='success'
        hint='vs previous 24h'
      />
    )
    await expect.element(getByText('vs previous 24h')).toBeInTheDocument()
  })
})
