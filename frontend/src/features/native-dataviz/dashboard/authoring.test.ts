import { describe, expect, it } from 'vitest'
import type { DashboardDefinition, ElementsAdd } from '../contracts'
import { align, autoArrange, distribute, restack, smartSnap, type SnapContext } from './authoring'
import * as commands from './commands'
import { findElement } from './frame'
import { LEGACY_GRID, type Element, type Page } from './layout'
import { applyLocal, inverseAll, type Change } from './operations'

const box = (x: number, y: number, w = 100, h = 50) => ({ x, y, w, h, rotation: 0 as const })
// Saved documents always carry these flags.
const FLAGS = { locked: false, hidden: false, decorative: false }
const text = (id: string, x: number, y: number, w = 100, h = 50, extra: Partial<Element> = {}): Element =>
  ({ kind: 'text', element_id: id, name: '', ...FLAGS, geometry: box(x, y, w, h), role: 'body', text: { runs: [{ kind: 'text', text: id }] }, ...extra }) as Element
const visual = (id: string, x: number, y: number): Element =>
  ({ kind: 'visual', element_id: id, name: '', ...FLAGS, geometry: box(x, y, 300, 200), chart_ref: { chart_id: `c-${id}`, revision: 1 } }) as Element
const group = (id: string, x: number, y: number, children: Element[]): Element =>
  ({ kind: 'group', element_id: id, name: '', ...FLAGS, style: {}, geometry: box(x, y, 400, 300), children }) as unknown as Element
const page = (elements: Element[], extra: Partial<Page> = {}): Page => ({
  page_id: 'p', title: 'P', elements, tab_order: [], mobile: { order: [], hidden: [] }, legacy_spacers: [],
  canvas: { size: 'custom', width: 1200, height: 720, fit: 'width', grid: { ...LEGACY_GRID, show: true, snap: true, snap_to_elements: true } },
  ...extra,
})
const dashboard = (first: Page, extra: Partial<DashboardDefinition> = {}): DashboardDefinition =>
  ({ schema_version: 3, dashboard_id: 'd', title: 'D', description: '', datasets: [], filters: [], actions: [], pages: [first], ...extra })
const context = (extra: Partial<SnapContext> = {}): SnapContext => ({
  grid: LEGACY_GRID, width: 1200, height: 720, others: [{ x: 300, y: 100, w: 200, h: 100 }],
  snapGrid: false, snapElements: true, threshold: 12, ...extra,
})

/** A command that applies, with its changes. */
function applies(command: commands.Command) {
  if (!command.changes) throw new Error(`Expected changes, got: ${command.message}`)
  return command
}

/** Apply changes, undo them with the recorded inverse, and expect the page back exactly. */
function roundTrip(before: DashboardDefinition, changes: Change[]) {
  const backward = inverseAll(changes, before)
  expect(backward).not.toBeNull()
  const after = changes.reduce(applyLocal, before)
  const restored = backward!.reduce(applyLocal, after)
  expect(restored.pages).toEqual(before.pages)
  expect(restored.actions).toEqual(before.actions)
  return after
}

