import type { ElementPlacement } from '../contracts'
import { absoluteBoxes, columnEdges, frameGrid, MIN_SIZE, pageSize, readingOrder, toGeometry, type Box, type Edges, type FrameGrid } from './frame'
import type { Element, Page } from './layout'

/**
 * Pure authoring geometry for the canvas editor: smart snapping with guides,
 * align, distribute, Auto-arrange and stacking order. Each produces boxes or
 * an order; the editor turns them into one operation.
 */

/** A guide drawn while dragging: a line at `at` across `from`..`to`, or a measured gap. */
export type Guide =
  | { kind: 'line'; axis: 'x' | 'y'; at: number; from: number; to: number }
  | { kind: 'gap'; axis: 'x' | 'y'; at: number; from: number; to: number; label: string }

export type SnapContext = {
  grid: FrameGrid
  width: number
  height: number
  /** Boxes of the items not being moved, in page pixels. */
  others: readonly Box[]
  snapGrid: boolean
  snapElements: boolean
  /** How close, in page pixels, a line pulls an edge onto it. */
  threshold: number
}

/** A line an edge snapped to; `column` lines are drawn by the grid already, so they get no guide. */
type Line = { at: number; target?: Box; column?: true }
type Hit = { delta: number; at: number; target?: Box; column?: true }

function closest(values: readonly number[], lines: readonly Line[], threshold: number): Hit | null {
  let best: Hit | null = null
  for (const value of values) {
    for (const line of lines) {
      const delta = line.at - value
      if (Math.abs(delta) <= threshold && (!best || Math.abs(delta) < Math.abs(best.delta))) best = { ...line, delta }
    }
  }
  return best
}

const onBaseline = (value: number, grid: FrameGrid) => grid.margin + Math.round((value - grid.margin) / grid.baseline) * grid.baseline

function lines(context: SnapContext, axis: 'x' | 'y') {
  const found: Line[] = []
  if (context.snapElements) {
    for (const other of context.others) {
      const [start, size] = axis === 'x' ? [other.x, other.w] : [other.y, other.h]
      found.push({ at: start, target: other }, { at: start + size / 2, target: other }, { at: start + size, target: other })
    }
    const extent = axis === 'x' ? context.width : context.height
    found.push({ at: extent / 2 }, { at: context.grid.margin }, { at: extent - context.grid.margin })
  }
  return found
}

/** The nearest neighbour on each side of a box, with the gap to it. */
function gaps(box: Box, others: readonly Box[]): Guide[] {
  const found: Guide[] = []
  const overlapsY = (other: Box) => other.y < box.y + box.h && box.y < other.y + other.h
  const overlapsX = (other: Box) => other.x < box.x + box.w && box.x < other.x + other.w
  const nearest = (candidates: Box[], gap: (other: Box) => number) =>
    candidates.map(other => ({ other, gap: gap(other) })).filter(item => item.gap > 0).sort((a, b) => a.gap - b.gap)[0]
  const left = nearest(others.filter(overlapsY), other => box.x - (other.x + other.w))
  const right = nearest(others.filter(overlapsY), other => other.x - (box.x + box.w))
  const above = nearest(others.filter(overlapsX), other => box.y - (other.y + other.h))
  const below = nearest(others.filter(overlapsX), other => other.y - (box.y + box.h))
  const midY = (other: Box) => (Math.max(box.y, other.y) + Math.min(box.y + box.h, other.y + other.h)) / 2
  const midX = (other: Box) => (Math.max(box.x, other.x) + Math.min(box.x + box.w, other.x + other.w)) / 2
  if (left) found.push({ kind: 'gap', axis: 'x', at: midY(left.other), from: left.other.x + left.other.w, to: box.x, label: `${Math.round(left.gap)}` })
  if (right) found.push({ kind: 'gap', axis: 'x', at: midY(right.other), from: box.x + box.w, to: right.other.x, label: `${Math.round(right.gap)}` })
  if (above) found.push({ kind: 'gap', axis: 'y', at: midX(above.other), from: above.other.y + above.other.h, to: box.y, label: `${Math.round(above.gap)}` })
  if (below) found.push({ kind: 'gap', axis: 'y', at: midX(below.other), from: box.y + box.h, to: below.other.y, label: `${Math.round(below.gap)}` })
  return found
}

function guideFor(axis: 'x' | 'y', hit: Hit, box: Box, context: SnapContext): Guide {
  const target = hit.target
  if (axis === 'x') {
    const from = target ? Math.min(target.y, box.y) : 0
    const to = target ? Math.max(target.y + target.h, box.y + box.h) : context.height
    return { kind: 'line', axis, at: hit.at, from, to }
  }
  const from = target ? Math.min(target.x, box.x) : 0
  const to = target ? Math.max(target.x + target.w, box.x + box.w) : context.width
  return { kind: 'line', axis, at: hit.at, from, to }
}

