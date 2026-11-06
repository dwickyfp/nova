import { describe, expect, it } from 'vitest'
import {
  EMPTY_LABEL,
  LOADING_LABEL,
  NATIVE_OBJECT_GROUPS,
  buildCatalogTree,
  buildDatabaseChildren,
  catalogObjectGroups,
  collectMatchingAncestorIds,
} from './helpers'
import type { CatalogInfo, DatabaseObjectsResponse, ExplorerNode } from './types'

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

describe('collectMatchingAncestorIds', () => {
  const leaf = (
    id: string,
    label: string,
    type: ExplorerNode['type'] = 'table',
  ): ExplorerNode => ({ id, label, type, path: [label], metadata: [] })

  const group = (id: string, label: string, children: ExplorerNode[]): ExplorerNode => ({
    id,
    label,
    type: 'group',
    path: [label],
    metadata: [],
    children,
  })

  const tree: ExplorerNode[] = [
    {
      id: 'catalog-default_catalog',
      label: 'Nova Catalog',
      type: 'catalog',
      path: ['Nova Catalog'],
      metadata: [],
      children: [
        {
          id: 'db-default_catalog-analytics',
          label: 'analytics',
          type: 'database',
          path: ['Nova Catalog', 'analytics'],
          metadata: [],
          children: [
            group('analytics-tables', 'Tables', [
              leaf('analytics-table-monthly_revenue', 'monthly_revenue'),
              leaf('analytics-table-orders', 'orders'),
            ]),
            group('analytics-views', 'Views', []),
          ],
        },
      ],
    },
  ]

  it('returns every ancestor on the path to a matching leaf', () => {
    const ids = collectMatchingAncestorIds(tree, 'monthl')
    expect(ids.has('catalog-default_catalog')).toBe(true)
    expect(ids.has('db-default_catalog-analytics')).toBe(true)
    expect(ids.has('analytics-tables')).toBe(true)
    // The leaf itself needs no expand, and non-matching branches stay closed.
    expect(ids.has('analytics-table-monthly_revenue')).toBe(false)
    expect(ids.has('analytics-views')).toBe(false)
  })

  it('matches on the node label case-insensitively', () => {
    const ids = collectMatchingAncestorIds(tree, 'monthly')
    expect(ids.has('analytics-tables')).toBe(true)
  })

  it('keeps ancestors closed when nothing matches', () => {
    expect(collectMatchingAncestorIds(tree, 'nope').size).toBe(0)
  })

  it('ignores placeholder rows', () => {
    const placeholderTree: ExplorerNode[] = [
      {
        id: 'catalog-external',
        label: 'external',
        type: 'catalog',
        path: ['external'],
        metadata: [],
        children: [group('db-loading', LOADING_LABEL, [])],
      },
    ]
    expect(collectMatchingAncestorIds(placeholderTree, 'loading').size).toBe(0)
  })
})


const objects = (overrides: Partial<DatabaseObjectsResponse> = {}): DatabaseObjectsResponse =>
  ({
    database: 'public',
    tables: [{ name: 'orders', table_model: null, engine: 'Partitioned table', row_count: null,
      data_size: null, create_time: null }],
    views: [{ name: 'jakarta_orders', definer: null, is_updatable: null }],
    materialized_views: [{ name: 'city_totals', refresh_type: null, is_active: null,
      last_refresh_state: null, table_rows: null }],
    functions: [],
    pipes: [],
    stages: [],
    tasks: [],
    entities: [],
    semantic_views: [],
    feature_views: [],
    ...overrides,
  }) as DatabaseObjectsResponse

describe('buildDatabaseChildren', () => {
  it('shows a source catalog only its data groups, each node tagged with the catalog', () => {
    const groups = catalogObjectGroups({
      ...catalog('pg_shop', ['public']),
      object_groups: ['tables', 'views', 'materialized_views'],
    })
    const children = buildDatabaseChildren(objects(), 'pg_shop', groups)
    expect(children.map((g) => g.label)).toEqual(['Tables', 'Views', 'Materialized Views'])
    const leaves = children.flatMap((g) => g.children ?? [])
    expect(leaves.map((n) => [n.type, n.label, n.catalog])).toEqual([
      ['table', 'orders', 'pg_shop'],
      ['view', 'jakarta_orders', 'pg_shop'],
      ['materialized_view', 'city_totals', 'pg_shop'],
    ])
    // A source table names its source's kind, not Nova's table model.
    expect(leaves[0].metadata).toEqual([{ label: 'Kind', value: 'Partitioned table' }])
  })

  it('keeps every Nova group, in order, for the internal catalog', () => {
    const children = buildDatabaseChildren(objects(), 'default_catalog', NATIVE_OBJECT_GROUPS)
    expect(children.map((g) => g.label)).toEqual([
      'Tables', 'Views', 'Materialized Views', 'Entities', 'Semantic Views', 'Feature Views',
      'Functions', 'Pipes', 'Stages', 'Tasks',
    ])
    expect(children.find((g) => g.label === 'Pipes')?.children?.[0].label).toBe(EMPTY_LABEL)
  })

  it('falls back by catalog when the backend sends no groups', () => {
    expect(catalogObjectGroups(catalog('default_catalog', []))).toEqual(NATIVE_OBJECT_GROUPS)
    expect(catalogObjectGroups(catalog('legacy_lake', []))).toEqual([
      'tables', 'views', 'materialized_views',
    ])
  })
})
