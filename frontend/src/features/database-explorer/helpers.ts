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

/** Labels of the placeholder rows; never counted as matches or selections. */
export const EMPTY_LABEL = 'No objects'
export const LOADING_LABEL = 'Loading'

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

    // Loading and empty placeholder rows never count: expanding them shows
    // nothing, and matching them would pop empty groups open on every search.
    const isPlaceholder = node.label === LOADING_LABEL || node.label === EMPTY_LABEL

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
      { label: 'Source', value: catalog.source_label ?? catalog.type },
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
        label: LOADING_LABEL,
        type: 'group' as ExplorerNodeType,
        path: [catalogLabel, db],
        database: db,
        catalog: catalog.name,
        metadata: [],
      }],
    })),
  }
}

type GroupItem = { id: string; name: string; metadata: ExplorerNode['metadata'] }

type GroupDef = {
  /** Group id suffix and node id part, kept stable for selection and tests. */
  idSuffix: string
  itemIdPart: string
  label: string
  type: ExplorerNodeType
  items: (data: DatabaseObjectsResponse, db: string, external: boolean) => GroupItem[]
}

const yesNo = (value: boolean | null | undefined) =>
  value == null ? 'Unknown' : value ? 'Yes' : 'No'

/**
 * Every explorer group, keyed as the backend names it in a catalog's
 * `object_groups`. A catalog shows only the groups it declares, in its order:
 * a source catalog (PostgreSQL, Snowflake, another StarRocks) only provides
 * data, so it declares tables, views, and materialized views.
 */
export const OBJECT_GROUP_DEFS: Record<string, GroupDef> = {
  tables: {
    idSuffix: 'tables', itemIdPart: 'table', label: 'Tables', type: 'table',
    items: (data, _db, external) => data.tables.map((t) => ({
      id: t.name, name: t.name,
      metadata: external
        ? [
            ...(t.engine ? [{ label: 'Kind', value: t.engine }] : []),
            ...(t.row_count != null ? [{ label: 'Rows', value: String(t.row_count) }] : []),
          ]
        : [
            { label: 'Model', value: formatModel(t.table_model) },
            { label: 'Engine', value: t.engine || 'Nova' },
            ...(t.row_count != null ? [{ label: 'Rows', value: String(t.row_count) }] : []),
            ...(t.data_size != null ? [{ label: 'Size', value: formatBytes(t.data_size) }] : []),
            ...(t.create_time ? [{ label: 'Created', value: t.create_time.split('T')[0] }] : []),
          ],
    })),
  },
  views: {
    idSuffix: 'views', itemIdPart: 'view', label: 'Views', type: 'view',
    items: (data) => data.views.map((v) => ({
      id: v.name, name: v.name,
      metadata: [
        ...(v.definer ? [{ label: 'Definer', value: v.definer }] : []),
        ...(v.is_updatable ? [{ label: 'Updatable', value: v.is_updatable }] : []),
      ],
    })),
  },
  materialized_views: {
    idSuffix: 'mvs', itemIdPart: 'mv', label: 'Materialized Views', type: 'materialized_view',
    items: (data, _db, external) => data.materialized_views.map((m) => ({
      id: m.name, name: m.name,
      metadata: external
        ? []
        : [
            ...(m.refresh_type ? [{ label: 'Refresh', value: m.refresh_type }] : []),
            { label: 'Active', value: yesNo(m.is_active) },
            ...(m.last_refresh_state ? [{ label: 'Last refresh', value: m.last_refresh_state }] : []),
            ...(m.table_rows != null ? [{ label: 'Rows', value: String(m.table_rows) }] : []),
          ],
    })),
  },
  entities: {
    idSuffix: 'entities', itemIdPart: 'entities', label: 'Entities', type: 'entity',
    items: (data, db) => (data.entities ?? []).map((entity) => ({
      id: entity.id, name: entity.name,
      metadata: [
        { label: 'Source relation', value: entity.relation },
        { label: 'Key columns', value: entity.key_columns.join(', ') },
        { label: 'Nova schema', value: entity.schema_name || db },
      ],
    })),
  },
  semantic_views: {
    idSuffix: 'semantic_views', itemIdPart: 'semantic_views', label: 'Semantic Views',
    type: 'semantic_view',
    items: (data, db) => (data.semantic_views ?? []).map((view) => ({
      id: view.id, name: view.name,
      metadata: [
        { label: 'Status', value: view.status },
        { label: 'Active version', value: view.active_version?.toString() ?? '—' },
        { label: 'Nova schema', value: view.schema_name || db },
      ],
    })),
  },
  feature_views: {
    idSuffix: 'feature_views', itemIdPart: 'feature_views', label: 'Feature Views',
    type: 'feature_view',
    items: (data) => (data.feature_views ?? []).map((view) => ({
      id: view.name, name: view.name,
      metadata: [
        { label: 'Status', value: view.status },
        { label: 'Active version', value: view.active_version?.toString() ?? '—' },
      ],
    })),
  },
  functions: {
    idSuffix: 'functions', itemIdPart: 'fn', label: 'Functions', type: 'function',
    items: (data) => data.functions.map((f) => ({
      id: f.name, name: f.name,
      metadata: [
        ...(f.routine_type ? [{ label: 'Type', value: f.routine_type }] : []),
        ...(f.definer ? [{ label: 'Definer', value: f.definer }] : []),
      ],
    })),
  },
  pipes: {
    idSuffix: 'pipes', itemIdPart: 'pipe', label: 'Pipes', type: 'pipe',
    items: (data) => data.pipes.map((p) => ({
      id: p.name, name: p.name,
      metadata: [
        ...(p.state ? [{ label: 'State', value: p.state }] : []),
        ...(p.target_table ? [{ label: 'Target', value: p.target_table }] : []),
        ...(p.load_status ? [{ label: 'Load status', value: p.load_status }] : []),
      ],
    })),
  },
  stages: {
    idSuffix: 'stages', itemIdPart: 'stage', label: 'Stages', type: 'stage',
    items: (data) => data.stages.map((s) => ({
      id: s.name, name: s.name,
      metadata: [
        ...(s.storage_connection ? [{ label: 'Connection', value: s.storage_connection }] : []),
        ...(s.base_prefix ? [{ label: 'Prefix', value: s.base_prefix }] : []),
      ],
    })),
  },
  // Nova CREATE TASK definitions scoped to this database.schema.
  tasks: {
    idSuffix: 'tasks', itemIdPart: 'task', label: 'Tasks', type: 'task',
    items: (data) => (data.tasks ?? []).map((t) => ({
      id: t.name, name: t.name,
      metadata: [
        ...(t.schedule_kind ? [{ label: 'Schedule', value: t.schedule_kind }] : []),
        ...(t.schedule_expr ? [{ label: 'Expr', value: t.schedule_expr }] : []),
        ...(t.timezone ? [{ label: 'Timezone', value: t.timezone }] : []),
        ...(t.overlap_policy ? [{ label: 'Overlap', value: t.overlap_policy }] : []),
      ],
    })),
  },
}

