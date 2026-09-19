import { page } from 'vitest/browser'
import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { AssistantPanel } from './assistant-panel'
import { AssistantToggle } from './assistant-toggle'

describe('AssistantToggle', () => {
  it('shows the panel affordance and reports its state when closed', async () => {
    const onToggle = vi.fn()
    const { getByRole } = await render(<AssistantToggle open={false} onToggle={onToggle} />)

    const button = getByRole('button', { name: 'Show assistant' })
    await expect.element(button).toBeInTheDocument()
    expect(button.element().getAttribute('aria-pressed')).toBe('false')
    expect(button.element().getAttribute('aria-expanded')).toBe('false')
    expect(button.element().getAttribute('aria-controls')).toBe('assistant-panel')
  })

  it('swaps to the hide affordance when open and fires onToggle', async () => {
    const onToggle = vi.fn()
    const { getByRole } = await render(<AssistantToggle open onToggle={onToggle} />)

    const button = getByRole('button', { name: 'Hide assistant' })
    expect(button.element().getAttribute('aria-pressed')).toBe('true')
    await button.click()
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('carries the 44px minimum hit target classes', async () => {
    const { getByRole } = await render(<AssistantToggle open={false} onToggle={() => {}} />)
    // The browser test runner does not load the Tailwind stylesheet, so the
    // measured box is the UA default. Assert the sizing contract instead.
    const classes = getByRole('button', { name: 'Show assistant' }).element().className
    expect(classes).toContain('min-h-11')
    expect(classes).toContain('min-w-11')
  })

  it('guards its own transition against reduced motion', async () => {
    const { getByRole } = await render(<AssistantToggle open={false} onToggle={() => {}} />)
    const classes = getByRole('button', { name: 'Show assistant' }).element().className
    expect(classes).toContain('motion-reduce:transition-none')
  })
})

describe('AssistantPanel motion guard', () => {
  it('keeps the reduced-motion variant on the animated wrapper', async () => {
    await page.viewport(1440, 900)
    try {
      const { container } = await render(<AssistantPanel open onOpenChange={() => {}} />)
      const wrapper = container.querySelector('#assistant-panel')?.parentElement
      expect(wrapper?.className).toContain('transition-[width]')
      expect(wrapper?.className).toContain('motion-reduce:transition-none')
    } finally {
      await page.viewport(375, 800)
    }
  })
})

describe('AssistantPanel narrow ladder', () => {
  it('renders the inline panel from md (768px) up', async () => {
    await page.viewport(768, 900)
    try {
      const { getByRole } = await render(<AssistantPanel open onOpenChange={() => {}} />)
      await expect.element(getByRole('complementary', { name: 'Assistant' })).toBeInTheDocument()
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('renders the Sheet just below md (767px)', async () => {
    await page.viewport(767, 900)
    try {
      const { getByRole, container } = await render(<AssistantPanel open onOpenChange={() => {}} />)
      await expect.element(getByRole('dialog')).toBeInTheDocument()
      expect(container.querySelector('#assistant-panel')).toBeNull()
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('keeps the panel out of the tab order while closed on a wide viewport', async () => {
    await page.viewport(1440, 900)
    try {
      const { container } = await render(<AssistantPanel open={false} onOpenChange={() => {}} />)
      const aside = container.querySelector('#assistant-panel')
      expect(aside).not.toBeNull()
      expect(aside?.hasAttribute('inert')).toBe(true)
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('removes inert and exposes the panel once open on a wide viewport', async () => {
    await page.viewport(1440, 900)
    try {
      const { container } = await render(<AssistantPanel open onOpenChange={() => {}} />)
      const aside = container.querySelector('#assistant-panel')
      expect(aside?.hasAttribute('inert')).toBe(false)
    } finally {
      await page.viewport(375, 800)
    }
  })
})
