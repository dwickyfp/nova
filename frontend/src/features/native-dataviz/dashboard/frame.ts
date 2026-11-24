import type React from 'react'
import type { ConnectorElement, ElementStyle, Geometry, GradientFill, ImageFill, SolidFill, Stroke } from '../contracts'
import type { Element, Page } from './layout'

/**
 * Pure geometry for the canvas frame: page size and scale, reading and tab
 * order, the phone projection, snapping and connector paths. The renderer and
 * the editor share these so what is drawn and what is saved agree.
 */

/** Elements in the same row when their tops are this close (server: ``ROW_TOLERANCE``). */
export const ROW_TOLERANCE = 24
/** Narrower than this, a page is read as one column. */
export const PHONE_WIDTH = 520
/** The smallest element a pointer can resize to, in page pixels. */
export const MIN_SIZE = 24
/** A column edge within this many page pixels pulls a dragged edge onto it. */
export const SNAP_DISTANCE = 12

const PRESETS: Record<string, { width: number; height: number }> = {
  '16:9': { width: 1280, height: 720 }, '4:3': { width: 1024, height: 768 },
  'a4-landscape': { width: 1123, height: 794 }, 'letter-landscape': { width: 1056, height: 816 },
}
const DEFAULT_GRID = { columns: 12, gutter: 16, margin: 24, baseline: 8 }

export type Box = { x: number; y: number; w: number; h: number }
export type FrameGrid = { columns: number; gutter: number; margin: number; baseline: number }

export function pageSize(page: Page): { width: number; height: number } {
  const canvas = page.canvas
  const preset = canvas?.size && canvas.size !== 'custom' ? PRESETS[canvas.size] : undefined
  return preset ?? { width: canvas?.width ?? 1280, height: canvas?.height ?? 720 }
}

export function frameGrid(page: Page): FrameGrid {
  const grid = page.canvas?.grid
  return {
    columns: grid?.columns ?? DEFAULT_GRID.columns, gutter: grid?.gutter ?? DEFAULT_GRID.gutter,
    margin: grid?.margin ?? DEFAULT_GRID.margin, baseline: grid?.baseline ?? DEFAULT_GRID.baseline,
  }
}

/**
 * Screen pixels per page pixel. ``width`` fills the available width and
 * scrolls down; ``page`` shows the whole page; ``actual`` draws at its own size.
 */
export function scaleFor(page: Page, available: { width: number; height: number }): number {
  const { width, height } = pageSize(page)
  if (!(available.width > 0)) return 1
  const fit = page.canvas?.fit ?? 'page'
  if (fit === 'actual') return 1
  const across = available.width / width
  if (fit === 'width' || !(available.height > 0)) return across
  return Math.min(across, available.height / height)
}

/** Top to bottom, then left to right, rows within ``ROW_TOLERANCE`` (server: ``reading_order``). */
export function readingOrder<T extends { geometry: Geometry }>(elements: readonly T[]): T[] {
  let remaining = [...elements].sort((a, b) => a.geometry.y - b.geometry.y || a.geometry.x - b.geometry.x)
  const ordered: T[] = []
  while (remaining.length) {
    const top = remaining[0].geometry.y
    const row = remaining.filter(item => item.geometry.y - top <= ROW_TOLERANCE)
    ordered.push(...[...row].sort((a, b) => a.geometry.x - b.geometry.x || a.geometry.y - b.geometry.y))
    remaining = remaining.filter(item => !row.includes(item))
  }
  return ordered
}

/** The keyboard order: the page's explicit list first, then reading order (server: ``tab_order``). */
export function tabOrder(page: Page, { includeHidden = false } = {}): Element[] {
  const focusable = (page.elements ?? []).filter(item => !item.decorative && (includeHidden || !item.hidden))
  const listed = page.tab_order ?? []
  if (!listed.length) return readingOrder(focusable)
  const byId = new Map(focusable.map(item => [item.element_id, item]))
  const first = listed.map(id => byId.get(id)).filter((item): item is Element => Boolean(item))
  return [...first, ...readingOrder(focusable).filter(item => !listed.includes(item.element_id))]
}

