import { describe, expect, it } from 'vitest'
import { buildCatalogTree } from './helpers'
import type { CatalogInfo } from './types'

const catalog = (name: string, databases: string[]): CatalogInfo => ({
  name,
  type: name === 'default_catalog' ? 'Internal' : 'Iceberg',
  comment: null,
  databases,
})

describe('buildCatalogTree', () => {
  it('tags database nodes with their catalog', () => {
    const tree = buildCatalogTree(catalog('iceberg_lake', ['sales']))
    const db = tree.children?.[0]
    expect(db?.catalog).toBe('iceberg_lake')
    expect(db?.database).toBe('sales')
  })

  it('does not collide when two catalogs expose the same database name', () => {
    const internal = buildCatalogTree(catalog('default_catalog', ['sales']))
    const external = buildCatalogTree(catalog('iceberg_lake', ['sales']))
    expect(internal.children?.[0].id).not.toBe(external.children?.[0].id)
  })

  it('labels the internal catalog as Nova Catalog', () => {
    const tree = buildCatalogTree(catalog('default_catalog', []))
    expect(tree.label).toBe('Nova Catalog')
  })
})
