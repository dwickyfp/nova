import type { Geometry } from '../contracts'
import { align, autoArrange, distribute, index, restack, type Alignment, type Restack } from './authoring'
import { clampBox, frameGrid, nudgeBox, pageSize, sameBox, toGeometry, type Arrow, type Box } from './frame'
import type { Element, Page } from './layout'
import type { Change } from './operations'

/**
 * Editor commands on the current selection, as the changes of one operation.
 * Each returns null when it does not apply, with a sentence that says why, so
 * the toolbar, the keyboard and the screen-reader announcement agree.
 */
export type Command = { changes: Change[]; select?: string[]; message: string } | { changes: null; message: string }

type Item = { id: string; element: Element | null; parent: string | null; geometry: Geometry; locked: boolean }

/** The selected items that exist on the page, with their parent group. */
export function chosen(page: Page, selection: readonly string[]): Item[] {
  const found = index(page)
  const spacers = page.legacy_spacers ?? []
  return selection.flatMap((id): Item[] => {
    const hit = found.get(id)
    if (hit) return [{ id, element: hit.element, parent: hit.parent, geometry: hit.element.geometry, locked: hit.element.locked ?? false }]
    const spacer = spacers.find(item => item.element_id === id)
    return spacer ? [{ id, element: null, parent: null, geometry: spacer.geometry, locked: spacer.locked ?? false }] : []
  })
}

const none = (message: string): Command => ({ changes: null, message })
const plural = (count: number, one: string) => `${count} ${one}${count === 1 ? '' : 's'}`

/** Items that move together: unlocked, in the first item's group. */
function movable(items: Item[]): Item[] {
  const parent = items[0]?.parent ?? null
  return items.filter(item => item.parent === parent && !item.locked)
}

/** The box items align with when there is only one: the page, or their group. */
function container(page: Page, parent: string | null): Box {
  if (parent) {
    const group = index(page).get(parent)?.element.geometry
    if (group) return { x: 0, y: 0, w: group.w, h: group.h }
  }
  const { width, height } = pageSize(page)
  return { x: 0, y: 0, w: width, h: height }
}

function arrange(page: Page, items: Item[], boxes: Map<string, Box>, message: string): Command {
  const placements = items.flatMap(item => {
    const box = boxes.get(item.id)
    if (!box) return []
    const clamped = clampBox(box, page, { nested: item.parent !== null })
    return sameBox(clamped, item.geometry) ? [] : [{ element_id: item.id, geometry: toGeometry(clamped, item.geometry.rotation ?? 0) }]
  })
  if (!placements.length) return none('Nothing needed to move')
  return { changes: [{ op: 'elements.arrange', page_id: page.page_id, placements }], message }
}

const ALIGN_WORDS: Record<Alignment, string> = {
  left: 'left edges', center: 'centres', right: 'right edges', top: 'tops', middle: 'middles', bottom: 'bottoms',
}

export function alignCommand(page: Page, selection: readonly string[], how: Alignment): Command {
  const items = movable(chosen(page, selection))
  if (!items.length) return none('Select an unlocked item to align')
  const boxes = align(new Map(items.map(item => [item.id, item.geometry])), how, container(page, items[0].parent))
  const target = items.length > 1 ? `Aligned the ${ALIGN_WORDS[how]} of ${items.length} items` : `Aligned to the ${items[0].parent ? 'group' : 'page'}`
  return arrange(page, items, boxes, target)
}

export function distributeCommand(page: Page, selection: readonly string[], axis: 'x' | 'y'): Command {
  const items = movable(chosen(page, selection))
  if (items.length < 3) return none('Select three or more unlocked items to space them evenly')
  const boxes = distribute(new Map(items.map(item => [item.id, item.geometry])), axis)
  return arrange(page, items, boxes, `Spaced ${items.length} items evenly ${axis === 'x' ? 'across' : 'down'}`)
}