/**
 * What a phone shows: content only (charts, filters, text, images and labelled
 * icons, also from inside groups), in the page's phone order, then reading order.
 */
export function mobileOrder(page: Page): Element[] {
  const hidden = new Set(page.mobile?.hidden ?? [])
  const readable: { element: Element; geometry: Geometry }[] = []
  const walk = (elements: readonly Element[], origin: { x: number; y: number }) => {
    for (const item of elements) {
      if (item.hidden || hidden.has(item.element_id)) continue
      const geometry = { ...item.geometry, x: origin.x + item.geometry.x, y: origin.y + item.geometry.y }
      if (item.kind === 'group') walk(item.children, geometry)
      else if (item.kind === 'visual' || item.kind === 'text' || item.kind === 'filter' || item.kind === 'image'
        || (item.kind === 'icon' && !item.decorative && Boolean(item.label))) readable.push({ element: item, geometry })
    }
  }
  walk(page.elements ?? [], { x: 0, y: 0 })
  const listed = page.mobile?.order ?? []
  const byId = new Map(readable.map(item => [item.element.element_id, item.element]))
  const first = listed.map(id => byId.get(id)).filter((item): item is Element => Boolean(item))
  return [...first, ...readingOrder(readable).map(item => item.element).filter(item => !listed.includes(item.element_id))]
}

/** A phone row's height: the element's own height, kept readable. */
export function mobileHeight(element: { geometry: Geometry }): number {
  return Math.round(Math.min(640, Math.max(120, element.geometry.h)))
}

/** Every element's box in page pixels, group children included (they are stored relative to the group). */
export function absoluteBoxes(elements: readonly Element[], origin = { x: 0, y: 0 }, into = new Map<string, Box>()): Map<string, Box> {
  for (const element of elements) {
    const box = { x: origin.x + element.geometry.x, y: origin.y + element.geometry.y, w: element.geometry.w, h: element.geometry.h }
    into.set(element.element_id, box)
    if (element.kind === 'group') absoluteBoxes(element.children, box, into)
  }
  return into
}

/** Column starts and ends of the page grid, in page pixels. */
export function columnEdges(grid: FrameGrid, width: number): { starts: number[]; ends: number[] } {
  const column = (width - 2 * grid.margin - (grid.columns - 1) * grid.gutter) / grid.columns
  const starts = Array.from({ length: grid.columns }, (_, index) => grid.margin + index * (column + grid.gutter))
  return { starts, ends: starts.map(start => start + column) }
}

function nearest(value: number, candidates: number[]): number | null {
  let best: number | null = null
  for (const candidate of candidates) {
    if (Math.abs(candidate - value) <= SNAP_DISTANCE && (best === null || Math.abs(candidate - value) < Math.abs(best - value))) best = candidate
  }
  return best
}

const onBaseline = (value: number, grid: FrameGrid) =>
  grid.margin + Math.round((value - grid.margin) / grid.baseline) * grid.baseline

/**
 * Snap a box to the page grid. A left edge near a column start (or a right
 * edge near a column end) lands on it; anything else lands on the baseline.
 * Tops land on baseline rows from the margin, and heights leave a gutter, so
 * a box on a 12-column page snaps back to exactly the cell it came from.
 */
export function snapBox(box: Box, grid: FrameGrid, width: number, mode: 'move' | { edges: Edges }): Box {
  const { starts, ends } = columnEdges(grid, width)
  const snapX = (value: number, lines: number[]) => nearest(value, lines) ?? onBaseline(value, grid)
  const snapBottom = (bottom: number) => onBaseline(bottom + grid.gutter, grid) - grid.gutter
  if (mode === 'move') {
    const startSnap = nearest(box.x, starts)
    const endSnap = nearest(box.x + box.w, ends)
    const x = startSnap ?? (endSnap !== null ? endSnap - box.w : onBaseline(box.x, grid))
    return { x: Math.round(x), y: Math.round(onBaseline(box.y, grid)), w: box.w, h: box.h }
  }
  let { x, y } = box
  let right = box.x + box.w
  let bottom = box.y + box.h
  if (mode.edges.includes('w')) x = snapX(x, starts)
  if (mode.edges.includes('e')) right = snapX(right, ends)
  if (mode.edges.includes('n')) y = onBaseline(y, grid)
  if (mode.edges.includes('s')) bottom = snapBottom(bottom)
  return { x: Math.round(x), y: Math.round(y), w: Math.round(Math.max(MIN_SIZE, right - x)), h: Math.round(Math.max(MIN_SIZE, bottom - y)) }
}