/** Groups of Nova's own catalog, in the order the backend lists them. */
export const NATIVE_OBJECT_GROUPS = [
  'tables', 'views', 'materialized_views', 'entities', 'semantic_views', 'feature_views',
  'functions', 'pipes', 'stages', 'tasks',
]

/** The groups a catalog declares, falling back by kind for an older backend. */
export function catalogObjectGroups(catalog: CatalogInfo | undefined | null): string[] {
  if (catalog?.object_groups?.length) return catalog.object_groups
  return !catalog || catalog.name === 'default_catalog'
    ? NATIVE_OBJECT_GROUPS
    : ['tables', 'views', 'materialized_views']
}

export function buildDatabaseChildren(
  data: DatabaseObjectsResponse,
  catalog = 'default_catalog',
  groups: string[] = NATIVE_OBJECT_GROUPS
): ExplorerNode[] {
  const db = data.database
  const catalogPath = catalog === 'default_catalog' ? 'Nova Catalog' : catalog
  const idBase = `${catalog}-${db}`
  const external = catalog !== 'default_catalog'

  return groups.flatMap((key) => {
    const def = OBJECT_GROUP_DEFS[key]
    if (!def) return []
    const groupId = `${idBase}-${def.idSuffix}`
    const items = def.items(data, db, external)
    const node: ExplorerNode = {
      id: groupId,
      label: def.label,
      type: 'group',
      path: [catalogPath, db, def.label],
      database: db,
      catalog,
      metadata: [{ label: 'Count', value: String(items.length) }],
      children: items.length > 0
        ? items.map((item) => ({
            id: `${idBase}-${def.itemIdPart}-${item.id}`,
            label: item.name,
            type: def.type,
            path: [catalogPath, db, def.label, item.name],
            database: db,
            catalog,
            metadata: item.metadata,
          }))
        : [{
            id: `${groupId}-empty`,
            label: EMPTY_LABEL,
            type: 'group' as ExplorerNodeType,
            path: [catalogPath, db, def.label],
            database: db,
            catalog,
            metadata: [],
            children: [],
          }],
    }
    return [node]
  })
}

/** The id of an object's tree node, as `buildDatabaseChildren` names it. */
export function objectNodeId(catalog: string, database: string, group: string, itemId: string) {
  const def = OBJECT_GROUP_DEFS[group]
  return `${catalog}-${database}-${def?.itemIdPart ?? group}-${itemId}`
}

/** The tree nodes to expand so an object of `group` is visible. */
export function objectAncestorIds(catalog: string, database: string, group?: string) {
  const ids = [`catalog-${catalog}`, `db-${catalog}-${database}`]
  const def = group ? OBJECT_GROUP_DEFS[group] : undefined
  if (def) ids.push(`${catalog}-${database}-${def.idSuffix}`)
  return ids
}

/** `catalog.database.object`, the name a query uses, or null for a group. */
export function qualifiedNameOf(node: ExplorerNode | null): string | null {
  if (!node) return null
  if (node.type === 'catalog') return node.id.replace(/^catalog-/, '')
  const catalog = node.catalog ?? 'default_catalog'
  if (node.type === 'database') return `${catalog}.${node.database ?? node.label}`
  if (node.type === 'group' || !node.database) return null
  const name = node.type === 'stage' ? `@${node.label}` : node.label
  return node.type === 'stage' ? name : `${catalog}.${node.database}.${name}`
}
