import type { DashboardDefinition, Geometry, Operation } from '../contracts'
import { appearanceOf, cellOf, cellRect, gridWidgets, grow, pageGrid, styleOf, upcastPage, upcastWidget, type Element, type Layout, type Page, type Widget } from './layout'

export type Change = Operation['changes'][number]

/** The change that reverts one applied change against the definition it was applied to. */
export function inverse(change: Change, before: DashboardDefinition): Change | null {
  const page = (id: string) => before.pages.find(item => item.page_id === id)
  const widget = (pageId: string, id: string) => {
    const current = page(pageId)
    return current ? gridWidgets(current).find(item => item.widget_id === id) : undefined
  }
  switch (change.op) {
    case 'layout.apply': {
      const current = page(change.page_id)
      if (!current) return null
      return { op: 'layout.apply', page_id: change.page_id, placements: gridWidgets(current).map(item =>
        ({ widget_id: item.widget_id, layout: item.layout })) }
    }
    case 'widget.add':
      return { op: 'widget.remove', page_id: change.page_id, widget_id: change.widget.widget_id }
    case 'widget.remove': {
      const removed = widget(change.page_id, change.widget_id)
      return removed ? { op: 'widget.add', page_id: change.page_id, widget: removed } : null
    }
    case 'widget.replace': {
      const replaced = widget(change.page_id, change.widget.widget_id)
      return replaced ? { op: 'widget.replace', page_id: change.page_id, widget: replaced } : null
    }
    case 'filters.replace':
      return { op: 'filters.replace', filters: before.filters ?? [] }
    case 'actions.replace':
      return { op: 'actions.replace', actions: before.actions ?? [] }
    case 'dashboard.rename':
      return { op: 'dashboard.rename', title: before.title }
    case 'page.add':
      return { op: 'page.remove', page_id: change.page.page_id }
    case 'page.rename': {
      const current = page(change.page_id)
      return current ? { op: 'page.rename', page_id: change.page_id, title: current.title } : null
    }
    default:
      return null
  }
}

/** An unchanged cell keeps the element's exact geometry; a moved one snaps to the cell. */
function keepOrCell(page: Page, current: Geometry | undefined, layout: Layout): Geometry {
  const { grid, width } = pageGrid(page)
  if (current) {
    const cell = cellOf(current, grid, width)
    if (cell.x === layout.x && cell.y === layout.y && cell.w === layout.w && cell.h === layout.h) return current
  }
  return cellRect(layout, grid, width)
}

function placed(page: Page, widget: Widget, current?: Element): Element | null {
  const { grid, width } = pageGrid(page)
  const element = upcastWidget(widget, grid, width)
  if (!element) return null
  const geometry = keepOrCell(page, current?.geometry, widget.layout)
  if (!current) return { ...element, geometry }
  // Canvas-only properties a grid client cannot express are kept, as on the server.
  const before = appearanceOf(current.style)
  const after = appearanceOf(styleOf(widget.appearance))
  const sameLook = before.surface === after.surface && before.border === after.border && before.padding === after.padding
  const kept = { ...element, geometry, name: current.name, hidden: current.hidden, decorative: current.decorative,
    style: sameLook ? current.style : element.style }
  if (kept.kind === 'text' && current.kind === 'text' && widget.kind === 'text') {
    const plain = (current.text?.runs ?? []).map(run => 'text' in run ? run.text : '').join('')
    if (plain === widget.text) kept.text = current.text
    if ((current.role === 'title') === (widget.style === 'title')) kept.role = current.role
  }
  return kept
}

/** Apply a change locally for an optimistic preview; the server result replaces it. */
export function applyLocal(definition: DashboardDefinition, change: Change): DashboardDefinition {
  const pages = definition.pages.map(page => ({ ...page, elements: [...(page.elements ?? [])], legacy_spacers: [...(page.legacy_spacers ?? [])] }))
  const next = { ...definition, pages }
  const index = 'page_id' in change ? pages.findIndex(page => page.page_id === change.page_id) : -1
  const target = index >= 0 ? pages[index] : undefined
  switch (change.op) {
    case 'layout.apply': {
      if (!target) break
      const layouts = new Map(change.placements.map(item => [item.widget_id, item.layout as Layout]))
      const move = <T extends { element_id: string; geometry: Geometry }>(item: T): T => {
        const layout = layouts.get(item.element_id)
        return layout ? { ...item, geometry: keepOrCell(target, item.geometry, layout) } : item
      }
      target.elements = target.elements.map(move)
      target.legacy_spacers = target.legacy_spacers.map(move)
      break
    }
    case 'widget.add': {
      if (!target) break
      const widget = change.widget as Widget
      if (widget.kind === 'spacer') break
      const element = placed(target, widget)
      if (element) target.elements.push(element)
      break
    }
    case 'widget.remove':
      if (!target) break
      target.elements = target.elements.filter(item => item.element_id !== change.widget_id)
      target.legacy_spacers = target.legacy_spacers.filter(item => item.element_id !== change.widget_id)
      break
    case 'widget.replace': {
      if (!target) break
      const widget = change.widget as Widget
      if (widget.kind === 'spacer') {
        target.legacy_spacers = target.legacy_spacers.map(item => item.element_id === widget.widget_id
          ? { ...item, title: widget.title ?? '', locked: widget.locked ?? false, style: styleOf(widget.appearance),
            geometry: keepOrCell(target, item.geometry, widget.layout) }
          : item)
        break
      }
      target.elements = target.elements.map(item => item.element_id === widget.widget_id ? placed(target, widget, item) ?? item : item)
      break
    }
    case 'filters.replace': next.filters = change.filters; break
    case 'actions.replace': next.actions = change.actions; break
    case 'dashboard.rename': next.title = change.title; break
    case 'page.add': {
      const page = 'widgets' in change.page ? upcastPage(change.page) : change.page as Page
      pages.push({ ...page, elements: [...(page.elements ?? [])], legacy_spacers: [...(page.legacy_spacers ?? [])] })
      break
    }
    case 'page.rename': if (target) target.title = change.title; break
  }
  if (target) pages[index] = grow(target) as typeof target
  return next
}

export function sameLayout(page: Page, placements: { widget_id: string; layout: Layout }[]): boolean {
  const current = new Map(gridWidgets(page).map(widget => [widget.widget_id, widget.layout]))
  return placements.length === current.size && placements.every(item => {
    const layout = current.get(item.widget_id)
    return layout && layout.x === item.layout.x && layout.y === item.layout.y && layout.w === item.layout.w && layout.h === item.layout.h
  })
}
