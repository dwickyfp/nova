import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cdp, page as browser } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import type { DashboardDefinition, ElementPlacement } from '../contracts'
import { DashboardCanvas } from './canvas'
import { LEGACY_GRID, type Element, type Page } from './layout'
import { applyLocal } from './operations'
import '@/styles/index.css'

// Canvas authoring: marquee, multi-select, moving together, smart guides, and the drag frame budget.

const id = (value: number) => `bbbbbbbb-0000-4000-8000-${String(value).padStart(12, '0')}`
const PAGE = id(1)
const FLAGS = { locked: false, hidden: false, decorative: false }
const box = (x: number, y: number, w: number, h: number) => ({ x, y, w, h, rotation: 0 as const })
const text = (n: number, x: number, y: number, w = 188, h = 60): Element =>
  ({ kind: 'text', element_id: id(100 + n), name: `Note ${n}`, ...FLAGS, geometry: box(x, y, w, h), role: 'body', text: { runs: [{ kind: 'text', text: `Note ${n}` }] } }) as Element
const shape = (n: number, x: number, y: number): Element =>
  ({ kind: 'shape', element_id: id(100 + n), name: `Shape ${n}`, ...FLAGS, geometry: box(x, y, 88, 60), shape: 'rect', style: { fill: { kind: 'solid', token: 'muted' } } }) as unknown as Element
const icon = (n: number, x: number, y: number): Element =>
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
const mouse = (type: 'down' | 'move' | 'up', { x, y, shift = false }: Mouse) => {
  const init: PointerEventInit = { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, shiftKey: shift,
    button: 0, buttons: type === 'up' ? 0 : 1, pointerId: 1, pointerType: 'mouse', isPrimary: true, view: window }
  if (type === 'down') {
    pressed = document.elementFromPoint(x, y)
    pressed!.dispatchEvent(new PointerEvent('pointerdown', init))
  } else document.dispatchEvent(new PointerEvent(type === 'move' ? 'pointermove' : 'pointerup', init))
  return Promise.resolve()
}
/** The centre of an element on screen. */
const centre = (element: Element) => {
  const rect = document.querySelector(`[data-element-id="${element.element_id}"]`)!.getBoundingClientRect()
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
}
const frameOrigin = () => document.querySelector('[data-canvas-frame]')!.getBoundingClientRect()
const nextFrame = () => new Promise(resolve => requestAnimationFrame(() => resolve(null)))

const arranged: ElementPlacement[][] = []
const selections: string[][] = []
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