describe('smart snapping', () => {
  it('pulls an edge onto another item and draws the guide across both', () => {
    const { box: next, guides } = smartSnap({ x: 505, y: 207, w: 100, h: 50 }, context(), 'move')
    expect(next).toEqual({ x: 500, y: 200, w: 100, h: 50 })
    expect(guides).toEqual([
      { kind: 'line', axis: 'x', at: 500, from: 100, to: 250 },
      { kind: 'line', axis: 'y', at: 200, from: 300, to: 600 },
    ])
  })

  it('lines centres up with another item and with the page centre', () => {
    // The moved box's centre (x 395) meets the other item's centre (x 400).
    expect(smartSnap({ x: 345, y: 400, w: 100, h: 50 }, context(), 'move').box.x).toBe(350)
    // With nothing else nearby, the box centres on the page.
    expect(smartSnap({ x: 545, y: 600, w: 100, h: 50 }, context({ others: [] }), 'move').box.x).toBe(550)
  })

  it('labels the gap to the nearest neighbour on each side', () => {
    const { guides } = smartSnap({ x: 540, y: 120, w: 100, h: 50 }, context({ snapElements: false }), 'move')
    expect(guides).toEqual([{ kind: 'gap', axis: 'x', at: 145, from: 500, to: 540, label: '40' }])
  })

  it('snaps to column edges and baseline rows without a guide line, since the grid is drawn', () => {
    const { box: next, guides } = smartSnap({ x: 108, y: 31, w: 188, h: 50 }, context({ snapGrid: true, snapElements: false, others: [] }), 'move')
    expect(next).toEqual({ x: 106, y: 30, w: 188, h: 50 })
    expect(guides).toEqual([])
  })

  it('places freely when snapping is suspended', () => {
    const free = context({ snapGrid: false, snapElements: false, others: [] })
    expect(smartSnap({ x: 505, y: 207, w: 100, h: 50 }, free, 'move')).toEqual({ box: { x: 505, y: 207, w: 100, h: 50 }, guides: [] })
  })

  it('snaps only the dragged edges when resizing', () => {
    const { box: next } = smartSnap({ x: 40, y: 40, w: 455, h: 55 }, context({ snapGrid: false }), { edges: 'se' })
    // The right edge meets the other item's right edge; the bottom meets its top.
    expect(next).toEqual({ x: 40, y: 40, w: 460, h: 60 })
  })
})

describe('align, distribute and stacking', () => {
  const boxes = new Map([['a', { x: 0, y: 0, w: 100, h: 50 }], ['b', { x: 200, y: 100, w: 50, h: 50 }]])

  it('aligns several items with each other, and one item with the page', () => {
    expect([...align(boxes, 'left', { x: 0, y: 0, w: 1200, h: 720 }).values()].map(item => item.x)).toEqual([0, 0])
    expect([...align(boxes, 'center', { x: 0, y: 0, w: 1200, h: 720 }).values()].map(item => item.x)).toEqual([75, 100])
    expect([...align(boxes, 'bottom', { x: 0, y: 0, w: 1200, h: 720 }).values()].map(item => item.y)).toEqual([100, 100])
    expect(align(new Map([['a', { x: 10, y: 10, w: 100, h: 50 }]]), 'middle', { x: 0, y: 0, w: 1200, h: 720 }).get('a')).toEqual({ x: 10, y: 335, w: 100, h: 50 })
  })

  it('spaces three or more items evenly, keeping the outer two in place', () => {
    const spread = distribute(new Map([['c', { x: 400, y: 0, w: 100, h: 50 }], ['a', { x: 0, y: 0, w: 100, h: 50 }], ['b', { x: 150, y: 0, w: 50, h: 50 }]]), 'x')
    expect(Object.fromEntries([...spread].map(([id, item]) => [id, item.x]))).toEqual({ a: 0, b: 225, c: 400 })
    expect(distribute(boxes, 'x')).toEqual(boxes)
  })

  it('moves chosen items one step or all the way, keeping their own order', () => {
    const order = ['a', 'b', 'c', 'd']
    expect(restack(order, new Set(['a', 'c']), 'front')).toEqual(['b', 'd', 'a', 'c'])
    expect(restack(order, new Set(['b', 'd']), 'back')).toEqual(['b', 'd', 'a', 'c'])
    expect(restack(order, new Set(['a', 'c']), 'forward')).toEqual(['b', 'a', 'd', 'c'])
    expect(restack(order, new Set(['b', 'd']), 'backward')).toEqual(['b', 'a', 'd', 'c'])
    expect(restack(order, new Set(['d']), 'forward')).toEqual(order)
  })
})

