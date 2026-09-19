import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { ExplorerDetail } from './explorer-detail'
import type { CatalogsResponse, ExplorerNode } from './types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const catalogsData: CatalogsResponse = {
  catalogs: [
    { name: 'default_catalog', type: 'Internal', comment: null, databases: ['sales'] },
    { name: 'iceberg_managed', type: 'Iceberg', comment: null, databases: ['lake'] },
    { name: 'engine_only', type: 'Hive', comment: null, databases: ['raw'] },
  ],
}

const catalogNode = (label: string): ExplorerNode => ({
  id: `catalog-${label}`,
  label,
  type: 'catalog',
  path: [label],
  metadata: [],
})

async function renderCatalog(label: string) {
  const { container } = await render(
    <ExplorerDetail
      node={catalogNode(label)}
      catalogsData={catalogsData}
      managedCatalogNames={new Set(['iceberg_managed'])}
      onCreateCatalog={() => {}}
      onDropCatalog={() => {}}
    />
  )

  const text = (container as HTMLElement).textContent ?? ''
  const badges = Array.from(
    (container as HTMLElement).querySelectorAll('[data-slot="badge"]')
  ).map((badge) => badge.textContent)
  const buttons = Array.from(
    (container as HTMLElement).querySelectorAll('button')
  ).map((button) => button.textContent?.trim())

  return { text, badges, buttons }
}

describe('catalog detail view', () => {
  // default_catalog sits at index 0 of GET /explorer/catalogs, so a
  // default-first match resolved every catalog node to it (NOVA-138 QA).
  it('shows the selected catalog type and databases, not the default catalog', async () => {
    const candidate = await renderCatalog('iceberg_managed')

    expect(candidate.text).toContain('lake')
    expect(candidate.text).not.toContain('sales')
    expect(candidate.badges).toContain('Iceberg')
  })

  it('offers Drop only for a Nova-managed catalog', async () => {
    const managed = await renderCatalog('iceberg_managed')
    const engineOnly = await renderCatalog('engine_only')

    expect(managed.buttons).toContain('Drop catalog')
    expect(engineOnly.buttons).not.toContain('Drop catalog')
  })

  it('resolves the renamed default catalog through the explicit fallback', async () => {
    const renamingNode = catalogNode('Nova Catalog')
    const { container } = await render(
      <ExplorerDetail
        node={renamingNode}
        catalogsData={catalogsData}
        managedCatalogNames={new Set(['iceberg_managed'])}
        onCreateCatalog={() => {}}
        onDropCatalog={() => {}}
      />
    )
    const text = (container as HTMLElement).textContent ?? ''

    expect(text).toContain('sales')
    expect(text).not.toContain('lake')
  })
})
