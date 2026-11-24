import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { page as browser, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import type { DashboardDefinition } from '../contracts'
import type { Command } from './commands'
import { ItemPanel } from './item-panel'
import { LEGACY_GRID, type Element, type Page } from './layout'
import { applyLocal } from './operations'
import '@/styles/index.css'

// The Format and layers panel: page background, formatting, layers and tab order, driven as a person would.

const FLAGS = { locked: false, hidden: false, decorative: false, style: {} }
const box = (x: number, y: number, w = 200, h = 100) => ({ x, y, w, h, rotation: 0 as const })
const text = (id: string, x: number, y: number, words: string): Element =>
  ({ kind: 'text', element_id: id, name: '', ...FLAGS, geometry: box(x, y), role: 'body', text: { runs: [{ kind: 'text', text: words }] } }) as Element
const shape = (id: string, x: number, y: number): Element =>
  ({ kind: 'shape', element_id: id, name: 'Card', ...FLAGS, decorative: true, geometry: box(x, y), shape: 'rect', style: { fill: { kind: 'solid', token: 'card', opacity: 100 } } }) as unknown as Element
const icon = (id: string): Element =>
  ({ kind: 'icon', element_id: id, name: 'Truck', ...FLAGS, decorative: true, geometry: box(500, 0, 48, 48), icon: 'truck', label: '' }) as unknown as Element
const start = (elements: Element[]): DashboardDefinition => ({
  schema_version: 3, dashboard_id: 'd', title: 'D', description: '', datasets: [], filters: [], actions: [],
  pages: [{ page_id: 'p', title: 'P', elements, tab_order: [], mobile: { order: [], hidden: [] }, legacy_spacers: [],
    canvas: { size: 'custom', width: 1200, height: 720, fit: 'width', background: null, grid: { ...LEGACY_GRID, show: true, snap: true, snap_to_elements: true } } }],
})

const ran: Command[] = []

/** The panel with changes applied as the editor applies them. */
function Harness({ initial, selected = [] }: { initial: DashboardDefinition; selected?: string[] }) {
  const [definition, setDefinition] = useState(initial)
  const [selection, setSelection] = useState(selected)
  return <div style={{ width: 320, height: 700, display: 'flex' }}>
    <ItemPanel page={definition.pages[0] as Page} selection={selection} onSelect={setSelection} disabled={false} onClose={() => undefined}
      run={command => {
        ran.push(command)
        if (command.changes) setDefinition(current => command.changes!.reduce(applyLocal, current))
      }} />
  </div>
}

const last = () => ran[ran.length - 1]
afterEach(async () => { await cleanup(); ran.length = 0; vi.unstubAllGlobals() })

describe('Format and layers panel', () => {
  it('sets a gradient page background when nothing is selected', async () => {
    const screen = await render(<Harness initial={start([text('a', 0, 0, 'Revenue')])} />)
    await expect.element(screen.getByText('Nothing selected', { exact: false })).toBeVisible()
    await screen.getByLabelText('Page background').click()
    await browser.getByRole('option', { name: 'Gradient' }).click()
    await expect.poll(() => last()?.message).toBe('Page background changed')
    expect(last().changes![0]).toMatchObject({ op: 'page.canvas.set', canvas: { background: { kind: 'gradient', start: 'background', end: 'muted', angle: 180 } } })
    // The gradient's own controls appear, and a direction change saves.
    await screen.getByLabelText('Direction').click()
    await browser.getByRole('option', { name: 'Left to right' }).click()
    await expect.poll(() => (last().changes![0] as { canvas: { background: { angle: number } } }).canvas.background.angle).toBe(90)
  })

  it('formats a selected shape: fill, shadow and an opacity it checks', async () => {
    const screen = await render(<Harness initial={start([shape('s', 0, 0)])} selected={['s']} />)
    await expect.element(screen.getByText('Shape selected')).toBeVisible()
    await screen.getByLabelText('Fill').click()
    await browser.getByRole('option', { name: 'Chart 2' }).click()
    await expect.poll(() => last()?.message).toBe('Fill changed')
    expect(last().changes![0]).toMatchObject({ op: 'elements.update', updates: [{ element_id: 's', set: { style: { fill: { kind: 'solid', token: 'chart-2' } } } }] })
    await screen.getByLabelText('Shadow').click()
    await browser.getByRole('option', { name: 'Raised' }).click()
    await expect.poll(() => last()?.message).toBe('Shadow changed')
    // The fill chosen before is kept: the change merges.
    expect(last().changes![0]).toMatchObject({ updates: [{ set: { style: { fill: { token: 'chart-2' }, shadow: 'md' } } }] })
    // The outline colour has its own name.
    await screen.getByLabelText('Outline colour').click()
    await browser.getByRole('option', { name: 'Brand' }).click()
    await expect.poll(() => last()?.message).toBe('Outline changed')
    expect(last().changes![0]).toMatchObject({ updates: [{ set: { style: { stroke: { token: 'brand', width: 1 } } } }] })
    await screen.getByLabelText('Opacity (%)').fill('140')
    await userEvent.keyboard('{Enter}')
    await expect.poll(() => last()?.message).toBe('Enter a whole number from 0 to 100 for opacity')
    expect(last().changes).toBeNull()
    await screen.getByLabelText('Opacity (%)').fill('60')
    await userEvent.keyboard('{Enter}')
    await expect.poll(() => last()?.message).toBe('Opacity set to 60%')
  })

  it('changes a text item\'s style', async () => {
    const screen = await render(<Harness initial={start([text('a', 0, 0, 'Revenue')])} selected={['a']} />)
    await screen.getByLabelText('Text style').click()
    await browser.getByRole('option', { name: 'Heading' }).click()
    await expect.poll(() => last()?.message).toBe('Text style set to heading')
    expect(last().changes![0]).toMatchObject({ updates: [{ element_id: 'a', set: { role: 'heading' } }] })
    await screen.getByLabelText('Padding').click()
    await browser.getByRole('option', { name: '4 px', exact: true }).click()
    await expect.poll(() => last()?.message).toBe('Padding set to 4 px')
    expect(last().changes![0]).toMatchObject({ updates: [{ element_id: 'a', set: { style: { padding: 4 } } }] })
  })

  it('labels a decorative icon, which makes screen readers read it', async () => {
    const screen = await render(<Harness initial={start([icon('i')])} selected={['i']} />)
    await expect.element(screen.getByRole('switch', { name: 'Decorative' })).toBeChecked()
    await screen.getByLabelText('Label for screen readers').fill('Deliveries')
    await userEvent.keyboard('{Enter}')
    await expect.poll(() => last()?.message).toBe('Label set to Deliveries; screen readers now read this icon')
    expect(last().changes![0]).toMatchObject({ updates: [{ element_id: 'i', set: { label: 'Deliveries', decorative: false } }] })
    await expect.element(screen.getByRole('switch', { name: 'Decorative' })).not.toBeChecked()
  })

  it('lists layers front to back and moves one with the buttons and with Alt+arrows', async () => {
    const screen = await render(<Harness initial={start([text('a', 0, 0, 'Revenue'), text('b', 300, 0, 'Orders'), shape('s', 0, 200)])} />)
    await screen.getByRole('tab', { name: 'Layers' }).click()
    // Each row's name button (the one that selects; pressed when selected).
    const names = () => [...screen.getByRole('list', { name: 'Layers, front to back' }).element().querySelectorAll('li > button[aria-pressed]')]
      .map(node => node.textContent)
    await expect.poll(names).toEqual(['Shape Card', 'Text Orders', 'Text Revenue'])
    await screen.getByRole('button', { name: 'Bring Revenue forward' }).click()
    await expect.poll(() => last()?.message).toBe('Revenue is now 2 of 3 from the front')
    await expect.poll(names).toEqual(['Shape Card', 'Text Revenue', 'Text Orders'])
    // The keyboard: focus a name, Alt+Up moves it up one.
    await screen.getByRole('button', { name: 'Text Revenue' }).click()
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}')
    await expect.poll(() => last()?.message).toBe('Revenue is now 1 of 3 from the front')
    await expect.poll(names).toEqual(['Text Revenue', 'Shape Card', 'Text Orders'])
    // Hide and lock from the row.
    await screen.getByRole('button', { name: 'Hide Orders' }).click()
    await expect.poll(() => last()?.message).toBe('Hid 1 item')
    await expect.element(screen.getByRole('button', { name: 'Show Orders' })).toBeVisible()
    await screen.getByRole('button', { name: 'Lock Card' }).click()
    await expect.element(screen.getByRole('button', { name: 'Unlock Card' })).toBeVisible()
  })

  it('sets the tab order, keeps decorative items out, and can follow the page again', async () => {
    const screen = await render(<Harness initial={start([text('a', 0, 0, 'Revenue'), text('b', 300, 0, 'Orders'), shape('s', 0, 200)])} />)
    await screen.getByRole('tab', { name: 'Tab order' }).click()
    const list = () => screen.getByRole('list', { name: 'Tab order' }).element().textContent
    await expect.poll(list).toBe('1Revenue2Orders')
    await screen.getByRole('button', { name: 'Move Orders earlier' }).click()
    await expect.poll(() => last()?.message).toBe('Orders is now 1 of 2 in the tab order')
    await expect.poll(list).toBe('1Orders2Revenue')
    await screen.getByRole('button', { name: 'Follow the page' }).click()
    await expect.poll(() => last()?.message).toBe('The tab order follows the page again')
    await expect.poll(list).toBe('1Revenue2Orders')
  })
})
