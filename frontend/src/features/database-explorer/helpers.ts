import {
  ArrowRightLeft,
  Box,
  CalendarClock,
  BrainCircuit,
  Database,
  Eye,
  FolderOpen,
  FolderTree,
  Layers3,
  Network,
  Sigma,
  Table2,
} from 'lucide-react'
import type {
  CatalogInfo,
  DatabaseObjectsResponse,
  ExplorerNode,
  ExplorerNodeType,
} from './types'

export function formatBytes(bytes: number | null): string {
  if (bytes == null || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

export function stripBackticks(value: string | null): string {
  if (!value) return '—'
  return value.replace(/`/g, '')
}

export function formatModel(model: string | null): string {
  if (!model) return 'Unknown'
  return model
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// ── Icons ─────────────────────────────────────────────────────

export function getNodeIcon(type: ExplorerNodeType) {
  switch (type) {
    case 'catalog': return FolderTree
    case 'database': return Database
    case 'group': return FolderOpen
    case 'table': return Table2
    case 'view': return Eye
    case 'materialized_view': return Layers3
    case 'function': return Sigma
    case 'pipe': return ArrowRightLeft
    case 'stage': return Box
    case 'task': return CalendarClock
    case 'entity': return Network
    case 'semantic_view': return BrainCircuit
    case 'feature_view': return Layers3
  }
}

export function getNodeTypeLabel(type: ExplorerNodeType) {
  switch (type) {
    case 'materialized_view': return 'Materialized View'
    case 'semantic_view': return 'Semantic View'
    case 'feature_view': return 'Feature View'
    default: return type.charAt(0).toUpperCase() + type.slice(1)
  }
}

// ── Tree utilities ────────────────────────────────────────────

export function filterTree(nodes: ExplorerNode[], query: string): ExplorerNode[] {
  if (!query) return nodes
  const results: ExplorerNode[] = []
  for (const node of nodes) {
    const children = node.children ? filterTree(node.children, query) : undefined
    const matchesSelf =
      node.label.toLowerCase().includes(query) ||
      getNodeTypeLabel(node.type).toLowerCase().includes(query)
    if (matchesSelf || (children?.length ?? 0) > 0) {
      results.push({ ...node, children })
    }
  }
  return results
}

// Every node on a path to a matching node, including group nodes that only
// survive so their matching descendant stays reachable. Search uses this to
// open the ancestors of a result in one pass; leaves are skipped because
// expanding them does nothing.
export function collectMatchingAncestorIds(
  nodes: ExplorerNode[],
  query: string,
): Set<string> {
  const ids = new Set<string>()
  if (!query) return ids

  const walk = (node: ExplorerNode, ancestors: string[]): boolean => {
    const children: ExplorerNode[] = node.children ?? []
    let hasMatchingDescendant = false
    for (const child of children) {
      if (walk(child, [...ancestors, node.id])) hasMatchingDescendant = true
    }

    const matchesSelf =
      node.label.toLowerCase().includes(query) ||
      getNodeTypeLabel(node.type).toLowerCase().includes(query)

    // "Loading..." and "No Objects Found" never count: expanding them shows
    // nothing, and matching them would pop empty groups open on every search.
    const isPlaceholder = node.label === 'Loading...' || node.label === 'No Objects Found'

    if (!isPlaceholder && (matchesSelf || hasMatchingDescendant)) {
      for (const ancestor of ancestors) ids.add(ancestor)
    }
    return hasMatchingDescendant || (matchesSelf && !isPlaceholder)
  }

  for (const node of nodes) walk(node, [])
  return ids
}

export function findNodeById(nodes: ExplorerNode[], id: string): ExplorerNode | null {
  for (const node of nodes) {
    if (node.id === id) return node
    if (node.children) {
      const found = findNodeById(node.children, id)
      if (found) return found
    }
  }
  return null
}

// ── Build catalog root node from API data ─────────────────────

export function buildCatalogTree(catalog: CatalogInfo): ExplorerNode {
  const catalogLabel =
    catalog.name === 'default_catalog' ? 'Nova Catalog' : catalog.name
  return {
    id: `catalog-${catalog.name}`,
    label: catalogLabel,
    type: 'catalog',
    path: [catalogLabel],
    metadata: [
      { label: 'Catalog type', value: catalog.type },
      { label: 'Databases', value: String(catalog.databases.length) },
      ...(catalog.comment ? [{ label: 'Comment', value: catalog.comment }] : []),
    ],
    children: catalog.databases.map((db) => ({
      // The catalog is part of the id: two catalogs can expose a database with
      // the same name, and an id collision would collapse them into one node.
      id: `db-${catalog.name}-${db}`,
      label: db,
      type: 'database' as ExplorerNodeType,
      path: [catalogLabel, db],
      database: db,
      catalog: catalog.name,
      metadata: [],
      // Placeholder child to make database nodes appear as expandable
      children: [{
        id: `db-${catalog.name}-${db}-loading`,
        label: 'Loading...',
        type: 'group' as ExplorerNodeType,
        path: [catalogLabel, db],
        database: db,
        catalog: catalog.name,
        metadata: [],
      }],
    })),
  }
}

export function buildDatabaseChildren(
  data: DatabaseObjectsResponse,
  catalog = 'default_catalog'
): ExplorerNode[] {
  const db = data.database
  const catalogPath = catalog === 'default_catalog' ? 'Nova Catalog' : catalog
  const idBase = `${catalog}-${db}`
  const children: ExplorerNode[] = []

  const emptyNode = (parentId: string, label: string): ExplorerNode => ({
    id: `${parentId}-empty`,
    label: 'No Objects Found',
    type: 'group' as ExplorerNodeType,
    path: [catalogPath, db, label],
    database: db,
    catalog,
    metadata: [],
    children: [],
  })

  // Tables group
  children.push({
    id: `${idBase}-tables`,
    label: 'Tables',
    type: 'group',
    path: [catalogPath, db, 'Tables'],
    database: db,
    catalog,
    metadata: [{ label: 'Count', value: String(data.tables.length) }],
    children: data.tables.length > 0
      ? data.tables.map((t) => ({
          id: `${idBase}-table-${t.name}`,
          label: t.name,
          type: 'table' as ExplorerNodeType,
          path: [catalogPath, db, 'Tables', t.name],
          database: db,
          catalog,
          metadata: [
            { label: 'Model', value: formatModel(t.table_model) },
            { label: 'Engine', value: t.engine || 'Nova' },
            ...(t.row_count != null ? [{ label: 'Rows', value: String(t.row_count) }] : []),
            ...(t.data_size != null ? [{ label: 'Size', value: formatBytes(t.data_size) }] : []),
            ...(t.create_time ? [{ label: 'Created', value: t.create_time.split('T')[0] }] : []),
          ],
        }))
      : [emptyNode(`${idBase}-tables`, 'Tables')],
  })

  // Views group
  children.push({
    id: `${idBase}-views`,
    label: 'Views',
    type: 'group',
    path: [catalogPath, db, 'Views'],
    database: db,
    catalog,
    metadata: [{ label: 'Count', value: String(data.views.length) }],
    children: data.views.length > 0
      ? data.views.map((v) => ({
          id: `${idBase}-view-${v.name}`,
          label: v.name,
          type: 'view' as ExplorerNodeType,
          path: [catalogPath, db, 'Views', v.name],
          database: db,
          metadata: [
            ...(v.definer ? [{ label: 'Definer', value: v.definer }] : []),
            ...(v.is_updatable ? [{ label: 'Updatable', value: v.is_updatable }] : []),
          ],
        }))
      : [emptyNode(`${idBase}-views`, 'Views')],
  })

  // MVs group
  children.push({
    id: `${idBase}-mvs`,
    label: 'Materialized Views',
    type: 'group',
    path: [catalogPath, db, 'Materialized Views'],
    database: db,
    catalog,
    metadata: [{ label: 'Count', value: String(data.materialized_views.length) }],
    children: data.materialized_views.length > 0
      ? data.materialized_views.map((m) => ({
          id: `${idBase}-mv-${m.name}`,
          label: m.name,
          type: 'materialized_view' as ExplorerNodeType,
          path: [catalogPath, db, 'Materialized Views', m.name],
          database: db,
          metadata: [
            ...(m.refresh_type ? [{ label: 'Refresh', value: m.refresh_type }] : []),
            { label: 'Active', value: m.is_active == null ? 'Unknown' : m.is_active ? 'Yes' : 'No' },
            ...(m.last_refresh_state ? [{ label: 'Last refresh', value: m.last_refresh_state }] : []),
            ...(m.table_rows != null ? [{ label: 'Rows', value: String(m.table_rows) }] : []),
          ],
        }))
      : [emptyNode(`${idBase}-mvs`, 'Materialized Views')],
  })

  const intelligenceGroups: Array<{
    key: 'entities' | 'semantic_views' | 'feature_views'
    label: string
    type: ExplorerNodeType
    items: Array<{ id: string; name: string; metadata: ExplorerNode['metadata'] }>
  }> = [
    {
      key: 'entities', label: 'Entities', type: 'entity',
      items: (data.entities ?? []).map((entity) => ({
        id: entity.id, name: entity.name,
        metadata: [
          { label: 'Source relation', value: entity.relation },
          { label: 'Key columns', value: entity.key_columns.join(', ') },
          { label: 'Nova schema', value: entity.schema_name || db },
        ],
      })),
    },
    {
      key: 'semantic_views', label: 'Semantic Views', type: 'semantic_view',
      items: (data.semantic_views ?? []).map((view) => ({
        id: view.id, name: view.name,
        metadata: [
          { label: 'Status', value: view.status },
          { label: 'Active version', value: view.active_version?.toString() ?? '—' },
          { label: 'Nova schema', value: view.schema_name || db },
        ],
      })),
    },
    {
      key: 'feature_views', label: 'Feature Views', type: 'feature_view',
      items: (data.feature_views ?? []).map((view) => ({
        id: view.name, name: view.name,
        metadata: [
          { label: 'Status', value: view.status },
          { label: 'Active version', value: view.active_version?.toString() ?? '—' },
        ],
      })),
    },
  ]
  for (const group of intelligenceGroups) {
    children.push({
      id: `${idBase}-${group.key}`,
      label: group.label,
      type: 'group',
      path: [catalogPath, db, group.label],
      database: db,
      catalog,
      metadata: [{ label: 'Count', value: String(group.items.length) }],
      children: group.items.length
        ? group.items.map((item) => ({
            id: `${idBase}-${group.key}-${item.id}`,
            label: item.name,
            type: group.type,
            path: [catalogPath, db, group.label, item.name],
            database: db,
            catalog,
            metadata: item.metadata,
          }))
        : [emptyNode(`${idBase}-${group.key}`, group.label)],
    })
  }

  // Functions group
  children.push({
    id: `${idBase}-functions`,
    label: 'Functions',
    type: 'group',
    path: [catalogPath, db, 'Functions'],
    database: db,
    catalog,
    metadata: [{ label: 'Count', value: String(data.functions.length) }],
    children: data.functions.length > 0
      ? data.functions.map((f) => ({
          id: `${idBase}-fn-${f.name}`,
          label: f.name,
          type: 'function' as ExplorerNodeType,
          path: [catalogPath, db, 'Functions', f.name],
          database: db,
          metadata: [
            ...(f.routine_type ? [{ label: 'Type', value: f.routine_type }] : []),
            ...(f.definer ? [{ label: 'Definer', value: f.definer }] : []),
          ],
        }))
      : [emptyNode(`${idBase}-functions`, 'Functions')],
  })

  // Pipes group
  children.push({
    id: `${idBase}-pipes`,
    label: 'Pipes',
    type: 'group',
    path: [catalogPath, db, 'Pipes'],
    database: db,
    catalog,
    metadata: [{ label: 'Count', value: String(data.pipes.length) }],
    children: data.pipes.length > 0
      ? data.pipes.map((p) => ({
          id: `${idBase}-pipe-${p.name}`,
          label: p.name,
          type: 'pipe' as ExplorerNodeType,
          path: [catalogPath, db, 'Pipes', p.name],
          database: db,
          metadata: [
            ...(p.state ? [{ label: 'State', value: p.state }] : []),
            ...(p.target_table ? [{ label: 'Target', value: p.target_table }] : []),
            ...(p.load_status ? [{ label: 'Load status', value: p.load_status }] : []),
          ],
        }))
      : [emptyNode(`${idBase}-pipes`, 'Pipes')],
  })

  // Stages group
  children.push({
    id: `${idBase}-stages`,
    label: 'Stages',
    type: 'group',
    path: [catalogPath, db, 'Stages'],
    database: db,
    catalog,
    metadata: [{ label: 'Count', value: String(data.stages.length) }],
    children: data.stages.length > 0
      ? data.stages.map((s) => ({
          id: `${idBase}-stage-${s.name}`,
          label: s.name,
          type: 'stage' as ExplorerNodeType,
          path: [catalogPath, db, 'Stages', s.name],
          database: db,
          metadata: [
            ...(s.storage_connection ? [{ label: 'Connection', value: s.storage_connection }] : []),
            ...(s.base_prefix ? [{ label: 'Prefix', value: s.base_prefix }] : []),
          ],
        }))
      : [emptyNode(`${idBase}-stages`, 'Stages')],
  })

  // Tasks group — Nova CREATE TASK definitions scoped to this database.schema.
  const tasks = data.tasks ?? []
  children.push({
    id: `${idBase}-tasks`,
    label: 'Tasks',
    type: 'group',
    path: [catalogPath, db, 'Tasks'],
    database: db,
    catalog,
    metadata: [{ label: 'Count', value: String(tasks.length) }],
    children: tasks.length > 0
      ? tasks.map((t) => ({
          id: `${idBase}-task-${t.name}`,
          label: t.name,
          type: 'task' as ExplorerNodeType,
          path: [catalogPath, db, 'Tasks', t.name],
          database: db,
          metadata: [
            ...(t.schedule_kind ? [{ label: 'Schedule', value: t.schedule_kind }] : []),
            ...(t.schedule_expr ? [{ label: 'Expr', value: t.schedule_expr }] : []),
            ...(t.timezone ? [{ label: 'Timezone', value: t.timezone }] : []),
            ...(t.overlap_policy ? [{ label: 'Overlap', value: t.overlap_policy }] : []),
          ],
        }))
      : [emptyNode(`${idBase}-tasks`, 'Tasks')],
  })

  return children
}