async function mount(elements: Element[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const screen = render(<QueryClientProvider client={client}>
    <div style={{ width: 1220, height: 900, display: 'flex', flexDirection: 'column' }}><Harness start={page(elements)} /></div>
  </QueryClientProvider>)
  await expect.poll(() => document.querySelectorAll('[data-element-id]').length).toBe(elements.length)
  return screen
}

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

describe('selecting several items', () => {
  const a = text(1, 6, 6)
  const b = text(2, 306, 6)
  const c = text(3, 6, 406)

  it('selects what a marquee touches, and Shift adds to it', async () => {
    await mount([a, b, c])
    const origin = frameOrigin()
    // From empty page below b, up and left across b and into a; c is further down.
    await mouse('down', { x: origin.left + 400, y: origin.top + 200 })
    await mouse('move', { x: origin.left + 250, y: origin.top + 120 })
    await mouse('move', { x: origin.left + 100, y: origin.top + 40 })
    await expect.poll(() => document.querySelector('[data-testid="marquee"]')).not.toBeNull()
    await mouse('up', { x: origin.left + 100, y: origin.top + 40 })
    await expect.poll(() => selections[selections.length - 1]).toEqual([a.element_id, b.element_id])
    expect(document.querySelector('[data-testid="marquee"]')).toBeNull()
    await expect.poll(() => [...document.querySelectorAll('[data-selected]')].map(item => item.getAttribute('data-element-id')))
      .toEqual([a.element_id, b.element_id])

    const point = centre(c)
    await mouse('down', { ...point, shift: true })
    await mouse('up', { ...point, shift: true })
    await expect.poll(() => selections[selections.length - 1]).toEqual([a.element_id, b.element_id, c.element_id])
    // Shift-clicking a selected item takes it out again.
    await mouse('down', { ...point, shift: true })
    await mouse('up', { ...point, shift: true })
    await expect.poll(() => selections[selections.length - 1]).toEqual([a.element_id, b.element_id])
    expect(arranged).toHaveLength(0)
  })

  it('moves the selected items together in one change, with guides while dragging', async () => {
    await mount([a, b, c])
    const first = centre(a)
    await mouse('down', first)
    await mouse('up', first)
    const second = centre(b)
    await mouse('down', { ...second, shift: true })
    await mouse('up', { ...second, shift: true })
    await expect.poll(() => selections[selections.length - 1]).toEqual([a.element_id, b.element_id])

    // Drag a down by 200 page pixels; the pair's left edge stays lined up with c, so a guide shows.
    await mouse('down', first)
    for (let step = 1; step <= 10; step++) {
      await mouse('move', { x: first.x, y: first.y + step * 20 })
      await nextFrame()
    }
    await expect.poll(() => document.querySelectorAll('[data-guide]').length).toBeGreaterThan(0)
    await mouse('up', { x: first.x, y: first.y + 200 })
    await expect.poll(() => arranged.length).toBe(1)
    const [placements] = arranged
    expect(placements.map(item => item.element_id)).toEqual([a.element_id, b.element_id])
    const dy = placements[0].geometry.y - a.geometry.y
    expect(dy).toBe(200)
    // Both moved by the same amount, and neither sideways.
    expect(placements.map(item => [item.geometry.x, item.geometry.y - dy])).toEqual([[a.geometry.x, a.geometry.y], [b.geometry.x, b.geometry.y]])
    expect(document.querySelectorAll('[data-guide]')).toHaveLength(0)
  })

  it('snaps a moved edge onto another item and shows the gap to it', async () => {
    const lone = text(4, 506, 206)
    await mount([a, lone])
    const point = centre(lone)
    await mouse('down', point)
    // Left by 296 page pixels: 4 pixels past a's right edge plus the gutter, so it snaps flush with the gap kept.
    for (let step = 1; step <= 8; step++) {
      await mouse('move', { x: point.x - step * 37, y: point.y - step * 25 })
      await nextFrame()
    }
    await expect.poll(() => document.querySelector('[data-guide="line"]')).not.toBeNull()
    await mouse('up', { x: point.x - 296, y: point.y - 200 })
    await expect.poll(() => arranged.length).toBe(1)
    // The top edge meets a's top edge.
    expect(arranged[0][0].geometry.y).toBe(a.geometry.y)
  })
})

describe('lines between items', () => {
  it('follows an item while it is dragged and after it lands', async () => {
    const a = text(1, 6, 6)
    const b = text(2, 506, 306)
    const line = { kind: 'connector', element_id: id(150), name: 'Flow', ...FLAGS, geometry: box(0, 0, 10, 10), head: 'arrow',
      start: { kind: 'element', element_id: a.element_id }, end: { kind: 'element', element_id: b.element_id } } as unknown as Element
    await mount([a, b, line])
    const path = () => document.querySelector(`[data-element-id="${line.element_id}"] path[stroke-linecap="round"]`)?.getAttribute('d')
    await expect.poll(path).toBeTruthy()
    const before = path()
    const point = centre(b)
    await mouse('down', point)
    for (let step = 1; step <= 6; step++) {
      await mouse('move', { x: point.x + step * 20, y: point.y })
      await nextFrame()
    }
    // Mid-drag, the line already ends at the moved item.
    await expect.poll(path).not.toBe(before)
    const during = path()
    await mouse('up', { x: point.x + 120, y: point.y })
    await expect.poll(() => arranged.length).toBe(1)
    expect(arranged[0][0].element_id).toBe(b.element_id)
    await expect.poll(path).toBe(during)
  })
})

describe('drag performance', () => {
  // 60 items: notes, shapes and icons on a 10 by 6 grid.
  const sixty = () => Array.from({ length: 60 }, (_, n) => {
    const x = 6 + (n % 10) * 118
    const y = 6 + Math.floor(n / 10) * 110
    return n % 3 === 0 ? text(n, x, y, 100, 60) : n % 3 === 1 ? shape(n, x, y) : icon(n, x, y)
  })

  type Frames = { count: number; median: number; p95: number; max: number }

  /**
   * Drag `target` through 90 pointer moves, timing every frame the canvas draws
   * with the style and layout work it causes. `dy` is 1 to drag down, -1 up.
   */
  async function measuredDrag(target: Element, dy: 1 | -1): Promise<Frames> {
    arranged.length = 0
    // Benchmark hygiene: a collection beforehand keeps a pause for garbage left by earlier tests out of the frames.
    await (cdp() as unknown as { send(method: string): Promise<unknown> }).send('HeapProfiler.collectGarbage')
    const durations: number[] = []
    const real = window.requestAnimationFrame.bind(window)
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => real(time => {
      const begin = performance.now()
      callback(time)
      void document.body.getBoundingClientRect()
      void document.querySelector(`[data-element-id="${target.element_id}"]`)!.getBoundingClientRect()
      durations.push(performance.now() - begin)
    }))
    const point = centre(target)
    await mouse('down', point)
    for (let step = 1; step <= 90; step++) {
      await mouse('move', { x: point.x + (step % 30) * 3, y: point.y + dy * step })
      // Wait with the real scheduler so only the canvas's own frames are timed.
      await new Promise(resolve => real(() => real(resolve)))
    }
    await mouse('up', { x: point.x, y: point.y + dy * 90 })
    vi.stubGlobal('requestAnimationFrame', real)
    await expect.poll(() => arranged.length).toBe(1)
    // The first frame also mounts the guides layer.
    const sorted = durations.slice(1).sort((p, q) => p - q)
    const at = (share: number) => sorted[Math.min(sorted.length - 1, Math.floor(share * sorted.length))]
    return { count: durations.length, median: at(0.5), p95: at(0.95), max: sorted[sorted.length - 1] }
  }

  /**
   * The 8 ms budget per frame. Wall-clock time also counts moments the system
   * takes the processor away, which happens when the full suite runs many
   * browsers at once, so the drag is measured up to three times: every attempt
   * must keep 95% of its frames within budget, and one attempt must keep every
   * frame within it. A real slowdown fails all three. Each drag starts where
   * the item now is, so `dy` must not leave it under a later (higher) item.
   */
  async function expectWithinBudget(target: Element, annotate: (message: string) => Promise<unknown>, dy: 1 | -1 = 1) {
    // One unmeasured drag warms the code up.
    const warm = centre(target)
    await mouse('down', warm)
    for (let step = 1; step <= 10; step++) {
      await mouse('move', { x: warm.x + step, y: warm.y + step })
      await nextFrame()
    }
    await mouse('move', warm)
    await nextFrame()
    await mouse('up', warm)
    const attempts: Frames[] = []
    while (attempts.length < 3 && !attempts.some(frames => frames.max <= 8)) {
      const frames = await measuredDrag(target, dy)
      attempts.push(frames)
      await annotate(`drag ${attempts.length}: ${frames.count} frames, median ${frames.median.toFixed(2)} ms, p95 ${frames.p95.toFixed(2)} ms, max ${frames.max.toFixed(2)} ms`)
      // Every attempt moved the item it pressed, not one it landed on.
      expect(arranged[0].map(item => item.element_id)).toContain(target.element_id)
      // One frame per move; a slow runner may merge a few.
      expect(frames.count).toBeGreaterThan(60)
      expect(frames.p95).toBeLessThanOrEqual(8)
    }
    expect(Math.min(...attempts.map(frames => frames.max))).toBeLessThanOrEqual(8)
  }

  it('keeps each frame within 8 ms dragging all 60 items together', { timeout: 60_000 }, async ({ annotate }) => {
    const elements = sixty()
    await mount(elements)
    // Select all 60 with a marquee, then drag them.
    const origin = frameOrigin()
    await mouse('down', { x: origin.left + 1199, y: origin.top + 710 })
    await mouse('move', { x: origin.left + 600, y: origin.top + 400 })
    await mouse('move', { x: origin.left + 1, y: origin.top + 1 })
    await mouse('up', { x: origin.left + 1, y: origin.top + 1 })
    await expect.poll(() => selections[selections.length - 1]?.length).toBe(60)
    await expectWithinBudget(elements[0], annotate)
    expect(arranged[0]).toHaveLength(60)
  })

  it('keeps each frame within 8 ms dragging one item that snaps to the other 59', { timeout: 60_000 }, async ({ annotate }) => {
    const elements = sixty()
    await mount(elements)
    // Upward, onto items drawn beneath it: dragged down it would land under item 43,
    // and a second attempt would press that item instead.
    await expectWithinBudget(elements[33], annotate, -1)
    expect(arranged[0].map(item => item.element_id)).toEqual([elements[33].element_id])
  })
})
