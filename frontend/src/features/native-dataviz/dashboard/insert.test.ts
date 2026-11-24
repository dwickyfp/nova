import { describe, expect, it } from 'vitest'
import type { DashboardDefinition } from '../contracts'
import type { Command } from './commands'
import { tabOrder } from './frame'
import * as insert from './insert'
import { LEGACY_GRID, type Element, type Page } from './layout'
import { applyLocal, inverseAll, type Change } from './operations'

const box = (x: number, y: number, w = 100, h = 50) => ({ x, y, w, h, rotation: 0 as const })
// Saved documents always carry these flags.
const FLAGS = { locked: false, hidden: false, decorative: false, style: {} }
const text = (id: string, x: number, y: number, w = 100, h = 50, extra: Partial<Element> = {}): Element =>
  ({ kind: 'text', element_id: id, name: `Note ${id}`, ...FLAGS, geometry: box(x, y, w, h), role: 'body', text: { runs: [{ kind: 'text', text: id }] }, ...extra }) as Element
const page = (elements: Element[], extra: Partial<Page> = {}): Page => ({
  page_id: 'p', title: 'P', elements, tab_order: [], mobile: { order: [], hidden: [] }, legacy_spacers: [],
  canvas: { size: 'custom', width: 1200, height: 720, fit: 'width', grid: { ...LEGACY_GRID, show: true, snap: true, snap_to_elements: true } },
  ...extra,
})
const dashboard = (first: Page): DashboardDefinition =>
  ({ schema_version: 3, dashboard_id: 'd', title: 'D', description: '', datasets: [], filters: [], actions: [], pages: [first] })

function changes(command: Command): Change[] {
  if (!command.changes) throw new Error(`Expected changes, got: ${command.message}`)
  return command.changes
}

/** Apply changes, undo them with the recorded inverse, and expect the page back exactly. */
function roundTrip(before: DashboardDefinition, list: Change[]) {
  const backward = inverseAll(list, before)
  expect(backward).not.toBeNull()
  const after = list.reduce(applyLocal, before)
  expect(backward!.reduce(applyLocal, after).pages).toEqual(before.pages)
  return after.pages[0]
}

describe('placing new items', () => {
  it('puts a new item in the first free place inside the margins', () => {
    // LEGACY_GRID: 12 columns, 12 px gutter, 6 px margin, 8 px rows.
    expect(insert.freeSpot(page([]), 240, 160)).toEqual({ x: 6, y: 6, w: 240, h: 160 })
    // The top-left is taken, so the next column start that clears it.
    const spot = insert.freeSpot(page([text('a', 6, 6, 300, 200)]), 240, 160)
    // Column starts are 6 + n × 100 px. 306 touches the item; 406 leaves a gutter.
    expect(spot).toEqual({ x: 406, y: 6, w: 240, h: 160 })
  })

  it('falls back to the top-left margin when nothing is free', () => {
    expect(insert.freeSpot(page([text('full', 0, 0, 1200, 720)]), 240, 160)).toEqual({ x: 6, y: 6, w: 240, h: 160 })
  })

  it('adds a shape as a decorative, named, selected item that Undo removes', () => {
    const before = dashboard(page([text('a', 6, 6, 300, 200)]))
    const command = insert.addShapeCommand(before.pages[0], 'rect', 's1')
    expect(command.message).toBe('Added a rectangle at x 406, y 6')
    expect('select' in command && command.select).toEqual(['s1'])
    const after = roundTrip(before, changes(command))
    const shape = after.elements!.find(item => item.element_id === 's1')!
    expect(shape).toMatchObject({ kind: 'shape', shape: 'rect', name: 'Rectangle 1', decorative: true, style: { radius: 8, fill: { kind: 'solid', token: 'card' }, stroke: { token: 'border', width: 1 } } })
    // Added in front.
    expect(after.elements![after.elements!.length - 1].element_id).toBe('s1')
  })

  it('draws a new line with an outline rather than a fill', () => {
    const shape = changes(insert.addShapeCommand(page([]), 'line', 'l'))[0]
    expect(shape).toMatchObject({ op: 'elements.add', elements: [{ shape: 'line', geometry: { w: 240, h: 16 }, style: { stroke: { token: 'foreground', width: 2 } } }] })
  })

  it('adds an icon named after its picture, decorative until it has a label', () => {
    const command = insert.addIconCommand(page([]), 'shopping-cart', 'i')
    expect(command.message).toBe('Added the shopping cart icon at x 6, y 6')
    expect(changes(command)[0]).toMatchObject({ elements: [{ kind: 'icon', icon: 'shopping-cart', name: 'Shopping cart', label: '', decorative: true }] })
    expect(insert.iconWords('building-2')).toBe('Building')
  })
})

