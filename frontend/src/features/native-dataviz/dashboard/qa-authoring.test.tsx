import { describe, expect, it } from 'vitest'
import { id, FLAGS, box, text, mouse, centre, frameOrigin, nextFrame, arranged, selections, mount, installCanvasHarness } from './canvas-harness'
import type { Element } from './layout'

// Canvas authoring: marquee, multi-select, moving together and smart guides.
// The drag frame budget is measured alone, in drag-budget.perf.test.tsx.

installCanvasHarness()

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
