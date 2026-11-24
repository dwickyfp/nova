import { describe, expect, it } from 'vitest'
import type { ConnectorElement, DashboardDefinition } from '../contracts'
import {
  absoluteBoxes, boxStyle, clampBox, columnEdges, connectorPath, countElements, fillCss, findElement, frameGrid, mobileHeight,
  mobileOrder, nudgeBox, pageSize, readingOrder, resizeBox, scaleFor, snapBox, tabOrder, themeRadius, toGeometry,
} from './frame'
import { applyLocal, inverse } from './operations'
import { cellOf, cellRect, LEGACY_GRID, upcastPage, type Element, type Page } from './layout'

const box = (x: number, y: number, w = 100, h = 50) => ({ x, y, w, h, rotation: 0 as const })
const text = (id: string, x: number, y: number, extra: Partial<Element> = {}): Element =>
  ({ kind: 'text', element_id: id, name: '', geometry: box(x, y), role: 'body', text: { runs: [{ kind: 'text', text: id }] }, ...extra }) as Element
const page = (elements: Element[], extra: Partial<Page> = {}): Page => ({
  page_id: 'p', title: 'P', elements, tab_order: [], mobile: { order: [], hidden: [] }, legacy_spacers: [],
  canvas: { size: 'custom', width: 1200, height: 720, fit: 'width', grid: { ...LEGACY_GRID, show: true, snap: true, snap_to_elements: true } },
  ...extra,
})
const legacy = () => upcastPage({ page_id: 'p', title: 'P', widgets: [] })

describe('page frame', () => {
  it('uses the preset size and scales by fit mode', () => {
    const slide = page([], { canvas: { size: '16:9', fit: 'page' } })
    expect(pageSize(slide)).toEqual({ width: 1280, height: 720 })
    expect(scaleFor(slide, { width: 640, height: 1000 })).toBe(0.5)
    expect(scaleFor(slide, { width: 2560, height: 360 })).toBe(0.5)
    expect(scaleFor(page([], { canvas: { size: '16:9', fit: 'actual' } }), { width: 300, height: 300 })).toBe(1)
    // A scrolling page fills the width; at 1,200 pixels a legacy page draws one to one.
    expect(scaleFor(legacy(), { width: 1200, height: 300 })).toBe(1)
    expect(scaleFor(legacy(), { width: 600, height: 300 })).toBe(0.5)
    expect(scaleFor(legacy(), { width: 0, height: 0 })).toBe(1)
  })

  it('reads top to bottom, then left to right, with a row tolerance', () => {
    const items = [text('c', 0, 200), text('b', 500, 20), text('a', 0, 0), text('d', 300, 224)]
    expect(readingOrder(items).map(item => item.element_id)).toEqual(['a', 'b', 'c', 'd'])
    // 25 pixels lower is the next row.
    expect(readingOrder([text('low', 0, 25), text('high', 600, 0)]).map(item => item.element_id)).toEqual(['high', 'low'])
  })

  it('puts the explicit tab order first and skips decorative and hidden items', () => {
    const items = [text('a', 0, 0), text('b', 200, 0), text('c', 0, 300), text('deco', 0, 400, { decorative: true }), text('gone', 0, 500, { hidden: true })]
    expect(tabOrder(page(items)).map(item => item.element_id)).toEqual(['a', 'b', 'c'])
    expect(tabOrder(page(items, { tab_order: ['c', 'a'] })).map(item => item.element_id)).toEqual(['c', 'a', 'b'])
    expect(tabOrder(page(items), { includeHidden: true }).map(item => item.element_id)).toEqual(['a', 'b', 'c', 'gone'])
  })

  it('projects a phone column from content only, grouped content included', () => {
    const group = { kind: 'group', element_id: 'g', name: 'Group', geometry: box(0, 100, 600, 400),
      children: [text('inner', 10, 10), { kind: 'shape', element_id: 's', geometry: box(0, 0) }] } as unknown as Element
    const items = [
      text('b', 600, 0), text('a', 0, 0), group, { kind: 'shape', element_id: 'line', geometry: box(0, 50) } as Element,
      { kind: 'icon', element_id: 'icon', icon: 'truck', label: 'Deliveries', geometry: box(0, 900) } as Element,
      { kind: 'icon', element_id: 'bare', icon: 'truck', geometry: box(0, 950) } as Element,
      text('gone', 0, 990),
    ]
    const order = (extra: Partial<Page> = {}) => mobileOrder(page(items, extra)).map(item => item.element_id)
    expect(order()).toEqual(['a', 'b', 'inner', 'icon', 'gone'])
    expect(order({ mobile: { order: ['icon', 'b'], hidden: ['gone', 'a'] } })).toEqual(['icon', 'b', 'inner'])
    expect(mobileHeight(text('short', 0, 0, { geometry: box(0, 0, 100, 40) }))).toBe(120)
    expect(mobileHeight(text('tall', 0, 0, { geometry: box(0, 0, 100, 2000) }))).toBe(640)
  })

  it('places group children relative to their group and counts every item', () => {
    const items = [{ kind: 'group', element_id: 'g', geometry: box(100, 200, 400, 300),
      children: [text('child', 10, 20), { kind: 'group', element_id: 'g2', geometry: box(50, 50, 100, 100), children: [text('deep', 5, 5)] }] } as unknown as Element]
    const boxes = absoluteBoxes(items)
    expect(boxes.get('child')).toEqual({ x: 110, y: 220, w: 100, h: 50 })
    expect(boxes.get('deep')).toEqual({ x: 155, y: 255, w: 100, h: 50 })
    expect(countElements(items)).toBe(4)
    expect(findElement(items, 'deep')?.element_id).toBe('deep')
    expect(findElement(items, 'none')).toBeUndefined()
  })
})

