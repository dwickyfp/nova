import { page } from 'vitest/browser'
import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { AssistantProvider } from './assistant-provider'
import { AssistantDock } from './assistant-dock'

/**
 * Mirrors the layout's structure: the provider wraps the route slot, so a route
 * change swaps the slot contents without remounting the assistant. Proves the
 * open state survives a navigation and that the toggle is the real control.
 */
function LayoutHarness({ route }: { route: string }) {
  return (
    <AssistantProvider>
      <div className='flex h-svh'>
        <div data-testid='route'>{route}</div>
        <AssistantDock />
      </div>
    </AssistantProvider>
  )
}

describe('AssistantDock', () => {
  it('opens and closes the panel through the persistent toggle', async () => {
    await page.viewport(1440, 900)
    try {
      const { getByRole, container } = await render(<LayoutHarness route='dashboard' />)

      const show = getByRole('button', { name: 'Show assistant' })
      await expect.element(show).toBeInTheDocument()

      await show.click()
      await expect.element(getByRole('button', { name: 'Hide assistant' })).toBeInTheDocument()

      const panel = container.querySelector('#assistant-panel')
      expect(panel?.hasAttribute('inert')).toBe(false)

      await getByRole('button', { name: 'Hide assistant' }).click()
      await expect.element(getByRole('button', { name: 'Show assistant' })).toBeInTheDocument()
      expect(panel?.hasAttribute('inert')).toBe(true)
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('keeps the open state across a route change', async () => {
    await page.viewport(1440, 900)
    try {
      const { getByRole, rerender } = await render(<LayoutHarness route='dashboard' />)

      await getByRole('button', { name: 'Show assistant' }).click()
      await expect.element(getByRole('button', { name: 'Hide assistant' })).toBeInTheDocument()

      await rerender(<LayoutHarness route='database-explorer' />)

      await expect.element(getByRole('button', { name: 'Hide assistant' })).toBeInTheDocument()
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('opens the narrow Sheet from the same FAB below md', async () => {
    await page.viewport(600, 800)
    try {
      const { getByRole } = await render(<LayoutHarness route='dashboard' />)

      await getByRole('button', { name: 'Show assistant' }).click()

      // The Sheet dialog replaces the inline panel below md; the FAB stays as
      // the trigger and the dialog carries its own labelled close control.
      await expect.element(getByRole('dialog')).toBeInTheDocument()
    } finally {
      await page.viewport(375, 800)
    }
  })
})