/** Resize handles: each names the edges it moves. */
export type Edges = 'n' | 'e' | 's' | 'w' | 'ne' | 'se' | 'sw' | 'nw'
export const HANDLES: Edges[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']

/** A box after moving the given edges by a page-pixel delta. */
export function resizeBox(box: Box, edges: Edges, dx: number, dy: number): Box {
  let { x, y } = box
  let right = box.x + box.w
  let bottom = box.y + box.h
  if (edges.includes('w')) x = Math.min(right - MIN_SIZE, x + dx)
  if (edges.includes('e')) right = Math.max(x + MIN_SIZE, right + dx)
  if (edges.includes('n')) y = Math.min(bottom - MIN_SIZE, y + dy)
  if (edges.includes('s')) bottom = Math.max(y + MIN_SIZE, bottom + dy)
  return { x, y, w: right - x, h: bottom - y }
}

/**
 * Keep a top-level box where the server accepts it: inside the page, with the
 * permitted overhang. A page that scrolls down has no bottom. A move slides
 * the box back inside; a resize trims the edge that crossed the page.
 */
export function clampBox(box: Box, page: Page, { nested = false, resize = false } = {}): Box {
  const round = (value: Box): Box => ({ x: Math.round(value.x), y: Math.round(value.y), w: Math.max(1, Math.round(value.w)), h: Math.max(1, Math.round(value.h)) })
  if (nested) return round(box)
  const { width, height } = pageSize(page)
  const scrolls = page.canvas?.fit === 'width'
  const bottom = scrolls ? Infinity : height
  if (resize) {
    const x = Math.max(0, box.x)
    const y = Math.max(0, box.y)
    const right = Math.min(width, box.x + box.w)
    const lower = Math.min(bottom, box.y + box.h)
    return round({ x, y, w: Math.max(MIN_SIZE, right - x), h: Math.max(MIN_SIZE, lower - y) })
  }
  const w = Math.min(box.w, width)
  const h = scrolls ? box.h : Math.min(box.h, height)
  return round({ x: Math.min(Math.max(box.x, 0), width - w), y: Math.min(Math.max(box.y, 0), bottom - h), w, h })
}

export type Arrow = 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown'

/**
 * One keyboard step. With snapping on, a horizontal move goes to the next
 * column start and a resize to the next column end; vertical steps are one
 * baseline row. Without snapping every step is one baseline unit.
 */
export function nudgeBox(box: Box, key: Arrow, resize: boolean, grid: FrameGrid, width: number, snap: boolean): Box {
  const sign = key === 'ArrowLeft' || key === 'ArrowUp' ? -1 : 1
  const horizontal = key === 'ArrowLeft' || key === 'ArrowRight'
  const { starts, ends } = columnEdges(grid, width)
  const next = (value: number, lines: number[]) => {
    const ahead = lines.map(line => Math.round(line)).filter(line => sign > 0 ? line > value : line < value)
    return ahead.length ? (sign > 0 ? Math.min(...ahead) : Math.max(...ahead)) : value + sign * grid.baseline
  }
  if (!resize) {
    if (!horizontal) return { ...box, y: box.y + sign * grid.baseline }
    return { ...box, x: snap ? next(box.x, starts) : box.x + sign * grid.baseline }
  }
  if (!horizontal) return { ...box, h: Math.max(MIN_SIZE, box.h + sign * grid.baseline) }
  const right = snap ? next(box.x + box.w, ends) : box.x + box.w + sign * grid.baseline
  return { ...box, w: Math.max(MIN_SIZE, right - box.x) }
}

/** The geometry a box saves as, keeping the element's rotation. */
export function toGeometry(box: Box, rotation: Geometry['rotation'] = 0): Geometry {
  return { x: Math.round(box.x), y: Math.round(box.y), w: Math.max(1, Math.round(box.w)), h: Math.max(1, Math.round(box.h)), rotation }
}

export function sameBox(a: Box, b: Box): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h
}

