import type { DashboardDefinition, Operation } from '../contracts'
import type { Layout, Page, Widget } from './layout'

export type Change = Operation['changes'][number]

/** The change that reverts one applied change against the definition it was applied to. */
export function inverse(change: Change, before: DashboardDefinition): Change | null {
  const page = (id: string) => before.pages.find(item => item.page_id === id)
  switch (change.op) {
    case 'layout.apply': {
      const current = page(change.page_id)
      if (!current) return null
      return { op: 'layout.apply', page_id: change.page_id, placements: (current.widgets ?? []).map(widget =>
        ({ widget_id: widget.widget_id, layout: widget.layout })) }
    }
    case 'widget.add':
      return { op: 'widget.remove', page_id: change.page_id, widget_id: change.widget.widget_id }
    case 'widget.remove': {
      const widget = page(change.page_id)?.widgets?.find(item => item.widget_id === change.widget_id)
      return widget ? { op: 'widget.add', page_id: change.page_id, widget } : null
    }
    case 'widget.replace': {
      const widget = page(change.page_id)?.widgets?.find(item => item.widget_id === change.widget.widget_id)
      return widget ? { op: 'widget.replace', page_id: change.page_id, widget } : null
    }
    case 'filters.replace':
      return { op: 'filters.replace', filters: before.filters ?? [] }
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

/** Apply a change locally for an optimistic preview; the server result replaces it. */
export function applyLocal(definition: DashboardDefinition, change: Change): DashboardDefinition {
  const pages = definition.pages.map(page => ({ ...page, widgets: [...(page.widgets ?? [])] }))
  const next = { ...definition, pages }
  const target = 'page_id' in change ? pages.find(page => page.page_id === change.page_id) : undefined
  switch (change.op) {
    case 'layout.apply': {
      if (!target) break
      const layouts = new Map(change.placements.map(item => [item.widget_id, item.layout as Layout]))
      target.widgets = target.widgets.map(widget => layouts.has(widget.widget_id) ? { ...widget, layout: layouts.get(widget.widget_id)! } : widget)
      break
    }
    case 'widget.add': target?.widgets.push(change.widget as Widget); break
    case 'widget.remove': if (target) target.widgets = target.widgets.filter(widget => widget.widget_id !== change.widget_id); break
    case 'widget.replace': if (target) target.widgets = target.widgets.map(widget => widget.widget_id === change.widget.widget_id ? change.widget as Widget : widget); break
    case 'filters.replace': next.filters = change.filters; break
    case 'dashboard.rename': next.title = change.title; break
    case 'page.add': pages.push({ ...change.page, widgets: [...(change.page.widgets ?? [])] }); break
    case 'page.rename': if (target) target.title = change.title; break
  }
  return next
}

export function sameLayout(page: Page, placements: { widget_id: string; layout: Layout }[]): boolean {
  const current = new Map((page.widgets ?? []).map(widget => [widget.widget_id, widget.layout]))
  return placements.length === current.size && placements.every(item => {
    const layout = current.get(item.widget_id)
    return layout && layout.x === item.layout.x && layout.y === item.layout.y && layout.w === item.layout.w && layout.h === item.layout.h
  })
}
