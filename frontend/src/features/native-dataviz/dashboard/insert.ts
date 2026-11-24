import type { ElementPatch, ElementStyle, GradientFill, IconElement, ImageFill, PageCanvas, ShapeElement, SolidFill } from '../contracts'
import { index } from './authoring'
import { chosen, type Command } from './commands'
import { absoluteBoxes, clampBox, columnEdges, frameGrid, pageSize, tabOrder, toGeometry, type Box } from './frame'
import type { Element, Page } from './layout'

/**
 * Commands that add items, format them and order them: the Insert tools, the
 * Format panel, the page background, Connect, and the selection pane's layer
 * and tab order. Like the Arrange commands, each returns one operation's
 * changes and the sentence announced for it.
 */

export type ShapeKind = NonNullable<ShapeElement['shape']>
export type IconName = IconElement['icon']
export type Token = SolidFill['token']
export type Fill = SolidFill | GradientFill | ImageFill

const FLAGS = { locked: false, hidden: false, decorative: false }
const none = (message: string): Command => ({ changes: null, message })

export const SHAPE_NAMES: Record<ShapeKind, string> = {
  rect: 'Rectangle', ellipse: 'Ellipse', line: 'Line', pill: 'Pill', triangle: 'Triangle',
}

/** Sizes for new items, in page pixels. A line is wide and thin; the rest are tiles. */
const SHAPE_SIZE: Record<ShapeKind, { w: number; h: number }> = {
  rect: { w: 240, h: 160 }, ellipse: { w: 160, h: 160 }, line: { w: 240, h: 16 }, pill: { w: 200, h: 48 }, triangle: { w: 160, h: 140 },
}
const ICON_SIZE = 48

/** Whether two boxes come closer than `gap` (touching counts when the gap is above zero). */
function crowds(a: Box, b: Box, gap: number): boolean {
  return a.x < b.x + b.w + gap && b.x < a.x + a.w + gap && a.y < b.y + b.h + gap && b.y < a.y + a.h + gap
}

/**
 * Where a new item goes: the first place inside the margins, scanning down
 * the page in baseline rows and across in column starts, that keeps a gutter
 * from everything already on the page. A full page puts it at the top-left
 * margin.
 */
export function freeSpot(page: Page, w: number, h: number): Box {
  const grid = frameGrid(page)
  const { width, height } = pageSize(page)
  const taken = [...absoluteBoxes(page.elements ?? []).values()]
  const starts = columnEdges(grid, width).starts.map(Math.round)
  const step = Math.max(grid.baseline, 8)
  for (let y = grid.margin; y + h <= height - grid.margin; y += step) {
    for (const x of starts) {
      if (x + w > width - grid.margin) break
      const box = { x, y, w, h }
      if (!taken.some(other => crowds(box, other, grid.gutter))) return box
    }
  }
  return clampBox({ x: grid.margin, y: grid.margin, w, h }, page)
}

function added(page: Page, element: Element, message: string): Command {
  return { changes: [{ op: 'elements.add', page_id: page.page_id, elements: [element] }], select: [element.element_id], message }
}

export function addShapeCommand(page: Page, shape: ShapeKind, id: string): Command {
  const size = SHAPE_SIZE[shape]
  const box = freeSpot(page, size.w, size.h)
  const count = (page.elements ?? []).filter(item => item.kind === 'shape' && (item.shape ?? 'rect') === shape).length
  // A line is drawn with its outline; a tile looks like a card (card fill, border,
  // card radius), so it reads on the page grid in both themes.
  const style: ElementStyle = shape === 'line'
    ? { stroke: { token: 'foreground', width: 2, dash: 'solid' } }
    : { fill: { kind: 'solid', token: 'card', opacity: 100 }, stroke: { token: 'border', width: 1, dash: 'solid' }, radius: shape === 'rect' ? 8 : 0 }
  const element = { kind: 'shape', element_id: id, name: `${SHAPE_NAMES[shape]} ${count + 1}`, ...FLAGS, decorative: true,
    geometry: toGeometry(box), shape, style } as Element
  return added(page, element, `Added a ${SHAPE_NAMES[shape].toLowerCase()} at x ${box.x}, y ${box.y}`)
}