describe('connecting two items', () => {
  const a = text('a', 0, 0)
  const b = text('b', 400, 300)

  it('anchors both ends to the items, in the order they were selected', () => {
    const before = dashboard(page([a, b]))
    const command = insert.connectCommand(before.pages[0], ['b', 'a'], 'line')
    expect(command.message).toBe('Connected Note b to Note a')
    const after = roundTrip(before, changes(command))
    expect(after.elements!.find(item => item.element_id === 'line')).toMatchObject({
      kind: 'connector', start: { kind: 'element', element_id: 'b' }, end: { kind: 'element', element_id: 'a' },
      head: 'arrow', geometry: box(0, 0, 500, 350),
    })
  })

  it('needs exactly two items, and not lines', () => {
    expect(insert.connectCommand(page([a, b]), ['a'], 'x').changes).toBeNull()
    expect(insert.connectCommand(page([a, b]), ['a'], 'x').message).toBe('Select exactly two items to connect them')
    const line = { kind: 'connector', element_id: 'l', name: '', ...FLAGS, geometry: box(0, 0, 10, 10), head: 'arrow',
      start: { kind: 'element', element_id: 'a' }, end: { kind: 'element', element_id: 'b' } } as unknown as Element
    expect(insert.connectCommand(page([a, b, line]), ['a', 'l'], 'x').changes).toBeNull()
  })
})

describe('formatting', () => {
  it('merges a style change into each selected item and undoes it', () => {
    const styled = text('b', 200, 0, 100, 50, { style: { radius: 8, shadow: 'sm' } })
    // 'a' has the default (empty) style, which Undo puts back rather than removing.
    const before = dashboard(page([text('a', 0, 0), styled]))
    const command = insert.styleCommand(before.pages[0], ['a', 'b'], { fill: { kind: 'solid', token: 'card', opacity: 100 } }, 'Fill changed')
    const after = roundTrip(before, changes(command))
    expect(after.elements!.map(item => item.style)).toEqual([
      { fill: { kind: 'solid', token: 'card', opacity: 100 } },
      { radius: 8, shadow: 'sm', fill: { kind: 'solid', token: 'card', opacity: 100 } },
    ])
  })

  it('takes a decorative item out of the tab order, so the page stays valid', () => {
    const before = dashboard(page([text('a', 0, 0), text('b', 0, 100)], { tab_order: ['b', 'a'] }))
    const command = insert.decorativeCommand(before.pages[0], ['b'], true)
    expect(command.message).toBe('The item is now decorative and skipped by screen readers')
    const after = roundTrip(before, changes(command))
    expect(after.tab_order).toEqual(['a'])
    expect(after.elements![1].decorative).toBe(true)
    expect(changes(insert.decorativeCommand(after, ['b'], false))).toHaveLength(1)
  })

  it('sets and removes the page background', () => {
    const before = dashboard(page([]))
    const gradient = { kind: 'gradient' as const, start: 'brand' as const, end: 'chart-2' as const, angle: 135 }
    const after = roundTrip(before, changes(insert.backgroundCommand(before.pages[0], gradient)))
    expect(after.canvas!.background).toEqual(gradient)
    expect(after.canvas!.grid).toEqual(before.pages[0].canvas!.grid)
    expect(insert.backgroundCommand(after, null).message).toBe('Page background removed')
  })
})

describe('layer and tab order', () => {
  const items = [text('a', 0, 0), text('b', 200, 0), text('c', 400, 0)]

  it('moves an item to a place counted from the front', () => {
    const before = dashboard(page(items))
    // The list shows c, b, a (front first); move a to the front.
    const command = insert.layerCommand(before.pages[0], 'a', 0)
    expect(command.message).toBe('Note a is now 1 of 3 from the front')
    expect(roundTrip(before, changes(command)).elements!.map(item => item.element_id)).toEqual(['b', 'c', 'a'])
    expect(insert.layerCommand(before.pages[0], 'c', 0).message).toBe('Already at the front')
    expect(changes(insert.layerCommand(before.pages[0], 'c', 2))[0]).toMatchObject({ element_ids: ['c', 'a', 'b'] })
  })

  it('moves an item in the tab order, keeping hidden items in their place', () => {
    const hidden = text('h', 600, 0, 100, 50, { hidden: true })
    const before = dashboard(page([...items, hidden]))
    const command = insert.tabOrderCommand(before.pages[0], 'c', 0)
    expect(command.message).toBe('Note c is now 1 of 4 in the tab order')
    const after = roundTrip(before, changes(command))
    expect(after.tab_order).toEqual(['c', 'a', 'b', 'h'])
    expect(tabOrder(after).map(item => item.element_id)).toEqual(['c', 'a', 'b'])
    expect(insert.tabOrderCommand(after, 'c', 0).message).toBe('Already first')
    expect(insert.resetTabOrderCommand(after).message).toBe('The tab order follows the page again')
    expect(insert.resetTabOrderCommand(before.pages[0]).changes).toBeNull()
  })

  it('leaves decorative items out of the tab order', () => {
    const decorative = text('d', 0, 300, 100, 50, { decorative: true })
    expect(insert.tabOrderCommand(page([...items, decorative]), 'd', 0).message).toBe('Decorative items are not in the tab order')
  })
})