export function autoArrangeCommand(page: Page, selection: readonly string[]): Command {
  const top = chosen(page, selection).filter(item => item.parent === null && item.element)
  const only = top.length > 1 ? new Set(top.map(item => item.id)) : undefined
  const placements = autoArrange(page, only)
  if (placements === null) return none('These items do not fit on the page in columns. Make the page taller or arrange fewer items.')
  const current = new Map((page.elements ?? []).map(item => [item.element_id, item.geometry]))
  const changed = placements.filter(item => !sameBox(item.geometry, current.get(item.element_id)!))
  if (!changed.length) return none('Everything is already on the grid')
  return { changes: [{ op: 'elements.arrange', page_id: page.page_id, placements: changed }], message: `Arranged ${plural(changed.length, 'item')} on the grid` }
}

const STACK_WORDS: Record<Restack, string> = { front: 'to the front', back: 'to the back', forward: 'forward', backward: 'backward' }

export function restackCommand(page: Page, selection: readonly string[], how: Restack): Command {
  const top = new Set(chosen(page, selection).filter(item => item.parent === null && item.element).map(item => item.id))
  if (!top.size) return none('Select an item on the page, outside a group, to change its stacking')
  const order = (page.elements ?? []).map(item => item.element_id)
  const next = restack(order, top, how)
  if (next.every((id, position) => id === order[position])) return none(`Already ${how === 'front' || how === 'forward' ? 'in front' : 'at the back'}`)
  return { changes: [{ op: 'elements.reorder', page_id: page.page_id, element_ids: next }], message: `Moved ${plural(top.size, 'item')} ${STACK_WORDS[how]}` }
}

export function groupCommand(page: Page, selection: readonly string[], groupId: string): Command {
  const top = chosen(page, selection).filter(item => item.parent === null && item.element)
  if (top.length < 2) return none('Select two or more items on the page to group them')
  const count = (page.elements ?? []).filter(item => item.kind === 'group').length
  return { changes: [{ op: 'elements.group', page_id: page.page_id, group_id: groupId, element_ids: top.map(item => item.id), name: `Group ${count + 1}` }],
    select: [groupId], message: `Grouped ${top.length} items` }
}

export function ungroupCommand(page: Page, selection: readonly string[]): Command {
  const groups = chosen(page, selection).filter(item => item.parent === null && item.element?.kind === 'group')
  if (!groups.length) return none('Select a group to ungroup it')
  const children = groups.flatMap(item => item.element?.kind === 'group' ? item.element.children.map(child => child.element_id) : [])
  return { changes: groups.map(item => ({ op: 'elements.ungroup' as const, page_id: page.page_id, group_id: item.id })), select: children,
    message: `Ungrouped ${plural(children.length, 'item')}` }
}

/** Lock or hide the selection; if every item already is, unlock or show it. */
export function toggleCommand(page: Page, selection: readonly string[], flag: 'locked' | 'hidden'): Command {
  const items = chosen(page, selection).filter(item => item.element)
  if (!items.length) return none('Select an item first')
  const on = !items.every(item => item.element?.[flag])
  const words = flag === 'locked' ? on ? 'Locked' : 'Unlocked' : on ? 'Hid' : 'Showed'
  return { changes: [{ op: 'elements.update', page_id: page.page_id, updates: items.map(item => ({ element_id: item.id, set: { [flag]: on } })) }],
    message: `${words} ${plural(items.length, 'item')}` }
}

/** Remove the selection (a group's child goes with its group when both are chosen). */
export function removeCommand(page: Page, selection: readonly string[]): Command {
  const items = chosen(page, selection)
  if (!items.length) return none('Select an item first')
  const ids = new Set(items.map(item => item.id))
  const found = index(page)
  const inside = (id: string): boolean => {
    const parent = found.get(id)?.parent ?? null
    return parent !== null && (ids.has(parent) || inside(parent))
  }
  const removed = items.filter(item => !inside(item.id)).map(item => item.id)
  // An item removed from inside a group cannot be put back in its group, so Undo cannot restore it.
  const nested = removed.some(id => found.get(id)?.parent)
  return { changes: [{ op: 'elements.remove', page_id: page.page_id, element_ids: removed }], select: [],
    message: `Removed ${plural(removed.length, 'item')}${nested ? '. Removing items from inside a group cannot be undone' : ''}` }
}