// ── Styles ─────────────────────────────────────────────────────────────

type Token = SolidFill['token']
const color = (token: Token, opacity = 100) => opacity >= 100
  ? `var(--${token})` : `color-mix(in oklab, var(--${token}) ${opacity}%, transparent)`

/** CSS for a fill, from theme tokens only. Image fills need an asset and are drawn by the caller. */
export function fillCss(fill: SolidFill | GradientFill | ImageFill | null | undefined): string | undefined {
  if (!fill) return undefined
  if ('start' in fill) return `linear-gradient(${fill.angle ?? 180}deg, ${color(fill.start)}, ${color(fill.end)})`
  if ('asset_id' in fill) return undefined
  return color(fill.token, fill.opacity ?? 100)
}

export function strokeColor(stroke: Stroke | null | undefined): string | undefined {
  return stroke && (stroke.width ?? 1) > 0 ? color(stroke.token) : undefined
}

export function dashArray(stroke: Stroke | null | undefined): string | undefined {
  const width = stroke?.width ?? 1
  return stroke?.dash === 'dash' ? `${width * 4} ${width * 3}` : stroke?.dash === 'dot' ? `${width} ${width * 2}` : undefined
}

const SHADOW = { none: undefined, sm: 'var(--shadow-sm, 0 1px 2px rgb(0 0 0 / 0.08))', md: 'var(--shadow-md, 0 4px 12px rgb(0 0 0 / 0.12))' } as const

/** The same shadows for drawn shapes (SVG), which follow the outline: an ellipse casts a round shadow. */
const DROP_SHADOW = { none: undefined, sm: 'drop-shadow(0 1px 2px rgb(0 0 0 / 0.08))', md: 'drop-shadow(0 4px 12px rgb(0 0 0 / 0.12))' } as const
export const dropShadowOf = (style: ElementStyle | undefined) => DROP_SHADOW[style?.shadow ?? 'none']

/**
 * Corner radius on the theme's scale: 8 is the theme's own card radius
 * (`--radius`), the others are in proportion and 9999 is a pill. Upcast
 * schema-2 cards (radius 8) therefore keep the rounding they always had, and a
 * dashboard follows the theme's roundness the way it follows its colours.
 */
export function themeRadius(radius: ElementStyle['radius']): string | undefined {
  if (!radius) return undefined
  if (radius >= 9999) return '9999px'
  return radius === 8 ? 'var(--radius)' : `calc(var(--radius) * ${radius / 8})`
}

export const opacityOf = (style: ElementStyle | undefined) =>
  style?.opacity !== undefined && style.opacity < 100 ? style.opacity / 100 : undefined

/** The box style for an element: fill, border, radius, shadow, opacity and padding. Content is laid out at its own size, so padding is too. */
export function boxStyle(style: ElementStyle | undefined): React.CSSProperties {
  const stroke = style?.stroke
  const width = stroke?.width ?? 1
  return {
    background: fillCss(style?.fill),
    border: stroke && width > 0 ? `${width}px ${stroke.dash === 'dash' ? 'dashed' : stroke.dash === 'dot' ? 'dotted' : 'solid'} ${strokeColor(stroke)}` : undefined,
    borderRadius: themeRadius(style?.radius),
    boxShadow: SHADOW[style?.shadow ?? 'none'],
    opacity: opacityOf(style),
    padding: style?.padding || undefined,
  }
}

// ── Connectors ─────────────────────────────────────────────────────────

type Point = { x: number; y: number }
type Side = 'n' | 'e' | 's' | 'w'

