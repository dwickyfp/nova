import type {
  CanvasGrid, DashboardDefinition, DashboardDefinitionV2, ElementStyle, Geometry, Layout, PageV2,
  WidgetAppearance,
} from '../contracts'

/**
 * Schema-3 pages are a free canvas, drawn by the frame renderer (canvas.tsx,
 * frame.ts). This module mirrors the server's upcaster
 * (backend/app/modules/viz/canvas.py) exactly, and keeps the 12-column cell
 * view that grid-speaking callers (adding, duplicating, replacing widgets and
 * the export) still use; the server applies those changes to the canvas.
 */
export type Page = DashboardDefinition['pages'][number]
export type Element = NonNullable<Page['elements']>[number]
export type Widget = NonNullable<PageV2['widgets']>[number]
export type { Layout }

export const COLUMNS = 12
export const LEGACY_WIDTH = 1200
export const LEGACY_ROW = 72
export const LEGACY_GRID: Required<Pick<CanvasGrid, 'columns' | 'gutter' | 'margin' | 'baseline'>> = {
  columns: 12, gutter: 12, margin: 6, baseline: 8,
}
const DEFAULT_GRID = { columns: 12, gutter: 16, margin: 24, baseline: 8 }
const PADDING = { none: 0, compact: 12, comfortable: 16 } as const

type Grid = { columns: number; gutter: number; margin: number }

function gridOf(page: Page): { grid: Grid; width: number } {
  const grid = { ...DEFAULT_GRID, ...page.canvas?.grid }
  return { grid, width: page.canvas?.width ?? 1280 }
}

function step(grid: Grid, width: number): number {
  const column = (width - 2 * grid.margin - (grid.columns - 1) * grid.gutter) / grid.columns
  return (column + grid.gutter) * grid.columns / COLUMNS
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))
/** Python's round(): halves go to the even neighbour. */
function roundHalfEven(value: number): number {
  const floor = Math.floor(value)
  const diff = value - floor
  if (Math.abs(diff - 0.5) > 1e-9) return Math.round(value)
  return floor % 2 === 0 ? floor : floor + 1
}

/** Canvas pixels for a 12-column cell on a page's grid. */
export function cellRect(layout: Layout, grid: Grid = LEGACY_GRID, width = LEGACY_WIDTH): Geometry {
  const pitch = step(grid, width)
  return {
    x: roundHalfEven(grid.margin + layout.x * pitch), w: roundHalfEven(layout.w * pitch - grid.gutter),
    y: grid.margin + layout.y * LEGACY_ROW, h: layout.h * LEGACY_ROW - grid.gutter, rotation: 0,
  }
}

/** The nearest 12-column cell; exact for anything ``cellRect`` produced. */
export function cellOf(geometry: Geometry, grid: Grid = LEGACY_GRID, width = LEGACY_WIDTH): Layout {
  const pitch = step(grid, width)
  const w = clamp(roundHalfEven((geometry.w + grid.gutter) / pitch), 2, COLUMNS)
  const x = clamp(roundHalfEven((geometry.x - grid.margin) / pitch), 0, COLUMNS - w)
  const h = clamp(roundHalfEven((geometry.h + grid.gutter) / LEGACY_ROW), 2, 48)
  const y = clamp(roundHalfEven((geometry.y - grid.margin) / LEGACY_ROW), 0, 10_000)
  return { x, y, w, h }
}

export function styleOf(appearance: WidgetAppearance = {}): ElementStyle {
  return {
    fill: (appearance.surface ?? 'card') === 'card' ? { kind: 'solid', token: 'card', opacity: 100 } : null,
    stroke: (appearance.border ?? true) ? { token: 'surface-border', width: 1, dash: 'solid' } : null,
    radius: 8, shadow: 'none', opacity: 100, padding: PADDING[appearance.padding ?? 'compact'],
  }
}

export function appearanceOf(style: ElementStyle = {}): WidgetAppearance {
  const padding = (Object.keys(PADDING) as (keyof typeof PADDING)[]).find(key => PADDING[key] === (style.padding ?? 0))
  return { surface: style.fill ? 'card' : 'transparent', border: Boolean(style.stroke), padding: padding ?? 'compact' }
}