/** Copies of the selected top-level items, a little below and right, with new IDs. */
export function duplicateCommand(page: Page, selection: readonly string[], newId: () => string): Command {
  const top = chosen(page, selection).filter(item => item.parent === null && item.element)
  if (!top.length) return none('Select an item on the page to duplicate it')
  const ids = new Map<string, string>()
  const remember = (element: Element) => {
    ids.set(element.element_id, newId())
    if (element.kind === 'group') element.children.forEach(remember)
  }
  top.forEach(item => remember(item.element!))
  const offset = frameGrid(page).baseline * 2
  const copy = (element: Element, outer: boolean): Element | null => {
    const moved = outer ? clampBox({ ...element.geometry, x: element.geometry.x + offset, y: element.geometry.y + offset }, page) : element.geometry
    const base = { ...element, element_id: ids.get(element.element_id)!, geometry: { ...element.geometry, ...moved } }
    if (base.kind === 'group') {
      const children = base.children.map(child => copy(child, false)).filter((child): child is Element => Boolean(child))
      return children.length ? { ...base, children: children as typeof base.children } : null
    }
    if (base.kind === 'connector') {
      // A copied line joins the copies of its ends, or keeps an end it was not copied with.
      const end = (point: typeof base.start) => 'element_id' in point ? { ...point, element_id: ids.get(point.element_id) ?? point.element_id } : point
      return { ...base, start: end(base.start), end: end(base.end) }
    }
    if (base.kind === 'annotation') return null
    return base
  }
  const elements = top.map(item => copy(item.element!, true)).filter((item): item is Element => Boolean(item))
  if (!elements.length) return none('Notes on data points cannot be duplicated yet')
  return { changes: [{ op: 'elements.add', page_id: page.page_id, elements }], select: elements.map(item => item.element_id),
    message: `Duplicated ${plural(elements.length, 'item')}` }
}

/**
 * One keyboard step for the selection: Alt+arrow moves (to the next column
 * start, or one baseline row), Alt+Shift+arrow resizes one item.
 */
export function nudgeCommand(page: Page, selection: readonly string[], key: Arrow, resize: boolean): Command {
  const all = chosen(page, selection)
  if (!all.length) return none('Select an item first')
  const items = movable(all)
  if (!items.length) return none('This item is locked')
  const snap = page.canvas?.grid?.snap ?? true
  const grid = frameGrid(page)
  const { width } = pageSize(page)
  if (resize) {
    if (items.length > 1) return none('Select one item to resize it')
    const item = items[0]
    const box = clampBox(nudgeBox(item.geometry, key, true, grid, width, snap), page, { nested: item.parent !== null, resize: true })
    if (sameBox(box, item.geometry)) return none('It cannot go further that way')
    return { changes: [{ op: 'elements.arrange', page_id: page.page_id, placements: [{ element_id: item.id, geometry: toGeometry(box, item.geometry.rotation ?? 0) }] }],
      message: `Resized to x ${box.x}, y ${box.y}, ${box.w} by ${box.h} pixels` }
  }
  // The first item sets the step; the others keep their distance to it.
  const lead = items[0]
  const leadBox = clampBox(nudgeBox(lead.geometry, key, false, grid, width, snap), page, { nested: lead.parent !== null })
  const dx = leadBox.x - lead.geometry.x
  const dy = leadBox.y - lead.geometry.y
  if (!dx && !dy) return none('It cannot go further that way')
  const placements = items.map(item => {
    const box = clampBox({ ...item.geometry, x: item.geometry.x + dx, y: item.geometry.y + dy }, page, { nested: item.parent !== null })
    return { element_id: item.id, geometry: toGeometry(box, item.geometry.rotation ?? 0) }
  })
  return { changes: [{ op: 'elements.arrange', page_id: page.page_id, placements }],
    message: items.length > 1 ? `Moved ${items.length} items` : `Moved to x ${leadBox.x}, y ${leadBox.y}, ${leadBox.w} by ${leadBox.h} pixels` }
}
