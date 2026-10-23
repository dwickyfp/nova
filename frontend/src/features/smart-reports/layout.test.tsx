import { afterEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import { SmartReportRenderer } from './renderer'
import { ReportFilter } from './filters'
import { reportSpecSchema, viewStateSchema, type Filter } from './contracts'
import fixture from './fixtures/basic.json'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const spec = reportSpecSchema.parse(fixture.spec)
const state = viewStateSchema.parse(fixture.state)
afterEach(async () => {
  document.documentElement.classList.remove('dark')
  await page.viewport(1280, 800)
})

describe('Report responsive exploration', () => {
  for (const width of [320, 390, 768, 1280]) for (const theme of ['light', 'dark']) {
    it(`keeps reading order and controls inside ${width}px in ${theme}`, async () => {
      await page.viewport(width, 800)
      document.documentElement.classList.toggle('dark', theme === 'dark')
      const screen = await render(<div className="smart-report w-full min-w-0 p-4"><SmartReportRenderer spec={{ ...spec, filters: [] }} state={state} onEvent={vi.fn()} /></div>)
      await expect.element(screen.getByRole('heading', { name: 'Revenue', exact: true })).toBeVisible()
      const widgets = Array.from(screen.container.querySelectorAll<HTMLElement>('.report-widget'))
      expect(widgets.map(widget => widget.querySelector('h4')?.textContent)).toEqual(['Revenue', 'Cities'])
      for (const widget of widgets) {
        const bounds = widget.getBoundingClientRect()
        expect(bounds.left).toBeGreaterThanOrEqual(0)
        expect(bounds.right).toBeLessThanOrEqual(width)
        expect(widget.scrollWidth).toBeLessThanOrEqual(widget.clientWidth + 1)
      }
      expect(screen.container.scrollWidth).toBeLessThanOrEqual(width)
    })
  }

  it('submits exact decimal bounds without converting through a JavaScript number', async () => {
    const filter: Filter = { ...spec.filters[0], kind: 'range', label: 'Amount', default: { kind: 'range', minimum: '9007199254740993.12', maximum: null, values: [], relative: null } }
    const onEvent = vi.fn()
    const client = new QueryClient()
    const screen = await render(<QueryClientProvider client={client}><ReportFilter filter={filter} state={{ ...state, filters: {} }} disabled={false} onEvent={onEvent} /></QueryClientProvider>)
    await screen.getByRole('button', { name: 'Apply range' }).click()
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'FILTER_SET', value: expect.objectContaining({ minimum: '9007199254740993.12' }) }))
  })
})