function sideOf(box: Box, toward: Point): Side {
  const cx = box.x + box.w / 2
  const cy = box.y + box.h / 2
  const dx = (toward.x - cx) / Math.max(1, box.w)
  const dy = (toward.y - cy) / Math.max(1, box.h)
  return Math.abs(dx) >= Math.abs(dy) ? (dx >= 0 ? 'e' : 'w') : (dy >= 0 ? 's' : 'n')
}

function anchorOn(box: Box, side: Side): Point {
  switch (side) {
    case 'n': return { x: box.x + box.w / 2, y: box.y }
    case 's': return { x: box.x + box.w / 2, y: box.y + box.h }
    case 'e': return { x: box.x + box.w, y: box.y + box.h / 2 }
    case 'w': return { x: box.x, y: box.y + box.h / 2 }
  }
}

type End = ConnectorElement['start']

function centre(end: End, boxes: Map<string, Box>): Point | null {
  if ('x' in end) return { x: end.x, y: end.y }
  if (!('element_id' in end)) return null
  const box = boxes.get(end.element_id)
  return box ? { x: box.x + box.w / 2, y: box.y + box.h / 2 } : null
}

function endpoint(end: End, other: Point, boxes: Map<string, Box>): { point: Point; side: Side | null } | null {
  if ('x' in end) return { point: { x: end.x, y: end.y }, side: null }
  // A data point is located by its chart; until it is resolved the line is not drawn.
  if (!('element_id' in end)) return null
  const box = boxes.get(end.element_id)
  if (!box) return null
  const side = !end.side || end.side === 'auto' ? sideOf(box, other) : end.side
  return { point: anchorOn(box, side), side }
}

/**
 * The SVG path of a connector in page pixels, or null when an end cannot be
 * located (a missing element or an unresolved data point): such a line is
 * hidden rather than guessed.
 */
export function connectorPath(connector: ConnectorElement, boxes: Map<string, Box>): { d: string; start: Point; end: Point; angle: number } | null {
  const startCentre = centre(connector.start, boxes)
  const endCentre = centre(connector.end, boxes)
  if (!startCentre || !endCentre) return null
  const a = endpoint(connector.start, endCentre, boxes)
  const b = endpoint(connector.end, startCentre, boxes)
  if (!a || !b) return null
  const p = a.point
  const q = b.point
  let d: string
  let approach: Point = p
  const curve = connector.curve ?? 'straight'
  if (curve === 'elbow') {
    const horizontal = a.side ? a.side === 'e' || a.side === 'w' : Math.abs(q.x - p.x) >= Math.abs(q.y - p.y)
    const mid = horizontal ? { x: (p.x + q.x) / 2, y: p.y } : { x: p.x, y: (p.y + q.y) / 2 }
    const turn = horizontal ? { x: mid.x, y: q.y } : { x: q.x, y: mid.y }
    d = `M ${p.x} ${p.y} L ${mid.x} ${mid.y} L ${turn.x} ${turn.y} L ${q.x} ${q.y}`
    approach = turn
  }
  else if (curve === 'arc') {
    const mx = (p.x + q.x) / 2
    const my = (p.y + q.y) / 2
    const bend = 0.25
    const control = { x: mx - (q.y - p.y) * bend, y: my + (q.x - p.x) * bend }
    d = `M ${p.x} ${p.y} Q ${control.x} ${control.y} ${q.x} ${q.y}`
    approach = control
  }
  else d = `M ${p.x} ${p.y} L ${q.x} ${q.y}`
  const angle = Math.atan2(q.y - approach.y, q.x - approach.x) * 180 / Math.PI
  return { d, start: p, end: q, angle }
}

/** An element at any depth. */
export function findElement(elements: readonly Element[], id: string): Element | undefined {
  for (const item of elements) {
    if (item.element_id === id) return item
    if (item.kind === 'group') {
      const found = findElement(item.children, id)
      if (found) return found
    }
  }
  return undefined
}

/** Every item on a page, grouped ones included, as the page cap counts them. */
export function countElements(elements: readonly Element[]): number {
  return elements.reduce((total, item) => total + 1 + (item.kind === 'group' ? countElements(item.children) : 0), 0)
}