describe('snapping and keyboard steps', () => {
  const grid = frameGrid(legacy())

  it('snaps a legacy cell back to exactly itself, so grid pages stay on the grid', () => {
    for (const cell of [{ x: 0, y: 0, w: 6, h: 4 }, { x: 3, y: 7, w: 3, h: 2 }, { x: 10, y: 40, w: 2, h: 9 }]) {
      const rect = cellRect(cell)
      const nudged = { x: rect.x + 5, y: rect.y - 3, w: rect.w, h: rect.h }
      expect(snapBox(nudged, grid, 1200, 'move')).toEqual({ x: rect.x, y: rect.y, w: rect.w, h: rect.h })
      const stretched = { x: rect.x, y: rect.y, w: rect.w + 7, h: rect.h + 3 }
      expect(snapBox(stretched, grid, 1200, { edges: 'se' })).toEqual({ x: rect.x, y: rect.y, w: rect.w, h: rect.h })
    }
  })

  it('uses column edges near them and the baseline elsewhere', () => {
    const { starts, ends } = columnEdges(grid, 1200)
    expect(starts.slice(0, 3)).toEqual([6, 106, 206])
    expect(ends.slice(0, 2)).toEqual([94, 194])
    // Halfway between columns there is no edge to pull to: the baseline rounds it.
    expect(snapBox({ x: 150, y: 10, w: 80, h: 80 }, grid, 1200, 'move')).toEqual({ x: 150, y: 14, w: 80, h: 80 })
    // A right edge near a column end aligns the box to it.
    expect(snapBox({ x: 40, y: 6, w: 150, h: 60 }, grid, 1200, 'move').x).toBe(44)
  })

  it('resizes by edges with a minimum size', () => {
    expect(resizeBox({ x: 100, y: 100, w: 200, h: 100 }, 'nw', 50, 20)).toEqual({ x: 150, y: 120, w: 150, h: 80 })
    expect(resizeBox({ x: 100, y: 100, w: 200, h: 100 }, 'e', -500, 0)).toEqual({ x: 100, y: 100, w: 24, h: 100 })
    expect(resizeBox({ x: 100, y: 100, w: 200, h: 100 }, 's', 0, 30)).toEqual({ x: 100, y: 100, w: 200, h: 130 })
  })

  it('keeps top-level items on the page and lets a scrolling page grow down', () => {
    expect(clampBox({ x: -40, y: -10, w: 300, h: 200 }, legacy())).toEqual({ x: 0, y: 0, w: 300, h: 200 })
    expect(clampBox({ x: 1100, y: 5000, w: 300, h: 200 }, legacy())).toEqual({ x: 900, y: 5000, w: 300, h: 200 })
    const slide = page([], { canvas: { size: '16:9', fit: 'page' } })
    expect(clampBox({ x: 0, y: 700, w: 300, h: 200 }, slide)).toEqual({ x: 0, y: 520, w: 300, h: 200 })
    // A resize trims the edge that crossed the page instead of moving the item.
    expect(clampBox({ x: 1100, y: 0, w: 300, h: 200 }, legacy(), { resize: true })).toEqual({ x: 1100, y: 0, w: 100, h: 200 })
    expect(clampBox({ x: -20.4, y: -3, w: 10.6, h: 5 }, legacy(), { nested: true })).toEqual({ x: -20, y: -3, w: 11, h: 5 })
    expect(toGeometry({ x: 1.4, y: 2.6, w: 0.2, h: 9 }, 90)).toEqual({ x: 1, y: 3, w: 1, h: 9, rotation: 90 })
  })

  it('steps by columns with snapping and by baseline rows', () => {
    const rect = cellRect({ x: 2, y: 1, w: 3, h: 2 })
    const geometry = { x: rect.x, y: rect.y, w: rect.w, h: rect.h }
    expect(cellOf({ ...nudgeBox(geometry, 'ArrowRight', false, grid, 1200, true), rotation: 0 })).toEqual({ x: 3, y: 1, w: 3, h: 2 })
    expect(cellOf({ ...nudgeBox(geometry, 'ArrowLeft', true, grid, 1200, true), rotation: 0 })).toEqual({ x: 2, y: 1, w: 2, h: 2 })
    expect(nudgeBox(geometry, 'ArrowDown', false, grid, 1200, true)).toEqual({ ...geometry, y: geometry.y + 8 })
    expect(nudgeBox(geometry, 'ArrowRight', false, grid, 1200, false)).toEqual({ ...geometry, x: geometry.x + 8 })
    expect(nudgeBox(geometry, 'ArrowUp', true, grid, 1200, true)).toEqual({ ...geometry, h: geometry.h - 8 })
  })
})