/** A readable name from an icon's saved name: 'shopping-cart' is 'Shopping cart'. */
export function iconWords(icon: IconName): string {
  const words = icon.replace(/-\d+$/, '').replace(/-/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function addIconCommand(page: Page, icon: IconName, id: string): Command {
  const box = freeSpot(page, ICON_SIZE, ICON_SIZE)
  // An icon starts decorative: a label makes it something a screen reader announces.
  const element = { kind: 'icon', element_id: id, name: iconWords(icon), ...FLAGS, decorative: true,
    geometry: toGeometry(box), icon, label: '', style: { stroke: { token: 'foreground', width: 2, dash: 'solid' } } } as Element
  return added(page, element, `Added the ${iconWords(icon).toLowerCase()} icon at x ${box.x}, y ${box.y}`)
}

/**
 * A line from the first selected item to the second. Its ends are anchored to
 * the items, so it follows them when they move and goes when either is removed.
 */
export function connectCommand(page: Page, selection: readonly string[], id: string): Command {
  const items = chosen(page, selection).filter(item => item.element && item.element.kind !== 'connector' && item.element.kind !== 'annotation')
  if (items.length !== 2) return none('Select exactly two items to connect them')
  const boxes = absoluteBoxes(page.elements ?? [])
  const [a, b] = items.map(item => boxes.get(item.id)!)
  // The stored box only bounds the line for the selection pane; its path comes from the ends.
  const x = Math.min(a.x, b.x)
  const y = Math.min(a.y, b.y)
  const bounds = { x, y, w: Math.max(1, Math.max(a.x + a.w, b.x + b.w) - x), h: Math.max(1, Math.max(a.y + a.h, b.y + b.h) - y) }
  const count = (page.elements ?? []).filter(item => item.kind === 'connector').length
  const element = { kind: 'connector', element_id: id, name: `Line ${count + 1}`, ...FLAGS, decorative: true, geometry: toGeometry(bounds),
    start: { kind: 'element', element_id: items[0].id, side: 'auto' }, end: { kind: 'element', element_id: items[1].id, side: 'auto' },
    head: 'arrow', curve: 'straight', style: { stroke: { token: 'muted-foreground', width: 2, dash: 'solid' } } } as Element
  const names = items.map(item => itemName(item.element!))
  return added(page, element, `Connected ${names[0]} to ${names[1]}`)
}

/** Items a format change can apply to: not legacy spacers, and every kind but groups. */
function formattable(page: Page, selection: readonly string[]) {
  return chosen(page, selection).flatMap(item => item.element && item.element.kind !== 'group' ? [item.element] : [])
}

/** Merge a style change into every selected item (a group's style is its own frame, so groups count too). */
export function styleCommand(page: Page, selection: readonly string[], change: Partial<ElementStyle>, message: string): Command {
  const items = chosen(page, selection).flatMap(item => item.element ? [item.element] : [])
  if (!items.length) return none('Select an item to format it')
  return { changes: [{ op: 'elements.update', page_id: page.page_id,
    updates: items.map(item => ({ element_id: item.element_id, set: { style: { ...(item.style ?? {}), ...change } } })) }], message }
}

/** Set properties of one kind of item, such as a shape's outline or an icon's label. */
export function propertyCommand(page: Page, id: string, set: ElementPatch, message: string): Command {
  const element = formattable(page, [id])[0]
  if (!element) return none('Select an item to change it')
  return { changes: [{ op: 'elements.update', page_id: page.page_id, updates: [{ element_id: id, set }] }], message }
}

/**
 * Mark items decorative (skipped by screen readers and the keyboard) or not.
 * A decorative item also leaves the page's tab order, which may list only
 * items people can reach.
 */
export function decorativeCommand(page: Page, selection: readonly string[], decorative: boolean): Command {
  const items = formattable(page, selection)
  if (!items.length) return none('Select an item first')
  const ids = new Set(items.map(item => item.element_id))
  const changes: Command['changes'] & object = [{ op: 'elements.update', page_id: page.page_id,
    updates: items.map(item => ({ element_id: item.element_id, set: { decorative } })) }]
  const listed = page.tab_order ?? []
  if (decorative && listed.some(id => ids.has(id))) {
    changes.push({ op: 'page.tab_order.set', page_id: page.page_id, element_ids: listed.filter(id => !ids.has(id)) })
  }
  const what = items.length === 1 ? 'item is' : `${items.length} items are`
  return { changes, message: decorative ? `The ${what} now decorative and skipped by screen readers` : `The ${what} now read by screen readers` }
}

/** The page behind every item: a theme colour, a gradient between two, a picture, or none. */
export function backgroundCommand(page: Page, background: Fill | null): Command {
  const canvas: PageCanvas = { ...(page.canvas ?? {}), background }
  return { changes: [{ op: 'page.canvas.set', page_id: page.page_id, canvas }],
    message: background ? 'Page background changed' : 'Page background removed' }
}

/**
 * Move one top-level item to a place in the stack, counted from the front, as
 * the Layers list shows it. Items inside a group keep the group's order.
 */
export function layerCommand(page: Page, id: string, fromFront: number): Command {
  const order = (page.elements ?? []).map(item => item.element_id)
  const at = order.indexOf(id)
  if (at < 0) return none('Only items on the page, outside a group, can change stacking here')
  const target = order.length - 1 - Math.max(0, Math.min(order.length - 1, fromFront))
  if (target === at) return none(fromFront <= 0 ? 'Already at the front' : 'Already there')
  const next = order.filter(item => item !== id)
  next.splice(target, 0, id)
  const found = index(page).get(id)?.element
  const name = found ? itemName(found) : 'Item'
  return { changes: [{ op: 'elements.reorder', page_id: page.page_id, element_ids: next }],
    message: `${name} is now ${fromFront + 1} of ${order.length} from the front` }
}

/** Move one item in the order keyboard and screen-reader users reach items. */
export function tabOrderCommand(page: Page, id: string, position: number): Command {
  // Hidden items keep their place, so showing one again puts it back where it was.
  const order = tabOrder(page, { includeHidden: true }).map(item => item.element_id)
  const at = order.indexOf(id)
  if (at < 0) return none('Decorative items are not in the tab order')
  const target = Math.max(0, Math.min(order.length - 1, position))
  if (target === at) return none(target === 0 ? 'Already first' : target === order.length - 1 ? 'Already last' : 'Already there')
  const next = order.filter(item => item !== id)
  next.splice(target, 0, id)
  const found = index(page).get(id)?.element
  const name = found ? itemName(found) : 'Item'
  return { changes: [{ op: 'page.tab_order.set', page_id: page.page_id, element_ids: next }],
    message: `${name} is now ${target + 1} of ${order.length} in the tab order` }
}

/** Put the tab order back to reading order: top to bottom, then left to right. */
export function resetTabOrderCommand(page: Page): Command {
  if (!(page.tab_order ?? []).length) return none('The tab order already follows the page')
  return { changes: [{ op: 'page.tab_order.set', page_id: page.page_id, element_ids: [] }], message: 'The tab order follows the page again' }
}

/** Theme colours an item may use, by the names people see. */
export const TOKENS: { token: Token; label: string }[] = [
  { token: 'card', label: 'Card' }, { token: 'background', label: 'Page' }, { token: 'muted', label: 'Muted' },
  { token: 'accent', label: 'Accent' }, { token: 'brand', label: 'Brand' }, { token: 'primary', label: 'Primary' },
  { token: 'foreground', label: 'Text' }, { token: 'muted-foreground', label: 'Muted text' }, { token: 'border', label: 'Border' },
  { token: 'success', label: 'Success' }, { token: 'warning', label: 'Warning' }, { token: 'destructive', label: 'Danger' },
  ...Array.from({ length: 9 }, (_, n) => ({ token: `chart-${n + 1}` as Token, label: `Chart ${n + 1}` })),
]

const KIND_NAMES: Record<string, string> = {
  visual: 'Chart', filter: 'Filter', text: 'Text', image: 'Image', shape: 'Shape', icon: 'Icon',
  connector: 'Line', annotation: 'Note', group: 'Group',
}

/** What an item is called in the Layers and Tab order lists. */
export function itemName(element: Element): string {
  if (element.name) return element.name
  if ((element.kind === 'visual' || element.kind === 'text' || element.kind === 'filter' || element.kind === 'image') && element.title) return element.title
  if (element.kind === 'text') {
    const words = element.text?.runs?.map(run => 'text' in run ? run.text : '').join('').trim()
    if (words) return words.length > 40 ? `${words.slice(0, 40)}…` : words
  }
  return kindName(element)
}

/** What kind of item this is, in words: Chart, Text, Shape. */
export function kindName(element: Element): string {
  return KIND_NAMES[element.kind ?? ''] ?? 'Item'
}