/** One element as a grid widget, or null for kinds a grid cannot show. */
export function widgetOf(element: Element, grid: Grid = LEGACY_GRID, width = LEGACY_WIDTH): Widget | null {
  const base = {
    widget_id: element.element_id, layout: cellOf(element.geometry, grid, width),
    locked: element.locked ?? false, appearance: appearanceOf(element.style),
  }
  switch (element.kind) {
    case 'visual':
      return { ...base, kind: 'chart', title: element.title ?? '', chart_ref: element.chart_ref,
        style: element.overrides ?? {}, filters: element.filters ?? null }
    case 'text': {
      const text = (element.text?.runs ?? []).map(run => 'text' in run ? run.text : '').join('')
      return { ...base, kind: 'text', title: element.title ?? '', text, style: element.role === 'title' ? 'title' : 'body' }
    }
    case 'image':
      return { ...base, kind: 'image', title: element.title ?? '', asset_id: element.asset_id, alt: element.alt, fit: element.fit }
    case 'filter':
      return { ...base, kind: 'filter', title: element.title ?? '', filter_id: element.filter_id }
    default:
      return null
  }
}

/** The page as grid widgets, in stacking order, then spacers (server: ``legacy_widgets``). */
export function gridWidgets(page: Page): Widget[] {
  const { grid, width } = gridOf(page)
  const widgets: Widget[] = []
  for (const element of page.elements ?? []) {
    const widget = widgetOf(element, grid, width)
    if (widget) widgets.push(widget)
  }
  for (const spacer of page.legacy_spacers ?? []) {
    widgets.push({ widget_id: spacer.element_id, layout: cellOf(spacer.geometry, grid, width), kind: 'spacer',
      title: spacer.title ?? '', locked: spacer.locked ?? false, appearance: appearanceOf(spacer.style) })
  }
  return widgets
}

const ELEMENT_DEFAULTS = { name: '', locked: false, hidden: false, decorative: false }

/** A grid widget as a canvas element on a page's grid (server: ``upcast_widget``). */
export function upcastWidget(widget: Widget, grid: Grid = LEGACY_GRID, width = LEGACY_WIDTH): Element | null {
  const base = {
    ...ELEMENT_DEFAULTS, element_id: widget.widget_id, geometry: cellRect(widget.layout, grid, width),
    locked: widget.locked ?? false, style: styleOf(widget.appearance),
  }
  switch (widget.kind) {
    case 'chart':
      return { ...base, kind: 'visual', title: widget.title ?? '', chart_ref: widget.chart_ref,
        overrides: widget.style ?? {}, filters: widget.filters ?? null }
    case 'text':
      return { ...base, kind: 'text', title: widget.title ?? '', role: widget.style ?? 'body',
        text: { runs: [{ kind: 'text', text: widget.text, weight: null }] } }
    case 'image':
      return { ...base, kind: 'image', title: widget.title ?? '', asset_id: widget.asset_id, alt: widget.alt, fit: widget.fit }
    case 'filter':
      return { ...base, kind: 'filter', title: widget.title ?? '', filter_id: widget.filter_id }
    default:
      return null
  }
}

/** A grid page as a scrolling canvas page (server: ``upcast_page``). */
export function upcastPage(page: PageV2): Page {
  const widgets = page.widgets ?? []
  const bottom = widgets.reduce((max, widget) => Math.max(max, widget.layout.y + widget.layout.h), 0)
  return {
    page_id: page.page_id, title: page.title,
    canvas: { size: 'custom', width: LEGACY_WIDTH, height: Math.max(720, bottom * LEGACY_ROW), fit: 'width',
      background: null, grid: { ...LEGACY_GRID, show: true, snap: true, snap_to_elements: true } },
    elements: widgets.map(widget => upcastWidget(widget)).filter((item): item is Element => item !== null),
    tab_order: [], mobile: { order: [], hidden: [] },
    legacy_spacers: widgets.filter(widget => widget.kind === 'spacer').map(widget => ({
      element_id: widget.widget_id, geometry: cellRect(widget.layout), title: widget.title ?? '',
      locked: widget.locked ?? false, style: styleOf(widget.appearance),
    })),
  }
}

/** A grid dashboard as schema 3 (server: ``upcast_dashboard``). */
export function upcastDashboard(document: DashboardDefinitionV2): DashboardDefinition {
  return {
    schema_version: 3, dashboard_id: document.dashboard_id, title: document.title,
    description: document.description ?? '', pages: document.pages.map(upcastPage),
    datasets: document.datasets ?? [], filters: document.filters ?? [], actions: document.actions ?? [],
  }
}

/** The page grid used to place and read cells. */
export function pageGrid(page: Page) {
  return gridOf(page)
}

/** Fit-width pages grow to hold their lowest element, like the server. */
export function grow(page: Page): Page {
  const canvas = page.canvas
  if (!canvas || canvas.fit !== 'width') return page
  const { grid } = gridOf(page)
  const boxes = [...(page.elements ?? []), ...(page.legacy_spacers ?? [])].map(item => item.geometry)
  const bottom = boxes.reduce((max, box) => Math.max(max, box.y + box.h), 0)
  return { ...page, canvas: { ...canvas, height: Math.min(750_000, Math.max(canvas.height ?? 720, bottom + grid.margin)) } }
}

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
