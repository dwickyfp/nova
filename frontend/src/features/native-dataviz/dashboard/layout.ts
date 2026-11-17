import type { DashboardDefinition } from '../contracts'

export type Page = DashboardDefinition['pages'][number]
export type Widget = NonNullable<Page['widgets']>[number]
export type Layout = Widget['layout']

export const COLUMNS = 12

function overlaps(a: Layout, b: Layout): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

/** The first top-left slot where a widget of this size fits without overlapping others. */
export function firstFreeSlot(widgets: readonly Widget[], w: number, h: number): Layout {
  const width = Math.max(2, Math.min(COLUMNS, w))
  const height = Math.max(2, Math.min(48, h))
  const bottom = widgets.reduce((max, widget) => Math.max(max, widget.layout.y + widget.layout.h), 0)
  for (let y = 0; y <= bottom; y++) {
    for (let x = 0; x + width <= COLUMNS; x++) {
      const candidate = { x, y, w: width, h: height }
      if (!widgets.some(widget => overlaps(candidate, widget.layout))) return candidate
    }
  }
  return { x: 0, y: bottom, w: width, h: height }
}

/**
 * The slot for a widget dropped on a cell: there when it fits, otherwise the
 * first free slot from that row down. Without a cell, the first free slot.
 */
export function slotAt(widgets: readonly Widget[], w: number, h: number, cell?: { x: number; y: number } | null): Layout {
  const width = Math.max(2, Math.min(COLUMNS, w))
  const height = Math.max(2, Math.min(48, h))
  if (!cell) return firstFreeSlot(widgets, width, height)
  const x = Math.max(0, Math.min(COLUMNS - width, Math.floor(cell.x)))
  const y = Math.max(0, Math.floor(cell.y))
  const exact = { x, y, w: width, h: height }
  if (!widgets.some(widget => overlaps(exact, widget.layout))) return exact
  const below = widgets.filter(widget => widget.layout.y + widget.layout.h > y)
    .map(widget => ({ ...widget, layout: { ...widget.layout, y: widget.layout.y - y } }))
  const slot = firstFreeSlot(below, width, height)
  return { ...slot, y: slot.y + y }
}

/** What a toolbar button carries when it is dragged onto the canvas. */
export type DropItem = { kind: 'chart'; visual: string } | { kind: 'text' }
export const DROP_TYPE = 'application/x-nova-dashboard-widget'
/** The item being dragged; drag-over events cannot read the payload itself. */
export const activeDrop: { item: DropItem | null } = { item: null }

export function dropSize(item: DropItem): { w: number; h: number } {
  return item.kind === 'text' ? DEFAULT_SIZES.text : sizeFor(item.visual)
}

/** Placements are valid when every widget fits the grid and none overlap. */
export function validPlacements(placements: { widget_id: string; layout: Layout }[]): boolean {
  return placements.every((item, index) => item.layout.x >= 0 && item.layout.w >= 2 && item.layout.h >= 2
    && item.layout.x + item.layout.w <= COLUMNS
    && placements.slice(0, index).every(other => !overlaps(item.layout, other.layout)))
}

export const DEFAULT_SIZES: Record<string, { w: number; h: number }> = {
  kpi: { w: 3, h: 2 }, gauge: { w: 3, h: 3 }, table: { w: 12, h: 5 }, pivot: { w: 12, h: 5 },
  text: { w: 12, h: 2 }, filter: { w: 3, h: 2 },
}

export function sizeFor(visual: string): { w: number; h: number } {
  return DEFAULT_SIZES[visual] ?? { w: 6, h: 4 }
}
