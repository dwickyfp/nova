import type { DashboardDefinition, ElementPatch, Geometry, Operation } from '../contracts'
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
    case 'elements.arrange': {
      const current = page(change.page_id)
      if (!current) return null
      const placements = change.placements.map(item => {
        const geometry = geometryOf(current, item.element_id)
        return geometry ? { element_id: item.element_id, geometry } : null
      })
      return placements.every(Boolean)
        ? { op: 'elements.arrange', page_id: change.page_id, placements: placements as { element_id: string; geometry: Geometry }[] }
        : null
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

/**
 * The changes that revert an operation's changes, newest first. Each change is
 * inverted against the document as it stood just before it. Null when one of
 * them cannot be reverted (the editor then does not offer Undo for it).
 */
/** What an element has when a property was never set; undo restores these rather than null, which the server refuses. */
const PATCH_DEFAULTS: Record<string, unknown> = { name: '', locked: false, hidden: false, decorative: false, style: {} }

export function inverseAll(changes: readonly Change[], before: DashboardDefinition): Change[] | null {
  let state = before
  const backward: Change[][] = []
  for (const change of changes) {
    const reverted = canvasInverse(change, state) ?? (() => { const one = inverse(change, state); return one ? [one] : null })()
    if (!reverted) return null
    backward.push(reverted)
    state = applyLocal(state, change)
  }
  return backward.reverse().flat()
}

function canvasInverse(change: Change, before: DashboardDefinition): Change[] | null | undefined {
  if (!('page_id' in change)) return undefined
  const current = before.pages.find(item => item.page_id === change.page_id)
  switch (change.op) {
    case 'elements.add':
      return [{ op: 'elements.remove', page_id: change.page_id, element_ids: change.elements.map(item => item.element_id) }]
    case 'elements.update': {
      if (!current) return null
      const updates = change.updates.map(update => {
        const element = findAny(current.elements ?? [], update.element_id)
        if (!element) return null
        const previous: Record<string, unknown> = {}
        for (const key of Object.keys(update.set)) previous[key] = (element as Record<string, unknown>)[key] ?? PATCH_DEFAULTS[key] ?? null
        return { element_id: update.element_id, set: previous as ElementPatch }
      })
      return updates.every(Boolean) ? [{ op: 'elements.update', page_id: change.page_id, updates: updates as { element_id: string; set: ElementPatch }[] }] : null
    }
    case 'elements.remove': {
      if (!current) return null
      const after = removeLocal({ ...before, pages: [...before.pages] }, current, change.element_ids)
      const survivors = new Set((after.page.elements ?? []).map(item => item.element_id))
      const original = current.elements ?? []
      // A removed child of a group that stays cannot be put back where it was.
      const nested = [...after.removed].filter(id => !original.some(item => item.element_id === id) && !original.some(item =>
        !survivors.has(item.element_id) && item.kind === 'group' && Boolean(findAny(item.children, id))))
      if (nested.length) return null
      const changes: Change[] = []
      let previous: string | null = null
      for (const item of original) {
        if (!survivors.has(item.element_id)) {
          changes.push({ op: 'elements.add', page_id: change.page_id, elements: [item], stack: previous ? { above: previous } : 'back' })
        }
        previous = item.element_id
      }
      changes.push({ op: 'page.tab_order.set', page_id: change.page_id, element_ids: [...(current.tab_order ?? [])] })
      changes.push({ op: 'page.mobile.set', page_id: change.page_id, mobile: { order: [...(current.mobile?.order ?? [])], hidden: [...(current.mobile?.hidden ?? [])] } })
      if (JSON.stringify(after.actions) !== JSON.stringify(before.actions ?? [])) changes.push({ op: 'actions.replace', actions: before.actions ?? [] })
      return changes
    }
    case 'elements.reorder':
      return current ? [{ op: 'elements.reorder', page_id: change.page_id, element_ids: (current.elements ?? []).map(item => item.element_id) }] : null
    case 'elements.group':
      return current ? [
        { op: 'elements.ungroup', page_id: change.page_id, group_id: change.group_id },
        { op: 'elements.reorder', page_id: change.page_id, element_ids: (current.elements ?? []).map(item => item.element_id) },
        { op: 'page.mobile.set', page_id: change.page_id, mobile: { order: [...(current.mobile?.order ?? [])], hidden: [...(current.mobile?.hidden ?? [])] } },
      ] : null
    case 'elements.ungroup': {
      const group = current?.elements?.find(item => item.element_id === change.group_id)
      if (!current || !group || group.kind !== 'group') return null
      return [
        { op: 'elements.group', page_id: change.page_id, group_id: group.element_id, element_ids: group.children.map(item => item.element_id), name: group.name ?? '' },
        // The group's own look and box, and its children's places inside it, as they were.
        { op: 'elements.update', page_id: change.page_id, updates: [
          { element_id: group.element_id, set: { geometry: group.geometry, ...(group.style ? { style: group.style } : {}), locked: group.locked ?? false,
            hidden: group.hidden ?? false, decorative: group.decorative ?? false } },
          ...group.children.map(child => ({ element_id: child.element_id, set: { geometry: child.geometry, hidden: child.hidden ?? false, locked: child.locked ?? false } })),
        ] },
        { op: 'page.tab_order.set', page_id: change.page_id, element_ids: [...(current.tab_order ?? [])] },
        { op: 'page.mobile.set', page_id: change.page_id, mobile: { order: [...(current.mobile?.order ?? [])], hidden: [...(current.mobile?.hidden ?? [])] } },
      ]
    }
    case 'page.canvas.set':
      return current ? [{ op: 'page.canvas.set', page_id: change.page_id, canvas: current.canvas ?? {} }] : null
    case 'page.tab_order.set':
      return current ? [{ op: 'page.tab_order.set', page_id: change.page_id, element_ids: [...(current.tab_order ?? [])] }] : null
    case 'page.mobile.set':
      return current ? [{ op: 'page.mobile.set', page_id: change.page_id, mobile: current.mobile ?? { order: [], hidden: [] } }] : null
    default:
      return undefined
  }
}

function findAny(items: readonly Element[], id: string): Element | undefined {
  for (const item of items) {
    if (item.element_id === id) return item
    const found = item.kind === 'group' ? findAny(item.children, id) : undefined
    if (found) return found
  }
  return undefined
}

function ids(items: readonly Element[]): string[] {
  return items.flatMap(item => [item.element_id, ...(item.kind === 'group' ? ids(item.children) : [])])
}

/** Element IDs a connector, note or bound value depends on (server: ``_references``). */
function references(element: Element): string[] {
  const found: string[] = []
  if (element.kind === 'connector') {
    for (const end of [element.start, element.end]) {
      if ('element_id' in end) found.push(end.element_id)
      else if ('anchor' in end) found.push(end.anchor.element_id)
    }
  }
  if (element.kind === 'annotation') found.push(element.target.element_id)
  if (element.kind === 'text' || element.kind === 'annotation') {
    for (const run of element.text?.runs ?? []) if ('value' in run) found.push(run.value.element_id)
  }
  return found
}

function prune(items: readonly Element[], removed: ReadonlySet<string>): Element[] {
  const kept: Element[] = []
  for (const item of items) {
    if (removed.has(item.element_id)) continue
    if (item.kind === 'group') {
      const children = prune(item.children, removed)
      if (!children.length) continue
      kept.push({ ...item, children: children as typeof item.children })
    } else kept.push(item)
  }
  return kept
}

/** Remove elements and what hung off them (server: ``_remove`` and ``_without_elements``). */
function removeLocal(definition: DashboardDefinition, page: Page, targets: readonly string[]) {
  const spacers = new Set((page.legacy_spacers ?? []).map(item => item.element_id))
  const removed = new Set<string>(targets.filter(id => spacers.has(id)))
  let elements = [...(page.elements ?? [])]
  let pending = new Set(targets.filter(id => !spacers.has(id)))
  while (pending.size) {
    for (const id of pending) {
      removed.add(id)
      const found = findAny(elements, id)
      if (found) for (const inner of ids([found])) removed.add(inner)
    }
    elements = prune(elements, removed)
    const dependents = new Set<string>()
    const walk = (items: readonly Element[]) => items.forEach(item => {
      if ((item.kind === 'connector' || item.kind === 'annotation') && references(item).some(id => removed.has(id))) dependents.add(item.element_id)
      if (item.kind === 'group') walk(item.children)
    })
    walk(elements)
    pending = new Set([...dependents].filter(id => !removed.has(id)))
  }
  const next: Page = {
    ...page, elements, legacy_spacers: (page.legacy_spacers ?? []).filter(item => !removed.has(item.element_id)),
    tab_order: (page.tab_order ?? []).filter(id => !removed.has(id)),
    mobile: { order: (page.mobile?.order ?? []).filter(id => !removed.has(id)), hidden: (page.mobile?.hidden ?? []).filter(id => !removed.has(id)) },
  }
  const actions = (definition.actions ?? []).flatMap(action => {
    if (removed.has(action.source_widget_id)) return []
    const targetsLeft = (action.target_widget_ids ?? []).filter(id => !removed.has(id))
    if (action.target_widget_ids?.length && !targetsLeft.length && action.kind !== 'navigate') return []
    return [{ ...action, target_widget_ids: targetsLeft }]
  })
  return { page: next, removed, actions }
}

function groupLocal(page: Page, groupId: string, members: readonly string[], name: string): Page {
  const top = page.elements ?? []
  const chosen = top.filter(item => members.includes(item.element_id))
  if (!chosen.length) return page
  const left = Math.min(...chosen.map(item => item.geometry.x))
  const upper = Math.min(...chosen.map(item => item.geometry.y))
  const right = Math.max(...chosen.map(item => item.geometry.x + item.geometry.w))
  const lower = Math.max(...chosen.map(item => item.geometry.y + item.geometry.h))
  const group = { kind: 'group' as const, element_id: groupId, name, locked: false, hidden: false, decorative: false, style: {},
    geometry: { x: left, y: upper, w: right - left, h: lower - upper, rotation: 0 as const },
    children: chosen.map(item => ({ ...item, geometry: { ...item.geometry, x: item.geometry.x - left, y: item.geometry.y - upper } })) }
  const at = top.indexOf(chosen[chosen.length - 1])
  const elements = [...top.slice(0, at).filter(item => !members.includes(item.element_id)), group as Element,
    ...top.slice(at + 1).filter(item => !members.includes(item.element_id))]
  const order: string[] = []
  for (const id of page.mobile?.order ?? []) {
    if (members.includes(id)) { if (!order.includes(groupId)) order.push(groupId) } else order.push(id)
  }
  const hiddenBefore = page.mobile?.hidden ?? []
  const hidden = hiddenBefore.filter(id => !members.includes(id))
  if (members.every(id => hiddenBefore.includes(id))) hidden.push(groupId)
  return { ...page, elements, mobile: { order, hidden } }
}

function ungroupLocal(page: Page, groupId: string): Page {
  const top = page.elements ?? []
  const index = top.findIndex(item => item.element_id === groupId)
  const group = top[index]
  if (!group || group.kind !== 'group') return page
  const children = group.children.map(child => ({ ...child,
    geometry: { ...child.geometry, x: child.geometry.x + group.geometry.x, y: child.geometry.y + group.geometry.y },
    hidden: Boolean(child.hidden || group.hidden), locked: Boolean(child.locked || group.locked) })) as Element[]
  const released = children.map(child => child.element_id)
  const swap = (list: readonly string[]) => list.flatMap(id => id === groupId ? released : [id])
  return { ...page, elements: [...top.slice(0, index), ...children, ...top.slice(index + 1)],
    tab_order: (page.tab_order ?? []).filter(id => id !== groupId),
    mobile: { order: swap(page.mobile?.order ?? []), hidden: swap(page.mobile?.hidden ?? []) } }
}

function updateLocal(items: readonly Element[], updates: ReadonlyMap<string, ElementPatch>): Element[] {
  return items.map(item => {
    const patch = updates.get(item.element_id)
    const next = (patch ? { ...item, ...Object.fromEntries(Object.entries(patch).filter(([, value]) => value !== undefined)) } : item) as Element
    return next.kind === 'group' ? { ...next, children: updateLocal(next.children, updates) as typeof next.children } : next
  })
}

/** An element's geometry at any depth, or a kept spacer's. */
function geometryOf(page: Page, id: string): Geometry | undefined {
  const walk = (items: readonly Element[]): Geometry | undefined => {
    for (const item of items) {
      if (item.element_id === id) return item.geometry
      const found = item.kind === 'group' ? walk(item.children) : undefined
      if (found) return found
    }
    return undefined
  }
  return walk(page.elements ?? []) ?? page.legacy_spacers?.find(item => item.element_id === id)?.geometry
}

function placeAll(items: readonly Element[], geometries: Map<string, Geometry>): Element[] {
  return items.map(item => {
    const geometry = geometries.get(item.element_id)
    const moved = geometry ? { ...item, geometry } : item
    return moved.kind === 'group' ? { ...moved, children: placeAll(moved.children, geometries) as typeof moved.children } : moved
  })
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
    case 'elements.arrange': {
      if (!target) break
      const geometries = new Map(change.placements.map(item => [item.element_id, item.geometry]))
      target.elements = placeAll(target.elements, geometries)
      target.legacy_spacers = target.legacy_spacers.map(item => {
        const geometry = geometries.get(item.element_id)
        return geometry ? { ...item, geometry } : item
      })
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
    case 'elements.add': {
      if (!target) break
      const stack = change.stack ?? 'front'
      if (stack === 'front') target.elements.push(...change.elements)
      else if (stack === 'back') target.elements.unshift(...change.elements)
      else {
        const at = target.elements.findIndex(item => item.element_id === stack.above)
        target.elements.splice(at + 1, 0, ...change.elements)
      }
      break
    }
    case 'elements.update':
      if (target) target.elements = updateLocal(target.elements, new Map(change.updates.map(item => [item.element_id, item.set])))
      break
    case 'elements.remove': {
      if (!target) break
      const result = removeLocal(definition, target, change.element_ids)
      pages[index] = { ...result.page, elements: [...(result.page.elements ?? [])], legacy_spacers: [...(result.page.legacy_spacers ?? [])] }
      next.actions = result.actions
      pages[index] = grow(pages[index]) as typeof target
      return next
    }
    case 'elements.reorder': {
      if (!target) break
      const byId = new Map(target.elements.map(item => [item.element_id, item]))
      target.elements = change.element_ids.map(id => byId.get(id)).filter((item): item is Element => Boolean(item))
      break
    }
    case 'elements.group':
    case 'elements.ungroup': {
      if (!target) break
      const changed = change.op === 'elements.group' ? groupLocal(target, change.group_id, change.element_ids, change.name ?? '') : ungroupLocal(target, change.group_id)
      pages[index] = { ...changed, elements: [...(changed.elements ?? [])], legacy_spacers: target.legacy_spacers }
      break
    }
    case 'page.canvas.set': if (target) target.canvas = change.canvas; break
    case 'page.tab_order.set': if (target) target.tab_order = change.element_ids; break
    case 'page.mobile.set': if (target) target.mobile = change.mobile; break
  }
  if (index >= 0 && pages[index]) pages[index] = grow(pages[index]) as typeof pages[number]
  return next
}