/**
 * Snap a moved or resized box. Edges and centres are pulled onto other items'
 * edges and centres, the page centre and margins (when snapping to items), and
 * onto column starts and ends (when snapping to the grid); an axis that found
 * no line lands on the baseline grid. Guides show what it snapped to and the
 * gaps to its neighbours.
 */
export function smartSnap(box: Box, context: SnapContext, mode: 'move' | { edges: Edges }): { box: Box; guides: Guide[] } {
  const { grid } = context
  const columns = context.snapGrid ? columnEdges(grid, context.width) : { starts: [], ends: [] }
  const xLines: Line[] = [...lines(context, 'x'), ...[...columns.starts, ...columns.ends].map(at => ({ at, column: true as const }))]
  const yLines = lines(context, 'y')
  const guides: Guide[] = []
  let next = { ...box }
  if (mode === 'move') {
    const hitX = closest([box.x, box.x + box.w / 2, box.x + box.w], xLines, context.threshold)
    const hitY = closest([box.y, box.y + box.h / 2, box.y + box.h], yLines, context.threshold)
    next.x = hitX ? box.x + hitX.delta : context.snapGrid ? onBaseline(box.x, grid) : box.x
    next.y = hitY ? box.y + hitY.delta : context.snapGrid ? onBaseline(box.y, grid) : box.y
    next = round(next)
    if (hitX && !hitX.column) guides.push(guideFor('x', hitX, next, context))
    if (hitY) guides.push(guideFor('y', hitY, next, context))
  } else {
    let left = box.x
    let top = box.y
    let right = box.x + box.w
    let bottom = box.y + box.h
    const snapEdge = (value: number, axisLines: typeof xLines, axis: 'x' | 'y', fallback: (v: number) => number) => {
      const hit = closest([value], axisLines, context.threshold)
      if (hit) { if (!hit.column) guides.push(guideFor(axis, hit, box, context)); return value + hit.delta }
      return context.snapGrid ? fallback(value) : value
    }
    if (mode.edges.includes('w')) left = snapEdge(left, xLines, 'x', value => onBaseline(value, grid))
    if (mode.edges.includes('e')) right = snapEdge(right, xLines, 'x', value => onBaseline(value, grid))
    if (mode.edges.includes('n')) top = snapEdge(top, yLines, 'y', value => onBaseline(value, grid))
    if (mode.edges.includes('s')) bottom = snapEdge(bottom, yLines, 'y', value => onBaseline(value + grid.gutter, grid) - grid.gutter)
    next = round({ x: left, y: top, w: Math.max(MIN_SIZE, right - left), h: Math.max(MIN_SIZE, bottom - top) })
  }
  return { box: next, guides: [...guides, ...gaps(next, context.others)] }
}

function round(box: Box): Box {
  return { x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.w), h: Math.round(box.h) }
}

/** The smallest box around some boxes. */
export function bounds(boxes: readonly Box[]): Box {
  const left = Math.min(...boxes.map(box => box.x))
  const top = Math.min(...boxes.map(box => box.y))
  const right = Math.max(...boxes.map(box => box.x + box.w))
  const bottom = Math.max(...boxes.map(box => box.y + box.h))
  return { x: left, y: top, w: right - left, h: bottom - top }
}

export type Alignment = 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom'

/** Line boxes up with each other, or one box with the page. */
export function align(boxes: ReadonlyMap<string, Box>, how: Alignment, page: Box): Map<string, Box> {
  const reference = boxes.size > 1 ? bounds([...boxes.values()]) : page
  const out = new Map<string, Box>()
  for (const [id, box] of boxes) {
    const next = { ...box }
    if (how === 'left') next.x = reference.x
    if (how === 'center') next.x = reference.x + (reference.w - box.w) / 2
    if (how === 'right') next.x = reference.x + reference.w - box.w
    if (how === 'top') next.y = reference.y
    if (how === 'middle') next.y = reference.y + (reference.h - box.h) / 2
    if (how === 'bottom') next.y = reference.y + reference.h - box.h
    out.set(id, round(next))
  }
  return out
}

/** Equal gaps between three or more boxes, keeping the first and last in place. */
export function distribute(boxes: ReadonlyMap<string, Box>, axis: 'x' | 'y'): Map<string, Box> {
  const entries = [...boxes].sort(([, a], [, b]) => axis === 'x' ? a.x - b.x || a.y - b.y : a.y - b.y || a.x - b.x)
  const out = new Map<string, Box>(entries.map(([id, box]) => [id, box]))
  if (entries.length < 3) return out
  const start = axis === 'x' ? entries[0][1].x : entries[0][1].y
  const last = entries[entries.length - 1][1]
  const end = axis === 'x' ? last.x + last.w : last.y + last.h
  const used = entries.reduce((sum, [, box]) => sum + (axis === 'x' ? box.w : box.h), 0)
  const gap = (end - start - used) / (entries.length - 1)
  let cursor = start
  for (const [id, box] of entries) {
    out.set(id, round(axis === 'x' ? { ...box, x: cursor } : { ...box, y: cursor }))
    cursor += (axis === 'x' ? box.w : box.h) + gap
  }
  return out
}