describe('Auto-arrange', () => {
  it('puts overlapping items on whole columns and rows, one below the other', () => {
    const placements = autoArrange(page([text('a', 10, 10, 190, 50), text('b', 15, 30, 190, 50)]))!
    expect(placements).toEqual([
      { element_id: 'a', geometry: box(6, 6, 188, 52) },
      { element_id: 'b', geometry: box(6, 70, 188, 52) },
    ])
  })

  it('leaves locked items and shapes where they are, and flows around locked items', () => {
    const shape = { kind: 'shape', element_id: 's', name: '', geometry: box(13, 13), shape: 'rect' } as unknown as Element
    const placements = autoArrange(page([text('lock', 6, 6, 188, 52, { locked: true }), shape, text('a', 20, 20, 190, 50)]))!
    expect(placements).toEqual([{ element_id: 'a', geometry: box(6, 70, 188, 52) }])
  })

  it('arranges only the chosen items when asked', () => {
    const placements = autoArrange(page([text('a', 10, 10, 190, 50), text('b', 15, 300, 190, 50)]), new Set(['b']))!
    expect(placements.map(item => item.element_id)).toEqual(['b'])
    // 'a' stays where it is (bottom at 60), so 'b' starts on the first row below it and the gutter.
    expect(placements[0].geometry.y).toBe(78)
  })

  it('refuses when the result would not fit a fixed-height page', () => {
    const fixed = page([text('a', 6, 6, 188, 500), text('b', 6, 20, 188, 500)], { canvas: { size: 'custom', width: 1200, height: 720, fit: 'page', grid: LEGACY_GRID } })
    expect(autoArrange(fixed)).toBeNull()
    expect(commands.autoArrangeCommand(fixed, []).changes).toBeNull()
  })
})

