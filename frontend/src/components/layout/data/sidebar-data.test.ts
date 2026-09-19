import { describe, expect, it } from 'vitest'
import { sidebarData } from './sidebar-data'

function navUrls() {
  return sidebarData.navGroups.flatMap((group) =>
    group.items.flatMap((item) =>
      'items' in item && item.items
        ? item.items.map((child) => child.url as string)
        : [(item as { url: string }).url],
    ),
  )
}

describe('sidebar navigation', () => {
  // Stages, Functions, and External Catalogs live inside Database Explorer now;
  // a returning top-level entry would split the navigation again (NOVA-138).
  it('has no top-level entry for the consolidated pages', () => {
    const urls = navUrls()

    expect(urls).not.toContain('/stages')
    expect(urls).not.toContain('/functions')
    expect(urls).not.toContain('/external-catalogs')
  })

  it('keeps Database Explorer as the entry point for those objects', () => {
    expect(navUrls()).toContain('/database-explorer')
  })

  it('keeps the neighbouring data-management entries', () => {
    const urls = navUrls()

    expect(urls).toContain('/migration')
    expect(urls).toContain('/tasks')
    expect(urls).toContain('/ml-models')
  })

  // Tasks and Task Graphs were merged into one Tasks page (NOVA task UI
  // rework): the flow now lives on the task detail page, so a separate
  // Task Graphs entry would split the navigation again.
  it('has a single Tasks entry, with no separate Task Graphs entry', () => {
    const urls = navUrls()

    expect(urls).not.toContain('/tasks-manager')
    expect(urls).not.toContain('/task-graphs')
  })
})