describe('vectors and styles', () => {
  const boxes = new Map([['a', { x: 0, y: 0, w: 100, h: 100 }], ['b', { x: 300, y: 0, w: 100, h: 100 }]])
  const connector = (extra: Partial<ConnectorElement>): ConnectorElement => ({
    kind: 'connector', element_id: 'c', geometry: box(0, 0), start: { kind: 'element', element_id: 'a', side: 'auto' },
    end: { kind: 'element', element_id: 'b', side: 'auto' }, ...extra,
  })

  it('joins facing sides and hides a line whose end cannot be found', () => {
    const straight = connectorPath(connector({}), boxes)!
    expect(straight.d).toBe('M 100 50 L 300 50')
    expect(straight.angle).toBe(0)
    expect(connectorPath(connector({ curve: 'elbow', end: { kind: 'point', x: 300, y: 250 } }), boxes)!.d)
      .toBe('M 100 50 L 200 50 L 200 250 L 300 250')
    expect(connectorPath(connector({ curve: 'arc' }), boxes)!.d).toMatch(/^M 100 50 Q /)
    expect(connectorPath(connector({ end: { kind: 'element', element_id: 'missing', side: 'auto' } }), boxes)).toBeNull()
    expect(connectorPath(connector({ end: { kind: 'datum', anchor: { element_id: 'a', match: [] } } as unknown as ConnectorElement['end'] }), boxes)).toBeNull()
    expect(connectorPath(connector({ start: { kind: 'element', element_id: 'a', side: 's' } }), boxes)!.start).toEqual({ x: 50, y: 100 })
  })

  it('rounds corners on the theme scale, so a legacy card keeps its rounding', () => {
    expect(themeRadius(8)).toBe('var(--radius)')
    expect(themeRadius(4)).toBe('calc(var(--radius) * 0.5)')
    expect(themeRadius(16)).toBe('calc(var(--radius) * 2)')
    expect(themeRadius(9999)).toBe('9999px')
    expect(themeRadius(0)).toBeUndefined()
  })

  it('keeps padding at its saved size, like the content it surrounds', () => {
    expect(boxStyle({ padding: 12 }).padding).toBe(12)
    expect(boxStyle({ padding: 0 }).padding).toBeUndefined()
  })

  it('paints only theme tokens', () => {
    expect(fillCss({ kind: 'solid', token: 'card', opacity: 100 })).toBe('var(--card)')
    expect(fillCss({ kind: 'solid', token: 'brand', opacity: 40 })).toBe('color-mix(in oklab, var(--brand) 40%, transparent)')
    expect(fillCss({ kind: 'gradient', start: 'chart-1', end: 'chart-2', angle: 90 })).toBe('linear-gradient(90deg, var(--chart-1), var(--chart-2))')
    expect(fillCss(null)).toBeUndefined()
  })
})

describe('canvas operations in the browser', () => {
  it('moves items at any depth and undoes the move exactly', () => {
    const before: DashboardDefinition = { schema_version: 3, dashboard_id: 'd', title: 'D', description: '', datasets: [], filters: [], actions: [],
      pages: [page([text('a', 6, 6), { kind: 'group', element_id: 'g', name: '', geometry: box(0, 300, 400, 200), children: [text('inner', 0, 0)] } as unknown as Element])] }
    const change = { op: 'elements.arrange' as const, page_id: 'p', placements: [
      { element_id: 'a', geometry: { x: 406, y: 1494, w: 300, h: 100, rotation: 0 as const } },
      { element_id: 'inner', geometry: { x: 20, y: 30, w: 100, h: 50, rotation: 0 as const } },
    ] }
    const after = applyLocal(before, change)
    expect(findElement(after.pages[0].elements!, 'a')!.geometry.y).toBe(1494)
    expect(findElement(after.pages[0].elements!, 'inner')!.geometry.x).toBe(20)
    // The scrolling page grows to hold the moved item.
    expect(after.pages[0].canvas!.height).toBe(1594 + 6)
    const back = inverse(change, before)!
    expect(applyLocal(after, back).pages[0].elements).toEqual(before.pages[0].elements)
    expect(inverse({ ...change, placements: [{ element_id: 'missing', geometry: box(0, 0) }] }, before)).toBeNull()
  })
})