/** Kinds Auto-arrange re-flows; shapes, icons, lines and notes keep their place. */
const FLOWING = new Set(['visual', 'filter', 'text', 'image', 'group'])

/**
 * Re-flow content onto the page grid: each chart, filter, text, image or group
 * takes the nearest whole columns and baseline rows, then rises into the first
 * free space in reading order, so nothing overlaps. Locked items and items that
 * do not flow stay put; locked ones are obstacles. Returns null when the result
 * would not fit a fixed-height page.
 */
export function autoArrange(page: Page, only?: ReadonlySet<string>): ElementPlacement[] | null {
  const grid = frameGrid(page)
  const { width, height } = pageSize(page)
  const { starts } = columnEdges(grid, width)
  const step = starts.length > 1 ? starts[1] - starts[0] : width
  const column = step - grid.gutter
  const elements = page.elements ?? []
  const moving = elements.filter(item => FLOWING.has(item.kind ?? '') && !item.locked && (!only || only.has(item.element_id)))
  const fixed = elements.filter(item => !moving.includes(item) && FLOWING.has(item.kind ?? '')).map(item => item.geometry)
  const placed: Box[] = [...fixed]
  const placements: ElementPlacement[] = []
  const scrolls = page.canvas?.fit === 'width'
  for (const element of readingOrder(moving)) {
    const box = element.geometry
    const span = Math.max(1, Math.min(grid.columns, Math.round((box.w + grid.gutter) / step)))
    const first = Math.max(0, Math.min(grid.columns - span, Math.round((box.x - grid.margin) / step)))
    const x = Math.round(starts[first])
    const w = Math.round(span * column + (span - 1) * grid.gutter)
    const h = Math.max(MIN_SIZE, Math.round((box.h + grid.gutter) / grid.baseline) * grid.baseline - grid.gutter)
    let y = grid.margin
    // Rise to the first row below everything already placed in these columns.
    for (const other of placed) {
      if (other.x < x + w && x < other.x + other.w) y = Math.max(y, other.y + other.h + grid.gutter)
    }
    y = grid.margin + Math.ceil((y - grid.margin) / grid.baseline) * grid.baseline
    const next = { x, y, w, h }
    placed.push(next)
    if (!scrolls && y + h > height + 64) return null
    placements.push({ element_id: element.element_id, geometry: toGeometry(next, box.rotation ?? 0) })
  }
  return placements
}

export type Restack = 'front' | 'back' | 'forward' | 'backward'

/** The page's back-to-front order after moving the chosen top-level items. */
export function restack(order: readonly string[], chosen: ReadonlySet<string>, how: Restack): string[] {
  const picked = order.filter(id => chosen.has(id))
  const rest = order.filter(id => !chosen.has(id))
  if (how === 'front') return [...rest, ...picked]
  if (how === 'back') return [...picked, ...rest]
  const next = [...order]
  if (how === 'forward') {
    for (let index = next.length - 2; index >= 0; index--) {
      if (chosen.has(next[index]) && !chosen.has(next[index + 1])) [next[index], next[index + 1]] = [next[index + 1], next[index]]
    }
  } else {
    for (let index = 1; index < next.length; index++) {
      if (chosen.has(next[index]) && !chosen.has(next[index - 1])) [next[index], next[index - 1]] = [next[index - 1], next[index]]
    }
  }
  return next
}

/** Top-level boxes of a page, in page pixels, except the given ones. */
export function otherBoxes(page: Page, except: ReadonlySet<string>): Box[] {
  return (page.elements ?? []).filter(item => !except.has(item.element_id) && item.kind !== 'connector' && !item.hidden)
    .map(item => ({ x: item.geometry.x, y: item.geometry.y, w: item.geometry.w, h: item.geometry.h }))
}

/** Elements by id, at any depth, with their parent group (null on the page). */
export function index(page: Page): Map<string, { element: Element; parent: string | null; box: Box }> {
  const boxes = absoluteBoxes(page.elements ?? [])
  const out = new Map<string, { element: Element; parent: string | null; box: Box }>()
  const walk = (items: readonly Element[], parent: string | null) => {
    for (const item of items) {
      out.set(item.element_id, { element: item, parent, box: boxes.get(item.element_id)! })
      if (item.kind === 'group') walk(item.children, item.element_id)
    }
  }
  walk(page.elements ?? [], null)
  return out
}
