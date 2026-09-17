import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { LoadingLines, LoadingOverlay, RefreshBanner } from './loading-overlay'

describe('LoadingOverlay', () => {
  it('announces itself as a live status region', async () => {
    const { container } = await render(<LoadingOverlay />)
    const root = container.querySelector('[data-slot="loading-overlay"]')
    expect(root?.getAttribute('role')).toBe('status')
    expect(root?.getAttribute('aria-live')).toBe('polite')
  })

  it('renders a caller-supplied label', async () => {
    const { getByText } = await render(<LoadingOverlay label='Loading models' />)
    await expect.element(getByText('Loading models')).toBeInTheDocument()
  })

  it('builds its placeholders from the shared Skeleton primitive', async () => {
    const { container } = await render(<LoadingOverlay />)
    const skeletons = container.querySelectorAll('[data-slot="skeleton"]')
    expect(skeletons.length).toBeGreaterThan(0)
  })
})

describe('LoadingLines', () => {
  it('renders the requested number of rows', async () => {
    const { container } = await render(<LoadingLines rows={3} />)
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(3)
  })

  it('exposes a text alternative for screen readers', async () => {
    const { getByText } = await render(<LoadingLines />)
    await expect.element(getByText('Loading')).toBeInTheDocument()
  })
})

describe('RefreshBanner', () => {
  it('reports the refresh without stealing focus', async () => {
    const { container } = await render(<RefreshBanner label='Refreshing users...' />)
    const root = container.querySelector('[data-slot="refresh-banner"]')
    expect(root?.className).toContain('pointer-events-none')
    expect(root?.getAttribute('role')).toBe('status')
  })

  it('renders the refresh label', async () => {
    const { getByText } = await render(<RefreshBanner label='Refreshing roles...' />)
    await expect.element(getByText('Refreshing roles...')).toBeInTheDocument()
  })

  it('respects reduced motion on the progress bar', async () => {
    const { container } = await render(<RefreshBanner label='Refreshing...' />)
    const bar = container.querySelector('.animate-pulse')
    expect(bar?.className).toContain('motion-reduce:animate-none')
  })
})
