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