describe('editor commands', () => {
  it('groups items into one undoable step that keeps their place and interactions', () => {
    const before = dashboard(page([visual('v', 100, 40), text('t', 600, 300), text('top', 0, 600)], { tab_order: ['t', 'v'] }),
      { actions: [{ action_id: 'x', kind: 'filter', source_widget_id: 'v', target_widget_ids: [], run_on: 'select' }] })
    const command = commands.groupCommand(before.pages[0], ['v', 't'], 'g')
    expect(command).toMatchObject({ select: ['g'], message: 'Grouped 2 items' })
    const after = roundTrip(before, command.changes!)
    const grouped = after.pages[0].elements!
    expect(grouped.map(item => item.element_id)).toEqual(['g', 'top'])
    expect(grouped[0].geometry).toEqual(box(100, 40, 600, 310))
    expect(findElement(grouped, 't')!.geometry).toEqual(box(500, 260))
    expect(after.actions).toEqual(before.actions)
  })

  it('ungroups back to page positions and undoes that too', () => {
    const before = dashboard(page([group('g', 200, 100, [text('a', 10, 20), text('b', 150, 120)]), text('c', 0, 600)], { tab_order: ['g', 'c'] }))
    const command = applies(commands.ungroupCommand(before.pages[0], ['g']))
    expect(command.select).toEqual(['a', 'b'])
    const after = roundTrip(before, command.changes!)
    expect(after.pages[0].elements!.map(item => [item.element_id, item.geometry.x, item.geometry.y])).toEqual([['a', 210, 120], ['b', 350, 220], ['c', 0, 600]])
    expect(after.pages[0].tab_order).toEqual(['c'])
  })

  it('locks, then unlocks when everything chosen is already locked', () => {
    const before = dashboard(page([text('a', 0, 0), text('b', 200, 0, 100, 50, { locked: true })]))
    const lock = commands.toggleCommand(before.pages[0], ['a', 'b'], 'locked')
    expect(lock.message).toBe('Locked 2 items')
    const after = roundTrip(before, lock.changes!)
    expect(after.pages[0].elements!.every(item => item.locked)).toBe(true)
    expect(commands.toggleCommand(after.pages[0], ['a', 'b'], 'locked').message).toBe('Unlocked 2 items')
    expect(commands.toggleCommand(before.pages[0], ['b'], 'hidden').message).toBe('Hid 1 item')
  })

  it('restacks and undoes the new order', () => {
    const before = dashboard(page([text('a', 0, 0), text('b', 10, 10), text('c', 20, 20)]))
    const command = commands.restackCommand(before.pages[0], ['a'], 'front')
    expect(roundTrip(before, command.changes!).pages[0].elements!.map(item => item.element_id)).toEqual(['b', 'c', 'a'])
    expect(commands.restackCommand(before.pages[0], ['c'], 'front').changes).toBeNull()
  })

  it('removes a group with its chosen child once, and undo brings back order, tab order and interactions', () => {
    const before = dashboard(page([text('a', 0, 0), group('g', 200, 100, [visual('v', 0, 0)]), text('c', 20, 20)], { tab_order: ['c', 'v', 'a'] }),
      { actions: [{ action_id: 'x', kind: 'filter', source_widget_id: 'v', target_widget_ids: [], run_on: 'select' }] })
    const command = commands.removeCommand(before.pages[0], ['v', 'g'])
    expect(command.changes).toEqual([{ op: 'elements.remove', page_id: 'p', element_ids: ['g'] }])
    const after = roundTrip(before, command.changes!)
    expect(after.pages[0].tab_order).toEqual(['c', 'a'])
    expect(after.actions).toEqual([])
  })

  it('says when removing an item from inside a group cannot be undone, and records no undo for it', () => {
    const before = dashboard(page([group('g', 200, 100, [text('a', 0, 0), text('b', 150, 0)])]))
    const command = applies(commands.removeCommand(before.pages[0], ['a']))
    expect(command.message).toBe('Removed 1 item. Removing items from inside a group cannot be undone')
    expect(inverseAll(command.changes, before)).toBeNull()
  })

  it('duplicates with new IDs, offset by two rows, and rejoins copied lines to the copies', () => {
    const line = { kind: 'connector', element_id: 'l', name: '', geometry: box(0, 0, 10, 10),
      start: { kind: 'element', element_id: 'a' }, end: { kind: 'element', element_id: 'far' } } as unknown as Element
    const before = dashboard(page([text('a', 0, 0), text('far', 600, 0), line]))
    let counter = 0
    const command = applies(commands.duplicateCommand(before.pages[0], ['a', 'l'], () => `n${++counter}`))
    expect(command.select).toEqual(['n1', 'n2'])
    const added = (command.changes[0] as ElementsAdd).elements as Element[]
    expect(added[0].geometry).toEqual(box(16, 16))
    expect(added[1]).toMatchObject({ start: { element_id: 'n1' }, end: { element_id: 'far' } })
    roundTrip(before, command.changes!)
  })

  it('moves several items together with Alt and an arrow, and resizes only one', () => {
    const before = dashboard(page([text('a', 6, 6, 188, 50), text('b', 206, 106, 188, 50), text('lock', 406, 6, 188, 50, { locked: true })]))
    const move = commands.nudgeCommand(before.pages[0], ['a', 'b', 'lock'], 'ArrowRight', false)
    expect(move.message).toBe('Moved 2 items')
    const after = roundTrip(before, move.changes!)
    expect(after.pages[0].elements!.map(item => item.geometry.x)).toEqual([106, 306, 406])
    expect(commands.nudgeCommand(before.pages[0], ['a', 'b'], 'ArrowRight', true).message).toBe('Select one item to resize it')
    expect(commands.nudgeCommand(before.pages[0], ['lock'], 'ArrowRight', false).message).toBe('This item is locked')
  })

  it('aligns and spreads items, ignoring locked ones', () => {
    const before = dashboard(page([text('a', 0, 0), text('b', 150, 40), text('c', 400, 80), text('lock', 900, 300, 100, 50, { locked: true })]))
    const aligned = commands.alignCommand(before.pages[0], ['a', 'b', 'c', 'lock'], 'top')
    expect(roundTrip(before, aligned.changes!).pages[0].elements!.map(item => item.geometry.y)).toEqual([0, 0, 0, 300])
    const spread = commands.distributeCommand(before.pages[0], ['a', 'b', 'c'], 'x')
    expect(roundTrip(before, spread.changes!).pages[0].elements!.map(item => item.geometry.x)).toEqual([0, 200, 400, 900])
    expect(commands.distributeCommand(before.pages[0], ['a', 'b'], 'x').changes).toBeNull()
  })

  it('undoes page settings', () => {
    const before = dashboard(page([text('a', 0, 0)]))
    const canvas = { ...before.pages[0].canvas!, grid: { ...before.pages[0].canvas!.grid!, show: false } }
    roundTrip(before, [{ op: 'page.canvas.set', page_id: 'p', canvas }])
  })
})
