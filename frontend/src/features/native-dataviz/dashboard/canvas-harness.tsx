/* eslint-disable react-refresh/only-export-components -- a test harness shared by test files; it is never hot-reloaded */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { afterEach, beforeEach, expect, vi } from 'vitest'
import { page as browser } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import type { DashboardDefinition, ElementPlacement } from '../contracts'
import { DashboardCanvas } from './canvas'
import { LEGACY_GRID, type Element, type Page } from './layout'
import { applyLocal } from './operations'
import '@/styles/index.css'

// A canvas with selection and moves kept as the editor keeps them, for browser tests of authoring and of the drag budget.

export const id = (value: number) => `bbbbbbbb-0000-4000-8000-${String(value).padStart(12, '0')}`
export const PAGE = id(1)
export const FLAGS = { locked: false, hidden: false, decorative: false }
export const box = (x: number, y: number, w: number, h: number) => ({ x, y, w, h, rotation: 0 as const })
export const text = (n: number, x: number, y: number, w = 188, h = 60): Element =>
  ({ kind: 'text', element_id: id(100 + n), name: `Note ${n}`, ...FLAGS, geometry: box(x, y, w, h), role: 'body', text: { runs: [{ kind: 'text', text: `Note ${n}` }] } }) as Element
export const shape = (n: number, x: number, y: number): Element =>
  ({ kind: 'shape', element_id: id(100 + n), name: `Shape ${n}`, ...FLAGS, geometry: box(x, y, 88, 60), shape: 'rect', style: { fill: { kind: 'solid', token: 'muted' } } }) as unknown as Element
export const icon = (n: number, x: number, y: number): Element =>
  ({ kind: 'icon', element_id: id(100 + n), name: `Icon ${n}`, ...FLAGS, geometry: box(x, y, 40, 40), icon: 'star' }) as unknown as Element
const page = (elements: Element[]): Page => ({
  page_id: PAGE, title: 'Canvas', elements, tab_order: [], mobile: { order: [], hidden: [] }, legacy_spacers: [],
  canvas: { size: 'custom', width: 1200, height: 720, fit: 'width', grid: { ...LEGACY_GRID, show: true, snap: true, snap_to_elements: true } },
})
const definitionOf = (first: Page): DashboardDefinition =>
  ({ schema_version: 3, dashboard_id: id(2), title: 'Canvas', description: '', datasets: [], filters: [], actions: [], pages: [first] })

type Mouse = { x: number; y: number; shift?: boolean }
let pressed: globalThis.Element | null = null
/** Pointer input as a mouse sends it: pressed on what is under the point, moved and released on the document. */
export const mouse = (type: 'down' | 'move' | 'up', { x, y, shift = false }: Mouse) => {
  const init: PointerEventInit = { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, shiftKey: shift,
    button: 0, buttons: type === 'up' ? 0 : 1, pointerId: 1, pointerType: 'mouse', isPrimary: true, view: window }
  if (type === 'down') {
    pressed = document.elementFromPoint(x, y)
    pressed!.dispatchEvent(new PointerEvent('pointerdown', init))
  } else document.dispatchEvent(new PointerEvent(type === 'move' ? 'pointermove' : 'pointerup', init))
  return Promise.resolve()
}
/** The centre of an element on screen. */
export const centre = (element: Element) => {
  const rect = document.querySelector(`[data-element-id="${element.element_id}"]`)!.getBoundingClientRect()
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
}
export const frameOrigin = () => document.querySelector('[data-canvas-frame]')!.getBoundingClientRect()
export const nextFrame = () => new Promise(resolve => requestAnimationFrame(() => resolve(null)))

export const arranged: ElementPlacement[][] = []
export const selections: string[][] = []
const clients: QueryClient[] = []

/** The canvas with selection and moves kept as the editor keeps them. */
function Harness({ start }: { start: Page }) {
  const [definition, setDefinition] = useState(() => definitionOf(start))
  const [selection, setSelection] = useState<string[]>([])
  const noop = () => undefined
  return <DashboardCanvas definition={definition} page={definition.pages[0]} draft editing selection={selection}
    onSelect={next => { selections.push(next); setSelection(next) }}
    onArrange={placements => {
      arranged.push(placements)
      setDefinition(current => applyLocal(current, { op: 'elements.arrange', page_id: PAGE, placements }))
    }}
    onEditChart={noop} onDuplicate={noop} onRemove={noop} contextFor={() => undefined} onSelectValue={noop} />
}

export async function mount(elements: Element[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const screen = render(<QueryClientProvider client={client}>
    <div style={{ width: 1220, height: 900, display: 'flex', flexDirection: 'column' }}><Harness start={page(elements)} /></div>
  </QueryClientProvider>)
  await expect.poll(() => document.querySelectorAll('[data-element-id]').length).toBe(elements.length)
  return screen
}

/** Register the per-test setup and teardown the harness needs. */
export function installCanvasHarness() {
  beforeEach(async () => {
    await browser.viewport(1400, 1000)
    arranged.length = 0
    selections.length = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => { throw new Error(`Unexpected request ${String(input)}`) }))
  })
  afterEach(async () => {
    await cleanup()
    for (const client of clients.splice(0)) client.clear()
    vi.unstubAllGlobals()
  })
}
