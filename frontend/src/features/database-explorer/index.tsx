import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowRightLeft,
  Blocks,
  Box,
  ChevronRight,
  Clock3,
  Database,
  Eye,
  FileText,
  FolderOpen,
  FolderTree,
  KeyRound,
  Layers3,
  Loader2,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Search,
  Sigma,
  Table2,
  Trash2,
  Upload,
} from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import {
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuSub,
} from '@/components/ui/sidebar'
import { api } from '@/lib/api-client'
import { toast } from 'sonner'

// ── Types ─────────────────────────────────────────────────────

type ExplorerNodeType =
  | 'catalog'
  | 'database'
  | 'group'
  | 'table'
  | 'view'
  | 'materialized_view'
  | 'function'
  | 'pipe'
  | 'stage'

type ExplorerNode = {
  id: string
  label: string
  type: ExplorerNodeType
  path: string[]
  metadata: Array<{ label: string; value: string }>
  children?: ExplorerNode[]
  /** Database name for lazy-load nodes */
  database?: string
}

// API response types
type CatalogInfo = {
  name: string
  type: string
  comment: string | null
  databases: string[]
}

type CatalogsResponse = {
  catalogs: CatalogInfo[]
}

type TableSummary = {
  name: string
  table_model: string | null
  engine: string | null
  row_count: number | null
  data_size: number | null
  create_time: string | null
}

type ViewSummary = {
  name: string
  definer: string | null
  is_updatable: string | null
}

type MVSummary = {
  name: string
  refresh_type: string | null
  is_active: boolean | null
  last_refresh_state: string | null
  table_rows: number | null
  query_rewrite_status: string | null
}

type FunctionSummary = {
  name: string
  routine_type: string | null
  definer: string | null
  created: string | null
}

type PipeSummary = {
  name: string
  state: string | null
  target_table: string | null
  load_status: string | null
  last_error: string | null
  created_time: string | null
}

type PipeDetailResponse = {
  name: string
  database: string
  pipe_id: number | null
  state: string | null
  target_table: string | null
  properties: Record<string, string>
  load_status: string | null
  last_error: string | null
  created_time: string | null
  create_ddl: string | null
}

type ViewDetailResponse = {
  name: string
  database: string
  definition: string | null
  definer: string | null
  security_type: string | null
  is_updatable: string | null
  create_ddl: string | null
}

type MVDetailResponse = {
  name: string
  database: string
  definition: string | null
  refresh_type: string | null
  is_active: string | null
  inactive_reason: string | null
  task_name: string | null
  last_refresh_state: string | null
  last_refresh_error_message: string | null
  last_refresh_start_time: string | null
  last_refresh_finished_time: string | null
  last_refresh_duration: number | null
  table_rows: number | null
  query_rewrite_status: string | null
  creator: string | null
}

type FunctionDetailResponse = {
  name: string
  database: string
  routine_type: string | null
  definition: string | null
  definer: string | null
  created: string | null
  last_altered: string | null
  is_deterministic: string | null
  sql_data_access: string | null
  comment: string | null
  signature: string | null
  return_type: string | null
  function_type: string | null
  properties: string | null
  create_ddl: string | null
}

type StageSummary = {
  name: string
  storage_connection: string | null
  base_prefix: string | null
  created_at: string | null
}

type DatabaseObjectsResponse = {
  database: string
  tables: TableSummary[]
  views: ViewSummary[]
  materialized_views: MVSummary[]
  functions: FunctionSummary[]
  pipes: PipeSummary[]
  stages: StageSummary[]
  summary: Record<string, number>
}

type ColumnInfo = {
  name: string
  ordinal_position: number
  data_type: string
  column_type: string | null
  is_nullable: string | null
  column_key: string | null
  column_default: string | null
  extra: string | null
  column_comment: string | null
  numeric_precision: number | null
  numeric_scale: number | null
  character_maximum_length: number | null
}

type TableProperties = {
  table_model: string | null
  primary_key: string | null
  partition_key: string | null
  distribute_key: string | null
  distribute_type: string | null
  distribute_bucket: string | number | null
  sort_key: string | null
  properties: Record<string, string>
  create_ddl: string | null
}

type PartitionInfo = {
  name: string
  partition_id: number | null
  partition_key: string | null
  partition_value: string | null
  row_count: number | null
  data_size: number | null
  storage_size: number | null
  buckets: number | null
  replication_num: number | null
  visible_version: number | null
  data_version: number | null
}

type TableDetailResponse = {
  name: string
  database: string
  columns: ColumnInfo[]
  properties: TableProperties
  partitions: PartitionInfo[]
  row_count: number | null
  data_size: number | null
}

// ── Helpers ───────────────────────────────────────────────────

function formatBytes(bytes: number | null): string {
  if (bytes == null || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

function stripBackticks(value: string | null): string {
  if (!value) return '—'
  return value.replace(/`/g, '')
}

function formatModel(model: string | null): string {
  if (!model) return 'Unknown'
  return model
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// ── Icons ─────────────────────────────────────────────────────

function getNodeIcon(type: ExplorerNodeType) {
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
  }
}

function getNodeTypeLabel(type: ExplorerNodeType) {
  switch (type) {
    case 'materialized_view': return 'Materialized View'
    default: return type.charAt(0).toUpperCase() + type.slice(1)
  }
}

// ── Tree utilities ────────────────────────────────────────────

function filterTree(nodes: ExplorerNode[], query: string): ExplorerNode[] {
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

function findNodeById(nodes: ExplorerNode[], id: string): ExplorerNode | null {
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

function buildCatalogTree(catalog: CatalogInfo): ExplorerNode {
  return {
    id: `catalog-${catalog.name}`,
    label: catalog.name === 'default_catalog' ? 'Nova Catalog' : catalog.name,
    type: 'catalog',
    path: [catalog.name === 'default_catalog' ? 'Nova Catalog' : catalog.name],
    metadata: [
      { label: 'Catalog type', value: catalog.type },
      { label: 'Databases', value: String(catalog.databases.length) },
      ...(catalog.comment ? [{ label: 'Comment', value: catalog.comment }] : []),
    ],
    children: catalog.databases.map((db) => ({
      id: `db-${db}`,
      label: db,
      type: 'database' as ExplorerNodeType,
      path: [catalog.name === 'default_catalog' ? 'Nova Catalog' : catalog.name, db],
      database: db,
      metadata: [],
      // Placeholder child to make database nodes appear as expandable
      children: [{
        id: `db-${db}-loading`,
        label: 'Loading...',
        type: 'group' as ExplorerNodeType,
        path: [catalog.name === 'default_catalog' ? 'Nova Catalog' : catalog.name, db],
        database: db,
        metadata: [],
      }],
    })),
  }
}

function buildDatabaseChildren(data: DatabaseObjectsResponse): ExplorerNode[] {
  const db = data.database
  const catalogPath = 'Nova Catalog'
  const children: ExplorerNode[] = []

  const emptyNode = (parentId: string, label: string): ExplorerNode => ({
    id: `${parentId}-empty`,
    label: 'No Objects Found',
    type: 'group' as ExplorerNodeType,
    path: [catalogPath, db, label],
    database: db,
    metadata: [],
    children: [],
  })

  // Tables group
  children.push({
    id: `${db}-tables`,
    label: 'Tables',
    type: 'group',
    path: [catalogPath, db, 'Tables'],
    database: db,
    metadata: [{ label: 'Count', value: String(data.tables.length) }],
    children: data.tables.length > 0
      ? data.tables.map((t) => ({
          id: `${db}-table-${t.name}`,
          label: t.name,
          type: 'table' as ExplorerNodeType,
          path: [catalogPath, db, 'Tables', t.name],
          database: db,
          metadata: [
            { label: 'Model', value: formatModel(t.table_model) },
            { label: 'Engine', value: t.engine || 'StarRocks' },
            ...(t.row_count != null ? [{ label: 'Rows', value: String(t.row_count) }] : []),
            ...(t.data_size != null ? [{ label: 'Size', value: formatBytes(t.data_size) }] : []),
            ...(t.create_time ? [{ label: 'Created', value: t.create_time.split('T')[0] }] : []),
          ],
        }))
      : [emptyNode(`${db}-tables`, 'Tables')],
  })

  // Views group
  children.push({
    id: `${db}-views`,
    label: 'Views',
    type: 'group',
    path: [catalogPath, db, 'Views'],
    database: db,
    metadata: [{ label: 'Count', value: String(data.views.length) }],
    children: data.views.length > 0
      ? data.views.map((v) => ({
          id: `${db}-view-${v.name}`,
          label: v.name,
          type: 'view' as ExplorerNodeType,
          path: [catalogPath, db, 'Views', v.name],
          database: db,
          metadata: [
            ...(v.definer ? [{ label: 'Definer', value: v.definer }] : []),
            ...(v.is_updatable ? [{ label: 'Updatable', value: v.is_updatable }] : []),
          ],
        }))
      : [emptyNode(`${db}-views`, 'Views')],
  })

  // MVs group
  children.push({
    id: `${db}-mvs`,
    label: 'Materialized Views',
    type: 'group',
    path: [catalogPath, db, 'Materialized Views'],
    database: db,
    metadata: [{ label: 'Count', value: String(data.materialized_views.length) }],
    children: data.materialized_views.length > 0
      ? data.materialized_views.map((m) => ({
          id: `${db}-mv-${m.name}`,
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
      : [emptyNode(`${db}-mvs`, 'Materialized Views')],
  })

  // Functions group
  children.push({
    id: `${db}-functions`,
    label: 'Functions',
    type: 'group',
    path: [catalogPath, db, 'Functions'],
    database: db,
    metadata: [{ label: 'Count', value: String(data.functions.length) }],
    children: data.functions.length > 0
      ? data.functions.map((f) => ({
          id: `${db}-fn-${f.name}`,
          label: f.name,
          type: 'function' as ExplorerNodeType,
          path: [catalogPath, db, 'Functions', f.name],
          database: db,
          metadata: [
            ...(f.routine_type ? [{ label: 'Type', value: f.routine_type }] : []),
            ...(f.definer ? [{ label: 'Definer', value: f.definer }] : []),
          ],
        }))
      : [emptyNode(`${db}-functions`, 'Functions')],
  })

  // Pipes group
  children.push({
    id: `${db}-pipes`,
    label: 'Pipes',
    type: 'group',
    path: [catalogPath, db, 'Pipes'],
    database: db,
    metadata: [{ label: 'Count', value: String(data.pipes.length) }],
    children: data.pipes.length > 0
      ? data.pipes.map((p) => ({
          id: `${db}-pipe-${p.name}`,
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
      : [emptyNode(`${db}-pipes`, 'Pipes')],
  })

  // Stages group
  children.push({
    id: `${db}-stages`,
    label: 'Stages',
    type: 'group',
    path: [catalogPath, db, 'Stages'],
    database: db,
    metadata: [{ label: 'Count', value: String(data.stages.length) }],
    children: data.stages.length > 0
      ? data.stages.map((s) => ({
          id: `${db}-stage-${s.name}`,
          label: s.name,
          type: 'stage' as ExplorerNodeType,
          path: [catalogPath, db, 'Stages', s.name],
          database: db,
          metadata: [
            ...(s.storage_connection ? [{ label: 'Connection', value: s.storage_connection }] : []),
            ...(s.base_prefix ? [{ label: 'Prefix', value: s.base_prefix }] : []),
          ],
        }))
      : [emptyNode(`${db}-stages`, 'Stages')],
  })

  return children
}

// ── Tree Node Component ───────────────────────────────────────

type TreeNodeRowProps = {
  node: ExplorerNode
  expandedIds: Set<string>
  selectedId: string
  onToggle: (id: string) => void
  onSelect: (id: string) => void
  onCreateStage?: (database: string) => void
}

function TreeNodeRow({
  node,
  expandedIds,
  selectedId,
  onToggle,
  onSelect,
  onCreateStage,
}: TreeNodeRowProps) {
  const hasChildren = Boolean(node.children?.length)
  const isExpanded = expandedIds.has(node.id)
  const isSelected = selectedId === node.id
  const Icon = getNodeIcon(node.type)

  // Empty placeholder node (e.g. "No Objects Found")
  if (node.label === 'No Objects Found') {
    return (
      <SidebarMenuItem key={node.id}>
        <div className='flex items-center gap-2 px-2 py-1.5 text-sm italic text-muted-foreground/60'>
          <span className='ml-4'>{node.label}</span>
        </div>
      </SidebarMenuItem>
    )
  }

  if (hasChildren) {
    const isStagesGroup = node.label === 'Stages' && node.type === 'group'

    const collapsibleContent = (
      <Collapsible
        key={node.id}
        open={isExpanded}
        onOpenChange={() => {
          onToggle(node.id)
          onSelect(node.id)
        }}
      >
        <SidebarMenuItem className='min-w-0'>
          <CollapsibleTrigger asChild>
            <button
              type='button'
              className={cn(
                'flex min-w-0 w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted',
                isSelected && 'bg-primary/10 font-medium text-primary',
              )}
            >
              <ChevronRight
                className={cn(
                  'size-4 shrink-0 transition-transform duration-200',
                  isExpanded && 'rotate-90',
                )}
              />
              <Icon
                className={cn(
                  'size-4 shrink-0',
                  isSelected ? 'text-primary' : 'text-muted-foreground',
                )}
              />
              <span className='flex-1 min-w-0 truncate text-left'>{node.label}</span>
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <SidebarMenuSub>
              {node.children?.map((child) => (
                <TreeNodeRow
                  key={child.id}
                  node={child}
                  expandedIds={expandedIds}
                  selectedId={selectedId}
                  onToggle={onToggle}
                  onSelect={onSelect}
                  onCreateStage={onCreateStage}
                />
              ))}
            </SidebarMenuSub>
          </CollapsibleContent>
        </SidebarMenuItem>
      </Collapsible>
    )

    if (isStagesGroup && node.database && onCreateStage) {
      return (
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div>{collapsibleContent}</div>
          </ContextMenuTrigger>
          <ContextMenuContent>
            <ContextMenuItem onClick={() => onCreateStage(node.database!)}>
              <Box className='mr-2 h-4 w-4' />
              Create Stage
            </ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>
      )
    }

    return collapsibleContent
  }

  return (
    <SidebarMenuItem key={node.id} className='min-w-0'>
      <button
        type='button'
        className={cn(
          'flex min-w-0 w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted',
          isSelected && 'bg-primary/10 font-medium text-primary',
        )}
        onClick={() => onSelect(node.id)}
      >
        <span className='flex h-4 w-4 shrink-0 items-center justify-center'>
          <span className='h-1.5 w-1.5 rounded-full bg-border' />
        </span>
        <Icon
          className={cn(
            'size-4 shrink-0',
            isSelected ? 'text-primary' : 'text-muted-foreground',
          )}
        />
        <span className='flex-1 min-w-0 truncate text-left'>{node.label}</span>
      </button>
    </SidebarMenuItem>
  )
}

// ── Detail Panel ──────────────────────────────────────────────

function ExplorerDetail({
  node,
  tableDetail,
  tableLoading,
  tableError,
  pipeDetail,
  pipeLoading,
  pipeError,
  viewDetail,
  viewLoading,
  viewError,
  mvDetail,
  mvLoading,
  mvError,
  fnDetail,
  fnLoading,
  fnError,
  catalogsData,
  dbCache,
}: {
  node: ExplorerNode | null
  tableDetail?: TableDetailResponse | null
  tableLoading?: boolean
  tableError?: string | null
  pipeDetail?: PipeDetailResponse | null
  pipeLoading?: boolean
  pipeError?: string | null
  viewDetail?: ViewDetailResponse | null
  viewLoading?: boolean
  viewError?: string | null
  mvDetail?: MVDetailResponse | null
  mvLoading?: boolean
  mvError?: string | null
  fnDetail?: FunctionDetailResponse | null
  fnLoading?: boolean
  fnError?: string | null
  catalogsData?: CatalogsResponse | null
  dbCache?: Map<string, DatabaseObjectsResponse>
}) {
  if (!node) {
    return (
      <div className='flex h-full min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-card/40'>
        <div className='space-y-2 text-center'>
          <div className='mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-muted'>
            <Blocks className='h-5 w-5 text-muted-foreground' />
          </div>
          <p className='text-sm font-medium'>No object selected</p>
          <p className='text-sm text-muted-foreground'>
            Select an item from the explorer tree to inspect its metadata.
          </p>
        </div>
      </div>
    )
  }

  const Icon = getNodeIcon(node.type)

  // Table detail view
  if (node.type === 'table' && tableDetail) {
    return (
      <div className='space-y-5'>
        <div className='flex flex-wrap items-center gap-3'>
          <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
            <Table2 className='h-5 w-5' />
          </div>
          <div className='space-y-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h1 className='text-3xl font-semibold tracking-tight'>{tableDetail.name}</h1>
              <Badge variant='secondary'>Table</Badge>
              {tableDetail.properties.table_model && (
                <Badge variant='outline'>{formatModel(tableDetail.properties.table_model)}</Badge>
              )}
            </div>
            <p className='text-sm text-muted-foreground'>
              {node.path.join(' / ')}
            </p>
          </div>
        </div>

        {/* Properties */}
        <section className='rounded-2xl border border-border bg-card/60 p-6'>
          <h2 className='mb-4 text-xl font-semibold'>Properties</h2>
          <div className='grid gap-x-6 gap-y-4 md:grid-cols-3'>
            <div className='space-y-1'>
              <p className='text-sm text-muted-foreground'>Table Model</p>
              <div className='text-base font-medium'>{formatModel(tableDetail.properties.table_model)}</div>
            </div>
            <div className='space-y-1'>
              <p className='text-sm text-muted-foreground'>Primary Key</p>
              <div className='text-base font-medium'>{stripBackticks(tableDetail.properties.primary_key)}</div>
            </div>
            <div className='space-y-1'>
              <p className='text-sm text-muted-foreground'>Distribution</p>
              <div className='text-base font-medium'>
                {tableDetail.properties.distribute_type
                  ? `${tableDetail.properties.distribute_type}(${stripBackticks(tableDetail.properties.distribute_key)})`
                  : '—'}
              </div>
            </div>
            <div className='space-y-1'>
              <p className='text-sm text-muted-foreground'>Buckets</p>
              <div className='text-base font-medium'>{tableDetail.properties.distribute_bucket ?? '—'}</div>
            </div>
            <div className='space-y-1'>
              <p className='text-sm text-muted-foreground'>Sort Key</p>
              <div className='text-base font-medium'>{stripBackticks(tableDetail.properties.sort_key)}</div>
            </div>
            <div className='space-y-1'>
              <p className='text-sm text-muted-foreground'>Row Count</p>
              <div className='text-base font-medium'>{tableDetail.row_count?.toLocaleString() ?? '—'}</div>
            </div>
            <div className='space-y-1'>
              <p className='text-sm text-muted-foreground'>Data Size</p>
              <div className='text-base font-medium'>{formatBytes(tableDetail.data_size)}</div>
            </div>
          </div>

          {/* Table properties */}
          {Object.keys(tableDetail.properties.properties).length > 0 && (
            <div className='mt-4 border-t border-border pt-4'>
              <p className='mb-2 text-sm font-medium text-muted-foreground'>Storage Properties</p>
              <div className='flex flex-wrap gap-2'>
                {Object.entries(tableDetail.properties.properties).map(([k, v]) => (
                  <Badge key={k} variant='outline' className='gap-1'>
                    <span className='text-muted-foreground'>{k}:</span> {v}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* Columns */}
        <section className='rounded-2xl border border-border bg-card/60 p-6'>
          <h2 className='mb-4 text-xl font-semibold'>
            Columns <span className='text-muted-foreground'>({tableDetail.columns.length})</span>
          </h2>
          <div className='overflow-x-auto'>
            <table className='w-full text-sm'>
              <thead>
                <tr className='border-b border-border text-left text-muted-foreground'>
                  <th className='pb-2 pr-4 font-medium'>Name</th>
                  <th className='pb-2 pr-4 font-medium'>Type</th>
                  <th className='pb-2 pr-4 font-medium'>Nullable</th>
                  <th className='pb-2 pr-4 font-medium'>Key</th>
                  <th className='pb-2 font-medium'>Default</th>
                </tr>
              </thead>
              <tbody>
                {tableDetail.columns.map((col) => (
                  <tr key={col.name} className='border-b border-border/50'>
                    <td className='py-2 pr-4 font-medium'>{col.name}</td>
                    <td className='py-2 pr-4 text-muted-foreground'>{col.column_type || col.data_type}</td>
                    <td className='py-2 pr-4 text-muted-foreground'>{col.is_nullable}</td>
                    <td className='py-2 pr-4'>
                      {col.column_key === 'PRI' ? (
                        <span title='Primary Key'><KeyRound className='h-4 w-4 text-primary' /></span>
                      ) : col.column_key === 'UNI' ? (
                        <span title='Unique Key'><KeyRound className='h-4 w-4 text-amber-500' /></span>
                      ) : col.column_key === 'MUL' ? (
                        <span title='Index'><KeyRound className='h-4 w-4 text-muted-foreground' /></span>
                      ) : (
                        <span className='text-muted-foreground'>—</span>
                      )}
                    </td>
                    <td className='py-2 text-muted-foreground'>{col.column_default || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* DDL */}
        {tableDetail.properties.create_ddl && (
          <section className='rounded-2xl border border-border bg-card/60 p-6'>
            <h2 className='mb-4 text-xl font-semibold'>DDL</h2>
            <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
              <code>{tableDetail.properties.create_ddl}</code>
            </pre>
          </section>
        )}
      </div>
    )
  }

  // Table loading/error state
  if (node.type === 'table') {
    if (tableLoading) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center'>
          <div className='flex items-center gap-2 text-sm text-muted-foreground'>
            <Loader2 className='h-5 w-5 animate-spin' />
            Loading table details...
          </div>
        </div>
      )
    }
    if (tableError) {
      return (
        <div className='flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive'>
          <AlertCircle className='h-4 w-4' />
          {tableError}
        </div>
      )
    }
  }

  // ── Catalog detail view ──────────────────────────────────
  if (node.type === 'catalog') {
    const catalog = catalogsData?.catalogs.find(
      (c) => c.name === 'default_catalog' || c.name === node.label,
    )
    const databases = catalog?.databases ?? []

    return (
      <div className='space-y-5'>
        <div className='flex flex-wrap items-center gap-3'>
          <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
            <FolderTree className='h-5 w-5' />
          </div>
          <div className='space-y-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h1 className='text-3xl font-semibold tracking-tight'>{node.label}</h1>
              <Badge variant='secondary'>Catalog</Badge>
              {catalog?.type && <Badge variant='outline'>{catalog.type}</Badge>}
            </div>
            {catalog?.comment && (
              <p className='max-w-2xl text-sm text-muted-foreground'>{catalog.comment}</p>
            )}
          </div>
        </div>

        <section className='rounded-2xl border border-border bg-card/60 p-6'>
          <h2 className='mb-4 text-xl font-semibold'>
            Databases <span className='text-muted-foreground'>({databases.length})</span>
          </h2>
          <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
            {databases.map((db) => {
              const cached = dbCache?.get(db)
              const tableCount = cached?.summary.tables ?? 0
              const viewCount = cached?.summary.views ?? 0
              const mvCount = cached?.summary.materialized_views ?? 0
              return (
                <div
                  key={db}
                  className='flex items-start gap-3 rounded-xl border border-border bg-background/60 p-4 transition-colors hover:bg-muted/30'
                >
                  <div className='flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary'>
                    <Database className='h-4 w-4' />
                  </div>
                  <div className='min-w-0 flex-1'>
                    <p className='truncate text-sm font-semibold'>{db}</p>
                    {cached ? (
                      <div className='mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground'>
                        {tableCount > 0 && <span>{tableCount} tables</span>}
                        {viewCount > 0 && <span>{viewCount} views</span>}
                        {mvCount > 0 && <span>{mvCount} MVs</span>}
                        {tableCount === 0 && viewCount === 0 && mvCount === 0 && <span>Empty</span>}
                      </div>
                    ) : (
                      <p className='mt-1 text-xs text-muted-foreground'>Expand to load</p>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      </div>
    )
  }

  // ── Database detail view ─────────────────────────────────
  if (node.type === 'database') {
    const dbName = node.database || node.label
    const cached = dbCache?.get(dbName)

    return (
      <div className='space-y-5'>
        <div className='flex flex-wrap items-center gap-3'>
          <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
            <Database className='h-5 w-5' />
          </div>
          <div className='space-y-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h1 className='text-3xl font-semibold tracking-tight'>{dbName}</h1>
              <Badge variant='secondary'>Database</Badge>
            </div>
            <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
          </div>
        </div>

        {cached ? (
          <>
            {/* Summary cards */}
            <section className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6'>
              {Object.entries(cached.summary).map(([key, count]) => (
                <div key={key} className='rounded-xl border border-border bg-card/60 p-4'>
                  <p className='text-xs text-muted-foreground'>{key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</p>
                  <p className='mt-1 text-2xl font-semibold'>{count}</p>
                </div>
              ))}
            </section>

            {/* Tables list */}
            {cached.tables.length > 0 && (
              <section className='rounded-2xl border border-border bg-card/60 p-6'>
                <h2 className='mb-4 text-xl font-semibold'>
                  Tables <span className='text-muted-foreground'>({cached.tables.length})</span>
                </h2>
                <div className='overflow-x-auto'>
                  <table className='w-full text-sm'>
                    <thead>
                      <tr className='border-b border-border text-left text-muted-foreground'>
                        <th className='pb-2 pr-4 font-medium'>Name</th>
                        <th className='pb-2 pr-4 font-medium'>Model</th>
                        <th className='pb-2 pr-4 font-medium'>Rows</th>
                        <th className='pb-2 pr-4 font-medium'>Size</th>
                        <th className='pb-2 font-medium'>Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cached.tables.map((t) => (
                        <tr key={t.name} className='border-b border-border/50'>
                          <td className='py-2 pr-4 font-medium'>{t.name}</td>
                          <td className='py-2 pr-4'>
                            <Badge variant='outline' className='text-xs'>{formatModel(t.table_model)}</Badge>
                          </td>
                          <td className='py-2 pr-4 text-muted-foreground'>{t.row_count?.toLocaleString() ?? '—'}</td>
                          <td className='py-2 pr-4 text-muted-foreground'>{formatBytes(t.data_size)}</td>
                          <td className='py-2 text-muted-foreground'>{t.create_time ? t.create_time.split('T')[0] : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* Views list */}
            {cached.views.length > 0 && (
              <section className='rounded-2xl border border-border bg-card/60 p-6'>
                <h2 className='mb-4 text-xl font-semibold'>
                  Views <span className='text-muted-foreground'>({cached.views.length})</span>
                </h2>
                <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
                  {cached.views.map((v) => (
                    <div key={v.name} className='flex items-center gap-3 rounded-xl border border-border bg-background/60 p-4'>
                      <Eye className='h-4 w-4 shrink-0 text-muted-foreground' />
                      <div className='min-w-0'>
                        <p className='truncate text-sm font-semibold'>{v.name}</p>
                        {v.definer && <p className='text-xs text-muted-foreground'>{v.definer}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* MVs list */}
            {cached.materialized_views.length > 0 && (
              <section className='rounded-2xl border border-border bg-card/60 p-6'>
                <h2 className='mb-4 text-xl font-semibold'>
                  Materialized Views <span className='text-muted-foreground'>({cached.materialized_views.length})</span>
                </h2>
                <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
                  {cached.materialized_views.map((m) => (
                    <div key={m.name} className='flex items-center gap-3 rounded-xl border border-border bg-background/60 p-4'>
                      <Layers3 className='h-4 w-4 shrink-0 text-muted-foreground' />
                      <div className='min-w-0'>
                        <p className='truncate text-sm font-semibold'>{m.name}</p>
                        <div className='mt-0.5 flex gap-2 text-xs text-muted-foreground'>
                          {m.refresh_type && <span>{m.refresh_type}</span>}
                          {m.is_active != null && <span>{m.is_active ? 'Active' : 'Inactive'}</span>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Functions list */}
            {cached.functions.length > 0 && (
              <section className='rounded-2xl border border-border bg-card/60 p-6'>
                <h2 className='mb-4 text-xl font-semibold'>
                  Functions <span className='text-muted-foreground'>({cached.functions.length})</span>
                </h2>
                <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
                  {cached.functions.map((f) => (
                    <div key={f.name} className='flex items-center gap-3 rounded-xl border border-border bg-background/60 p-4'>
                      <Sigma className='h-4 w-4 shrink-0 text-muted-foreground' />
                      <div className='min-w-0'>
                        <p className='truncate text-sm font-semibold'>{f.name}</p>
                        {f.routine_type && <p className='text-xs text-muted-foreground'>{f.routine_type}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Pipes list */}
            {cached.pipes.length > 0 && (
              <section className='rounded-2xl border border-border bg-card/60 p-6'>
                <h2 className='mb-4 text-xl font-semibold'>
                  Pipes <span className='text-muted-foreground'>({cached.pipes.length})</span>
                </h2>
                <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
                  {cached.pipes.map((p) => (
                    <div key={p.name} className='flex items-center gap-3 rounded-xl border border-border bg-background/60 p-4'>
                      <ArrowRightLeft className='h-4 w-4 shrink-0 text-muted-foreground' />
                      <div className='min-w-0'>
                        <p className='truncate text-sm font-semibold'>{p.name}</p>
                        <div className='mt-0.5 flex gap-2 text-xs text-muted-foreground'>
                          {p.state && <Badge variant='outline' className='text-xs'>{p.state}</Badge>}
                          {p.target_table && <span>→ {p.target_table}</span>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Stages list */}
            {cached.stages.length > 0 && (
              <section className='rounded-2xl border border-border bg-card/60 p-6'>
                <h2 className='mb-4 text-xl font-semibold'>
                  Stages <span className='text-muted-foreground'>({cached.stages.length})</span>
                </h2>
                <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
                  {cached.stages.map((s) => (
                    <div key={s.name} className='flex items-center gap-3 rounded-xl border border-border bg-background/60 p-4'>
                      <Box className='h-4 w-4 shrink-0 text-muted-foreground' />
                      <div className='min-w-0'>
                        <p className='truncate text-sm font-semibold'>@{s.name}</p>
                        {s.storage_connection && <p className='text-xs text-muted-foreground'>{s.storage_connection}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Empty database */}
            {cached.summary.tables === 0 && cached.summary.views === 0 && cached.summary.materialized_views === 0 && (
              <section className='rounded-2xl border border-dashed border-border bg-card/40 p-8 text-center'>
                <p className='text-sm text-muted-foreground'>This database has no objects yet.</p>
              </section>
            )}
          </>
        ) : (
          <section className='flex h-full min-h-[240px] items-center justify-center rounded-2xl border border-dashed border-border bg-card/40'>
            <div className='flex items-center gap-2 text-sm text-muted-foreground'>
              <Loader2 className='h-4 w-4 animate-spin' />
              Loading database objects...
            </div>
          </section>
        )}
      </div>
    )
  }

  // ── Stage detail view — file browser ─────────────────────
  if (node.type === 'stage') {
    return <StageFilesPanel node={node} />
  }

  // ── Pipe detail view ──────────────────────────────────────
  if (node.type === 'pipe') {
    if (pipeLoading) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center'>
          <div className='flex items-center gap-2 text-sm text-muted-foreground'>
            <Loader2 className='h-5 w-5 animate-spin' />
            Loading pipe details...
          </div>
        </div>
      )
    }
    if (pipeError) {
      return (
        <div className='flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive'>
          <AlertCircle className='h-4 w-4' />
          {pipeError}
        </div>
      )
    }
    if (pipeDetail) {
      const stateColor =
        pipeDetail.state === 'RUNNING'
          ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20'
          : pipeDetail.state === 'SUSPENDED'
            ? 'bg-amber-500/10 text-amber-600 border-amber-500/20'
            : pipeDetail.state === 'ERROR'
              ? 'bg-red-500/10 text-red-600 border-red-500/20'
              : ''

      return (
        <div className='space-y-5'>
          {/* Header */}
          <div className='flex flex-wrap items-center gap-3'>
            <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
              <ArrowRightLeft className='h-5 w-5' />
            </div>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h1 className='text-3xl font-semibold tracking-tight'>{pipeDetail.name}</h1>
                <Badge variant='secondary'>Pipe</Badge>
                {pipeDetail.state && (
                  <Badge variant='outline' className={stateColor}>
                    {pipeDetail.state}
                  </Badge>
                )}
              </div>
              <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
            </div>
          </div>

          {/* Properties */}
          <section className='rounded-2xl border border-border bg-card/60 p-6'>
            <h2 className='mb-4 text-xl font-semibold'>Properties</h2>
            <div className='grid gap-x-6 gap-y-4 md:grid-cols-3'>
              {pipeDetail.pipe_id != null && (
                <div className='space-y-1'>
                  <p className='text-sm text-muted-foreground'>Pipe ID</p>
                  <div className='text-base font-medium'>{pipeDetail.pipe_id}</div>
                </div>
              )}
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>State</p>
                <div className='text-base font-medium'>{pipeDetail.state || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Target Table</p>
                <div className='text-base font-medium'>{pipeDetail.target_table || '—'}</div>
              </div>
              <div className='space-y-1 col-span-2'>
                <p className='text-sm text-muted-foreground'>Load Status</p>
                <div className='flex flex-wrap gap-x-4 gap-y-1 text-sm'>
                  {(() => {
                    try {
                      const ls = typeof pipeDetail.load_status === 'string'
                        ? JSON.parse(pipeDetail.load_status)
                        : pipeDetail.load_status
                      return (
                        <>
                          {ls.loadedFiles != null && <span className='font-medium'>{ls.loadedFiles} files loaded</span>}
                          {ls.loadedBytes != null && <span className='text-muted-foreground'>{(ls.loadedBytes / 1024).toFixed(1)} KB</span>}
                          {ls.lastLoadedTime && <span className='text-muted-foreground'>Last: {ls.lastLoadedTime}</span>}
                        </>
                      )
                    } catch {
                      return <span className='font-medium break-all'>{pipeDetail.load_status || '—'}</span>
                    }
                  })()}
                </div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Created</p>
                <div className='text-base font-medium'>
                  {pipeDetail.created_time ? pipeDetail.created_time.split('T')[0] : '—'}
                </div>
              </div>
            </div>
          </section>

          {/* Pipe Properties (from PROPERTIES column) */}
          {Object.keys(pipeDetail.properties).length > 0 && (
            <section className='rounded-2xl border border-border bg-card/60 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>Pipe Configuration</h2>
              <div className='grid gap-x-6 gap-y-4 md:grid-cols-2'>
                {Object.entries(pipeDetail.properties).map(([k, v]) => (
                  <div key={k} className='space-y-1'>
                    <p className='text-sm text-muted-foreground'>
                      {k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                    </p>
                    <div className='text-base font-medium break-all'>{v}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Last Error */}
          {pipeDetail.last_error && (
            <section className='rounded-2xl border border-destructive/30 bg-destructive/5 p-6'>
              <h2 className='mb-3 flex items-center gap-2 text-xl font-semibold text-destructive'>
                <AlertCircle className='h-5 w-5' />
                Last Error
              </h2>
              <pre className='overflow-x-auto whitespace-pre-wrap rounded-lg bg-destructive/5 p-4 text-sm leading-relaxed text-destructive'>
                {pipeDetail.last_error}
              </pre>
            </section>
          )}

          {/* DDL */}
          {pipeDetail.create_ddl && (
            <section className='rounded-2xl border border-border bg-card/60 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>DDL</h2>
              <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
                <code>{pipeDetail.create_ddl}</code>
              </pre>
            </section>
          )}
        </div>
      )
    }
  }

  // ── View detail view ───────────────────────────────────
  if (node.type === 'view') {
    if (viewLoading) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center'>
          <div className='flex items-center gap-2 text-sm text-muted-foreground'>
            <Loader2 className='h-5 w-5 animate-spin' />
            Loading view details...
          </div>
        </div>
      )
    }
    if (viewError) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center rounded-2xl border border-destructive/30 bg-destructive/5 p-6'>
          <div className='flex items-center gap-2 text-sm text-destructive'>
            <AlertCircle className='h-4 w-4' />
            {viewError}
          </div>
        </div>
      )
    }
    if (viewDetail) {
      return (
        <div className='space-y-5'>
          {/* Header */}
          <div className='flex flex-wrap items-center gap-3'>
            <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
              <Eye className='h-5 w-5' />
            </div>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h1 className='text-3xl font-semibold tracking-tight'>{viewDetail.name}</h1>
                <Badge variant='secondary'>View</Badge>
                {viewDetail.is_updatable === 'YES' && (
                  <Badge variant='outline'>Updatable</Badge>
                )}
              </div>
              <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
            </div>
          </div>

          {/* Properties */}
          <section className='rounded-2xl border border-border bg-card/60 p-6'>
            <h2 className='mb-4 text-xl font-semibold'>Properties</h2>
            <div className='grid gap-x-6 gap-y-4 md:grid-cols-3'>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Definer</p>
                <div className='text-base font-medium'>{viewDetail.definer || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Security Type</p>
                <div className='text-base font-medium'>{viewDetail.security_type || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Updatable</p>
                <div className='text-base font-medium'>{viewDetail.is_updatable || '—'}</div>
              </div>
            </div>
          </section>

          {/* DDL */}
          {viewDetail.create_ddl && (
            <section className='rounded-2xl border border-border bg-card/60 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>DDL</h2>
              <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
                <code>{viewDetail.create_ddl}</code>
              </pre>
            </section>
          )}

          {/* Definition (fallback if no DDL) */}
          {!viewDetail.create_ddl && viewDetail.definition && (
            <section className='rounded-2xl border border-border bg-card/60 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>Definition</h2>
              <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
                <code>{viewDetail.definition}</code>
              </pre>
            </section>
          )}
        </div>
      )
    }
  }

  // ── Materialized View detail view ──────────────────────
  if (node.type === 'materialized_view') {
    if (mvLoading) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center'>
          <div className='flex items-center gap-2 text-sm text-muted-foreground'>
            <Loader2 className='h-5 w-5 animate-spin' />
            Loading materialized view details...
          </div>
        </div>
      )
    }
    if (mvError) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center rounded-2xl border border-destructive/30 bg-destructive/5 p-6'>
          <div className='flex items-center gap-2 text-sm text-destructive'>
            <AlertCircle className='h-4 w-4' />
            {mvError}
          </div>
        </div>
      )
    }
    if (mvDetail) {
      const activeColor =
        mvDetail.is_active === 'true'
          ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20'
          : 'bg-amber-500/10 text-amber-600 border-amber-500/20'

      return (
        <div className='space-y-5'>
          {/* Header */}
          <div className='flex flex-wrap items-center gap-3'>
            <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
              <Layers3 className='h-5 w-5' />
            </div>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h1 className='text-3xl font-semibold tracking-tight'>{mvDetail.name}</h1>
                <Badge variant='secondary'>Materialized View</Badge>
                {mvDetail.is_active && (
                  <Badge variant='outline' className={activeColor}>
                    {mvDetail.is_active === 'true' ? 'Active' : 'Inactive'}
                  </Badge>
                )}
              </div>
              <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
            </div>
          </div>

          {/* Properties */}
          <section className='rounded-2xl border border-border bg-card/60 p-6'>
            <h2 className='mb-4 text-xl font-semibold'>Properties</h2>
            <div className='grid gap-x-6 gap-y-4 md:grid-cols-3'>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Refresh Type</p>
                <div className='text-base font-medium'>{mvDetail.refresh_type || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Last Refresh State</p>
                <div className='text-base font-medium'>{mvDetail.last_refresh_state || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Task Name</p>
                <div className='text-base font-medium'>{mvDetail.task_name || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Table Rows</p>
                <div className='text-base font-medium'>{mvDetail.table_rows ?? '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Query Rewrite</p>
                <div className='text-base font-medium'>{mvDetail.query_rewrite_status || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Creator</p>
                <div className='text-base font-medium'>{mvDetail.creator || '—'}</div>
              </div>
              {mvDetail.last_refresh_duration != null && (
                <div className='space-y-1'>
                  <p className='text-sm text-muted-foreground'>Last Refresh Duration</p>
                  <div className='text-base font-medium'>{mvDetail.last_refresh_duration}ms</div>
                </div>
              )}
            </div>
          </section>

          {/* Last Refresh Error */}
          {mvDetail.last_refresh_error_message && (
            <section className='rounded-2xl border border-destructive/30 bg-destructive/5 p-6'>
              <h2 className='mb-3 flex items-center gap-2 text-xl font-semibold text-destructive'>
                <AlertCircle className='h-5 w-5' />
                Last Refresh Error
              </h2>
              <pre className='overflow-x-auto whitespace-pre-wrap rounded-lg bg-destructive/5 p-4 text-sm leading-relaxed text-destructive'>
                {mvDetail.last_refresh_error_message}
              </pre>
            </section>
          )}

          {/* DDL / Definition */}
          {mvDetail.definition && (
            <section className='rounded-2xl border border-border bg-card/60 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>Definition</h2>
              <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
                <code>{mvDetail.definition}</code>
              </pre>
            </section>
          )}
        </div>
      )
    }
  }

  // ── Function detail view ─────────────────────────────
  if (node.type === 'function') {
    if (fnLoading) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center'>
          <div className='flex items-center gap-2 text-sm text-muted-foreground'>
            <Loader2 className='h-5 w-5 animate-spin' />
            Loading function details...
          </div>
        </div>
      )
    }
    if (fnError) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center rounded-2xl border border-destructive/30 bg-destructive/5 p-6'>
          <div className='flex items-center gap-2 text-sm text-destructive'>
            <AlertCircle className='h-4 w-4' />
            {fnError}
          </div>
        </div>
      )
    }
    if (fnDetail) {
      return (
        <div className='space-y-5'>
          {/* Header */}
          <div className='flex flex-wrap items-center gap-3'>
            <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
              <Sigma className='h-5 w-5' />
            </div>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h1 className='text-3xl font-semibold tracking-tight'>{fnDetail.name}</h1>
                <Badge variant='secondary'>{fnDetail.function_type || 'Function'}</Badge>
                {fnDetail.return_type && (
                  <Badge variant='outline'>→ {fnDetail.return_type}</Badge>
                )}
              </div>
              <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
            </div>
          </div>

          {/* Properties */}
          <section className='rounded-2xl border border-border bg-card/60 p-6'>
            <h2 className='mb-4 text-xl font-semibold'>Properties</h2>
            <div className='grid gap-x-6 gap-y-4 md:grid-cols-3'>
              {fnDetail.signature && (
                <div className='space-y-1'>
                  <p className='text-sm text-muted-foreground'>Signature</p>
                  <div className='text-base font-medium font-mono'>{fnDetail.signature}</div>
                </div>
              )}
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Return Type</p>
                <div className='text-base font-medium'>{fnDetail.return_type || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Definer</p>
                <div className='text-base font-medium'>{fnDetail.definer || '—'}</div>
              </div>
              <div className='space-y-1'>
                <p className='text-sm text-muted-foreground'>Deterministic</p>
                <div className='text-base font-medium'>{fnDetail.is_deterministic || '—'}</div>
              </div>
              {fnDetail.created && (
                <div className='space-y-1'>
                  <p className='text-sm text-muted-foreground'>Created</p>
                  <div className='text-base font-medium'>{String(fnDetail.created).split('T')[0]}</div>
                </div>
              )}
            </div>
          </section>

          {/* DDL */}
          {fnDetail.create_ddl && (
            <section className='rounded-2xl border border-border bg-card/60 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>DDL</h2>
              <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
                <code>{fnDetail.create_ddl}</code>
              </pre>
            </section>
          )}

          {/* Definition fallback */}
          {!fnDetail.create_ddl && fnDetail.definition && (
            <section className='rounded-2xl border border-border bg-card/60 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>Definition</h2>
              <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
                <code>{fnDetail.definition}</code>
              </pre>
            </section>
          )}
        </div>
      )
    }
  }

  // ── Group / generic detail view ──────────────────────────
  const childCount = node.children?.length ?? 0

  return (
    <div className='space-y-5'>
      <div className='flex flex-wrap items-center gap-3'>
        <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
          <Icon className='h-5 w-5' />
        </div>
        <div className='space-y-1'>
          <div className='flex flex-wrap items-center gap-2'>
            <h1 className='text-3xl font-semibold tracking-tight'>{node.label}</h1>
            <Badge variant='secondary'>{getNodeTypeLabel(node.type)}</Badge>
          </div>
          <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
        </div>
      </div>

      <div className='border-b border-border'>
        <div className='inline-flex border-b-2 border-primary px-1 py-3 text-sm font-medium text-primary'>
          Object Details
        </div>
      </div>

      <section className='rounded-2xl border border-border bg-card/60 p-6'>
        <h2 className='mb-4 text-xl font-semibold'>Overview</h2>
        <div className='grid gap-x-6 gap-y-4 md:grid-cols-2'>
          {node.metadata.map((item) => (
            <div key={item.label} className='space-y-1'>
              <p className='text-sm text-muted-foreground'>{item.label}</p>
              <div className='text-base font-medium'>{item.value}</div>
            </div>
          ))}
          {childCount > 0 && (
            <div className='space-y-1'>
              <p className='text-sm text-muted-foreground'>Direct children</p>
              <div className='text-base font-medium'>{childCount}</div>
            </div>
          )}
        </div>
      </section>

      {/* Show children list for groups */}
      {node.type === 'group' && node.children && node.children.length > 0 && (
        <section className='rounded-2xl border border-border bg-card/60 p-6'>
          <h2 className='mb-4 text-xl font-semibold'>
            Objects <span className='text-muted-foreground'>({node.children.length})</span>
          </h2>
          <div className='grid gap-2'>
            {node.children.map((child) => {
              const ChildIcon = getNodeIcon(child.type)
              return (
                <div key={child.id} className='flex items-center gap-3 rounded-lg border border-border/50 bg-background/60 px-4 py-2.5'>
                  <ChildIcon className='h-4 w-4 shrink-0 text-muted-foreground' />
                  <div className='min-w-0 flex-1'>
                    <p className='truncate text-sm font-medium'>{child.label}</p>
                    {child.metadata.length > 0 && (
                      <p className='truncate text-xs text-muted-foreground'>
                        {child.metadata.map((m) => `${m.label}: ${m.value}`).join(' · ')}
                      </p>
                    )}
                  </div>
                  <Badge variant='outline' className='shrink-0 text-xs'>
                    {getNodeTypeLabel(child.type)}
                  </Badge>
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}

// ── Stage Files Panel ─────────────────────────────────────────

type StageFile = {
  name: string
  size: number
  last_modified: string | null
  is_dir: boolean
}

function StageFilesPanel({ node }: { node: ExplorerNode }) {
  const queryClient = useQueryClient()
  const stageName = node.label.replace(/^@/, '')
  const database = node.database || ''

  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [uploadFolder, setUploadFolder] = useState('')
  const [currentPath, setCurrentPath] = useState<string[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const pathPrefix = currentPath.join('/')

  // Fetch files with current path prefix
  const {
    data: filesData,
    isLoading: filesLoading,
    isFetching: filesFetching,
    error: filesError,
    refetch: refetchFiles,
  } = useQuery<{ files: StageFile[]; count: number }>({
    queryKey: ['explorer-stage-files', database, stageName, pathPrefix],
    queryFn: () =>
      api.get(
        `/explorer/databases/${database}/stages/${stageName}/files${pathPrefix ? `?prefix=${encodeURIComponent(pathPrefix)}` : ''}`,
      ),
    enabled: !!database && !!stageName,
  })

  const handleRefreshFiles = useCallback(async () => {
    setRefreshing(true)
    await refetchFiles()
    setRefreshing(false)
  }, [refetchFiles])

  const files = filesData?.files ?? []

  // Upload handler
  const handleUpload = useCallback(async (filesToUpload: File[]) => {
    if (!filesToUpload.length) return
    setUploading(true)
    try {
      const extraFolder = uploadFolder.trim() ? `${uploadFolder.trim().replace(/^\//, '')}/` : ''
      const basePath = currentPath.length > 0 ? `${currentPath.join('/')}/` : ''
      const fullPrefix = `${basePath}${extraFolder}`
      for (const file of filesToUpload) {
        const formData = new FormData()
        formData.append('file', file)
        formData.append('filename', `${fullPrefix}${file.name}`)
        await api.upload(`/explorer/databases/${database}/stages/${stageName}/files`, formData)
      }
      const destLabel = fullPrefix ? ` to /${fullPrefix.replace(/\/$/, '')}` : ''
      toast.success(`${filesToUpload.length} file(s) uploaded${destLabel}`)
      setUploadOpen(false)
      setPendingFiles([])
      setUploadFolder('')
      queryClient.invalidateQueries({ queryKey: ['explorer-stage-files', database, stageName] })
    } catch (err) {
      toast.error(`Upload failed: ${String(err)}`)
    } finally {
      setUploading(false)
    }
  }, [database, stageName, queryClient, uploadFolder, currentPath])

  // Delete handler
  const handleDelete = useCallback(async (filename: string) => {
    try {
      const targetName = currentPath.length > 0 ? `${currentPath.join('/')}/${filename}` : filename
      await api.delete(`/explorer/databases/${database}/stages/${stageName}/files/${targetName}`)
      toast.success(`Deleted "${filename}"`)
      queryClient.invalidateQueries({ queryKey: ['explorer-stage-files', database, stageName] })
    } catch (err) {
      toast.error(`Delete failed: ${String(err)}`)
    }
    setDeleteTarget(null)
  }, [database, stageName, queryClient, currentPath])

  // Drop handlers
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const droppedFiles = Array.from(e.dataTransfer.files)
    if (droppedFiles.length) {
      setPendingFiles((prev) => [...prev, ...droppedFiles])
    }
  }, [])

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files || [])
    if (selected.length) {
      setPendingFiles((prev) => [...prev, ...selected])
    }
  }, [])

  return (
    <div className='space-y-5'>
      {/* Header */}
      <div className='flex flex-wrap items-center gap-3'>
        <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
          <Box className='h-5 w-5' />
        </div>
        <div className='flex-1 space-y-1'>
          <div className='flex flex-wrap items-center gap-2'>
            <h1 className='text-3xl font-semibold tracking-tight'>{stageName}</h1>
            <Badge variant='secondary'>Stage</Badge>
          </div>
          <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
        </div>
        <Button size='sm' onClick={() => { setPendingFiles([]); setUploadFolder(''); setUploadOpen(true) }}>
          <Upload className='mr-2 h-4 w-4' />
          Upload
        </Button>
      </div>

      {/* Tab */}
      <div className='border-b border-border'>
        <div className='inline-flex border-b-2 border-primary px-1 py-3 text-sm font-medium text-primary'>
          Stage Files
        </div>
      </div>

      {/* Breadcrumb navigation */}
      {currentPath.length > 0 && (
        <div className='flex items-center gap-1 text-sm'>
          <button
            type='button'
            onClick={() => setCurrentPath([])}
            className='text-muted-foreground hover:text-foreground transition-colors'
          >
            {stageName}
          </button>
          {currentPath.map((seg, i) => (
            <Fragment key={i}>
              <ChevronRight className='h-3.5 w-3.5 text-muted-foreground/60' />
              {i === currentPath.length - 1 ? (
                <span className='font-medium'>{seg}</span>
              ) : (
                <button
                  type='button'
                  onClick={() => setCurrentPath(currentPath.slice(0, i + 1))}
                  className='text-muted-foreground hover:text-foreground transition-colors'
                >
                  {seg}
                </button>
              )}
            </Fragment>
          ))}
        </div>
      )}

      {/* Files list */}
      <section>
        {filesLoading ? (
          <div className='flex items-center gap-2 p-6 text-sm text-muted-foreground'>
            <Loader2 className='h-4 w-4 animate-spin' />
            Loading files...
          </div>
        ) : filesError ? (
          <div className='flex items-center gap-2 p-6 text-sm text-destructive'>
            <AlertCircle className='h-4 w-4' />
            Failed to load files: {String(filesError)}
          </div>
        ) : files.length === 0 ? (
          <div className='flex flex-col items-center gap-2 py-12 text-center'>
            <Box className='h-10 w-10 text-muted-foreground/40' />
            <p className='text-sm font-medium'>
              {currentPath.length > 0 ? 'This folder is empty' : 'No files yet'}
            </p>
            <p className='text-xs text-muted-foreground'>
              {currentPath.length === 0 && (
                <>Upload files or use <code className='rounded bg-muted px-1 py-0.5 text-xs'>SELECT * FROM @{stageName}.your_file.csv</code> to query.</>
              )}
            </p>
          </div>
        ) : (
          <>
          <div className='flex items-center justify-between px-4 py-2'>
            <div className='flex items-center gap-2'>
              <span className='text-xs text-muted-foreground'>
                {files.filter((f: StageFile) => !f.is_dir).length} file{files.filter((f: StageFile) => !f.is_dir).length !== 1 ? 's' : ''}
                {files.filter((f: StageFile) => f.is_dir).length > 0 && `, ${files.filter((f: StageFile) => f.is_dir).length} folder${files.filter((f: StageFile) => f.is_dir).length !== 1 ? 's' : ''}`}
              </span>
              <span className='text-xs text-muted-foreground'>·</span>
              <span className='text-xs text-muted-foreground'>
                {formatBytes(files.filter((f: StageFile) => !f.is_dir).reduce((sum: number, f: StageFile) => sum + f.size, 0))} total
              </span>
            </div>
            <button
              type='button'
              onClick={handleRefreshFiles}
              disabled={refreshing || filesFetching}
              className='rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50'
              aria-label='Refresh files'
            >
              <RefreshCw className={cn('h-3.5 w-3.5 transition-transform', (refreshing || filesFetching) && 'animate-spin')} />
            </button>
          </div>
          <table className='w-full'>
            <thead>
              <tr className='border-b border-border text-left text-xs font-medium uppercase tracking-wider text-muted-foreground'>
                <th className='px-4 py-3'>Name</th>
                <th className='w-28 px-4 py-3 text-right'>Size</th>
                <th className='w-52 px-4 py-3 text-right'>Last Modified</th>
                <th className='w-12 px-4 py-3' />
              </tr>
            </thead>
            <tbody className='divide-y divide-border'>
              {/* Back row when inside a folder */}
              {currentPath.length > 0 && (
                <tr
                  className='cursor-pointer hover:bg-muted/30'
                  onClick={() => setCurrentPath(currentPath.slice(0, -1))}
                >
                  <td className='px-4 py-3'>
                    <div className='flex items-center gap-2'>
                      <ChevronRight className='h-4 w-4 rotate-180 text-muted-foreground' />
                      <span className='text-sm text-muted-foreground'>..</span>
                    </div>
                  </td>
                  <td /><td /><td />
                </tr>
              )}
              {files.map((file: StageFile) => (
                <tr
                  key={file.name}
                  className={cn('hover:bg-muted/30', file.is_dir && 'cursor-pointer')}
                  onClick={file.is_dir ? () => setCurrentPath([...currentPath, file.name]) : undefined}
                >
                  <td className='px-4 py-3'>
                    <div className='flex min-w-0 items-center gap-2'>
                      {file.is_dir ? (
                        <FolderOpen className='h-4 w-4 shrink-0 text-muted-foreground' />
                      ) : (
                        <FileText className='h-4 w-4 shrink-0 text-muted-foreground' />
                      )}
                      <span className='truncate text-sm font-medium'>{file.name}</span>
                    </div>
                  </td>
                  <td className='px-4 py-3 text-right text-sm text-muted-foreground'>
                    {file.is_dir ? '—' : formatBytes(file.size)}
                  </td>
                  <td className='px-4 py-3 text-right text-sm text-muted-foreground'>
                    {file.last_modified
                      ? new Date(file.last_modified).toLocaleDateString('en-US', {
                          year: 'numeric', month: 'short', day: 'numeric',
                          hour: '2-digit', minute: '2-digit',
                        })
                      : '—'}
                  </td>
                  <td className='px-4 py-3 text-right'>
                    {!file.is_dir && (
                      <button
                        type='button'
                        onClick={(e) => { e.stopPropagation(); setDeleteTarget(file.name) }}
                        className='rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive'
                      >
                        <Trash2 className='h-3.5 w-3.5' />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </>
        )}
      </section>

      {/* Upload Dialog */}
      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent className='sm:max-w-lg'>
          <DialogHeader>
            <DialogTitle>Upload to @{stageName}</DialogTitle>
            <DialogDescription>
              Drag and drop files or browse to upload to this stage.
            </DialogDescription>
          </DialogHeader>

          <div className='space-y-4 py-2'>
            {/* Drop zone */}
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                'flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 transition-colors',
                dragOver
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:border-muted-foreground/50',
              )}
            >
              <Upload className={cn('mb-3 h-8 w-8', dragOver ? 'text-primary' : 'text-muted-foreground/60')} />
              <p className='text-sm font-medium'>Drop files here or click to browse</p>
              <p className='mt-1 text-xs text-muted-foreground'>CSV, JSON, Parquet, ORC, Avro</p>
              <input
                ref={fileInputRef}
                type='file'
                multiple
                className='hidden'
                onChange={handleFileInput}
              />
            </div>

            {/* Folder name input */}
            <div className='space-y-1.5'>
              <Label htmlFor='upload-folder' className='text-sm'>
                Folder <span className='font-normal text-muted-foreground'>(optional)</span>
              </Label>
              <div className='flex items-center rounded-md border border-border'>
                <input
                  id='upload-folder'
                  type='text'
                  placeholder='/e.g. data/import'
                  value={uploadFolder}
                  onChange={(e) => {
                    let val = e.target.value
                    if (val && !val.startsWith('/')) {
                      val = '/' + val
                    }
                    setUploadFolder(val)
                  }}
                  onKeyDown={(e) => {
                    if (!uploadFolder && e.key.length === 1 && e.key !== '/') {
                      setUploadFolder('/' + e.key)
                      e.preventDefault()
                    }
                  }}
                  className='flex-1 bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60'
                />
              </div>
              {uploadFolder && (
                <p className='text-xs text-muted-foreground'>
                  Files will be uploaded to <code className='rounded bg-muted px-1 py-0.5 text-xs'>{uploadFolder.replace(/^\//, '')}/</code>
                </p>
              )}
            </div>

            {/* Pending files list */}
            {pendingFiles.length > 0 && (
              <div className='space-y-2'>
                <p className='text-sm font-medium'>Files to upload ({pendingFiles.length})</p>
                <div className='max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2'>
                  {pendingFiles.map((f, i) => (
                    <div key={i} className='flex items-center justify-between rounded px-2 py-1 text-sm hover:bg-muted/50'>
                      <div className='flex min-w-0 items-center gap-2'>
                        <FileText className='h-3.5 w-3.5 shrink-0 text-muted-foreground' />
                        <span className='truncate'>{f.name}</span>
                      </div>
                      <div className='flex items-center gap-2'>
                        <span className='text-xs text-muted-foreground'>{formatBytes(f.size)}</span>
                        <button
                          type='button'
                          onClick={() => setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))}
                          className='rounded p-0.5 text-muted-foreground hover:text-destructive'
                        >
                          <Trash2 className='h-3 w-3' />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant='outline' onClick={() => { setUploadOpen(false); setPendingFiles([]); setUploadFolder('') }}>
              Cancel
            </Button>
            <Button
              onClick={() => handleUpload(pendingFiles)}
              disabled={pendingFiles.length === 0 || uploading}
            >
              {uploading ? (
                <>
                  <Loader2 className='mr-2 h-4 w-4 animate-spin' />
                  Uploading...
                </>
              ) : (
                <>
                  <Upload className='mr-2 h-4 w-4' />
                  Upload {pendingFiles.length > 0 ? `(${pendingFiles.length})` : ''}
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete File</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete <strong>{deleteTarget}</strong>? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setDeleteTarget(null)}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteTarget && handleDelete(deleteTarget)}
              className='bg-destructive text-destructive-foreground hover:bg-destructive/90'
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// ── Resizable Sidebar ──────────────────────────────────────

interface ResizableSidebarProps {
  minWidth: number
  maxWidth: number
  defaultWidth: number
  autoCloseThreshold: number
  searchQuery: string
  setSearchQuery: (v: string) => void
  catalogsLoading: boolean
  isRefreshing: boolean
  catalogsError: unknown
  filteredTree: ExplorerNode[]
  expandedIds: Set<string>
  selectedId: string
  toggleExpanded: (id: string) => void
  setSelectedId: (id: string) => void
  dbLoading: boolean
  activeDb: string | null
  handleRefresh: () => void
  onCreateStage: (database: string) => void
}

function ResizableExplorerSidebar({
  minWidth,
  maxWidth,
  defaultWidth,
  autoCloseThreshold,
  searchQuery,
  setSearchQuery,
  catalogsLoading,
  isRefreshing,
  catalogsError,
  filteredTree,
  expandedIds,
  selectedId,
  toggleExpanded,
  setSelectedId,
  dbLoading,
  activeDb,
  handleRefresh,
  onCreateStage,
}: ResizableSidebarProps) {
  const [width, setWidth] = useState(defaultWidth)
  const [collapsed, setCollapsed] = useState(false)
  const [animating, setAnimating] = useState(false)
  const isResizing = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(0)

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isResizing.current = true
    startX.current = e.clientX
    startWidth.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [width])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing.current) return
      const delta = e.clientX - startX.current
      const newWidth = startWidth.current + delta

      if (newWidth < autoCloseThreshold) {
        // Trigger smooth collapse animation
        isResizing.current = false
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        setAnimating(true)
        setWidth(0)
        setTimeout(() => {
          setCollapsed(true)
          setAnimating(false)
        }, 200) // match CSS transition duration
      } else {
        setWidth(Math.min(Math.max(newWidth, minWidth), maxWidth))
      }
    }

    const handleMouseUp = () => {
      if (isResizing.current) {
        isResizing.current = false
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [autoCloseThreshold, minWidth, maxWidth])

  const handleOpen = useCallback(() => {
    setCollapsed(false)
    setWidth(0)
    setAnimating(true)
    // Animate open after mount
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        setWidth(defaultWidth)
        setTimeout(() => setAnimating(false), 200)
      })
    })
  }, [defaultWidth])

  const handleClose = useCallback(() => {
    setAnimating(true)
    setWidth(0)
    setTimeout(() => {
      setCollapsed(true)
      setAnimating(false)
    }, 200)
  }, [])

  // Collapsed state — show toggle button on left edge of content
  if (collapsed && !animating) {
    return (
      <button
        type='button'
        onClick={handleOpen}
        className='group z-10 mt-3 ml-1 flex h-7 w-6 shrink-0 items-center justify-center rounded-r-md border border-l-0 border-border bg-background shadow-sm transition-colors hover:bg-muted'
        aria-label='Open explorer sidebar'
      >
        <PanelLeftOpen className='h-3.5 w-3.5 text-muted-foreground transition-colors group-hover:text-foreground' />
      </button>
    )
  }

  return (
    <>
      <aside
        className={cn(
          'flex min-h-0 flex-shrink-0 flex-col border-r bg-muted/20 overflow-hidden',
          (animating || !isResizing.current) && 'transition-[width] duration-200 ease-in-out',
        )}
        style={{ width: `${width}px` }}
      >
        <div className='flex items-center gap-2 border-b px-3 py-2'>
          <div className='relative flex-1'>
            <Search className='pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground' />
            <Input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder='Search objects...'
              className='h-8 pl-8 text-sm'
            />
          </div>
          <button
            type='button'
            onClick={handleRefresh}
            disabled={isRefreshing || catalogsLoading}
            className='inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50'
            aria-label='Refresh'
          >
            <RefreshCw className={cn('h-3.5 w-3.5 transition-transform', (isRefreshing || catalogsLoading) && 'animate-spin')} />
          </button>
        </div>

        <div className='min-h-0 flex-1 overflow-y-auto px-3 py-2'>
          {catalogsLoading ? (
            <div className='flex h-full min-h-[240px] items-center justify-center'>
              <Loader2 className='h-5 w-5 animate-spin text-muted-foreground' />
            </div>
          ) : catalogsError ? (
            <div className='flex h-full min-h-[240px] items-center justify-center rounded-lg border border-dashed border-border px-4 text-center'>
              <div className='space-y-1'>
                <p className='text-xs font-medium text-destructive'>Failed to load catalogs</p>
                <p className='text-xs text-muted-foreground'>
                  {String(catalogsError)}
                </p>
              </div>
            </div>
          ) : filteredTree.length === 0 ? (
            <div className='flex h-full min-h-[240px] items-center justify-center rounded-lg border border-dashed border-border px-4 text-center'>
              <div className='space-y-1'>
                <p className='text-xs font-medium'>No objects found</p>
                <p className='text-xs text-muted-foreground'>
                  Try a different search term.
                </p>
              </div>
            </div>
          ) : (
            <SidebarMenu>
              {filteredTree.map((node) => (
                <TreeNodeRow
                  key={node.id}
                  node={node}
                  expandedIds={expandedIds}
                  selectedId={selectedId}
                  onToggle={toggleExpanded}
                  onSelect={setSelectedId}
                  onCreateStage={onCreateStage}
                />
              ))}
            </SidebarMenu>
          )}

          {dbLoading && activeDb && (
            <div className='flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground'>
              <Loader2 className='h-3 w-3 animate-spin' />
              Loading {activeDb}...
            </div>
          )}
        </div>
      </aside>

      {/* Resize handle */}
      <div
        onMouseDown={handleMouseDown}
        className='group relative z-10 w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-primary/30 active:bg-primary/50'
      >
        <div className='absolute inset-y-0 -left-1 -right-1' />
      </div>
    </>
  )
}

// ── Main Page ─────────────────────────────────────────────────

export function DatabaseExplorerPage() {
  const queryClient = useQueryClient()
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set())
  const [selectedId, setSelectedId] = useState('')
  const [isRefreshing, setIsRefreshing] = useState(false)

  // Create Stage dialog state
  const [createStageOpen, setCreateStageOpen] = useState(false)
  const [createStageDb, setCreateStageDb] = useState('')
  const [createStageName, setCreateStageName] = useState('')
  const [createStageLoading, setCreateStageLoading] = useState(false)

  const handleOpenCreateStage = useCallback((database: string) => {
    setCreateStageDb(database)
    setCreateStageName('')
    setCreateStageOpen(true)
  }, [])

  const handleCreateStage = useCallback(async () => {
    if (!createStageName.trim() || !createStageDb) return
    setCreateStageLoading(true)
    try {
      await api.post('/stages', {
        name: createStageName.trim(),
        database_name: createStageDb,
        schema_name: createStageDb,
        storage_connection: 'production',
        base_prefix: '',
      })
      toast.success(`Stage "${createStageName}" created`)
      setCreateStageOpen(false)
      // Invalidate React Query cache for this database so it refetches
      queryClient.invalidateQueries({ queryKey: ['explorer-db', createStageDb] })
      // Remove from dbCache so the tree rebuilds and triggers re-fetch
      setDbCache((prev) => {
        const next = new Map(prev)
        next.delete(createStageDb)
        return next
      })
    } catch (err) {
      toast.error(`Failed to create stage: ${String(err)}`)
    } finally {
      setCreateStageLoading(false)
    }
  }, [createStageName, createStageDb])

  // Fetch catalogs on mount
  const { data: catalogsData, isLoading: catalogsLoading, error: catalogsError, refetch: refetchCatalogs } = useQuery<CatalogsResponse>({
    queryKey: ['explorer-catalogs'],
    queryFn: () => api.get('/explorer/catalogs'),
  })

  // Build tree from catalog data
  const catalogTree = useMemo(() => {
    if (!catalogsData) return []
    return catalogsData.catalogs.map(buildCatalogTree)
  }, [catalogsData])

  // Auto-expand first catalog on load and select it
  const [initialExpandDone, setInitialExpandDone] = useState(false)

  if (catalogTree.length > 0 && !initialExpandDone) {
    const firstCatalog = catalogTree[0]
    setExpandedIds(new Set([firstCatalog.id]))
    setSelectedId(firstCatalog.id)
    setInitialExpandDone(true)
  }

  // Lazy-load database objects when a database node is expanded
  const expandedDbIds = useMemo(() => {
    const dbs: string[] = []
    for (const id of expandedIds) {
      if (id.startsWith('db-')) {
        dbs.push(id.replace('db-', ''))
      }
    }
    return dbs
  }, [expandedIds])

  // Track which databases have been loaded
  const [dbCache, setDbCache] = useState<Map<string, DatabaseObjectsResponse>>(() => new Map())

  // Fetch each expanded database (one query at a time via React Query)
  const activeDb = expandedDbIds.find((db) => !dbCache.has(db)) || null

  const { data: dbObjects, isLoading: dbLoading } = useQuery<DatabaseObjectsResponse>({
    queryKey: ['explorer-db', activeDb],
    queryFn: () => api.get(`/explorer/databases/${activeDb}`),
    enabled: !!activeDb,
  })

  // Cache loaded database objects
  useMemo(() => {
    if (activeDb && dbObjects) {
      setDbCache((prev) => {
        if (prev.has(activeDb)) return prev
        const next = new Map(prev)
        next.set(activeDb, dbObjects)
        return next
      })
    }
  }, [activeDb, dbObjects])

  // Inject loaded children into tree
  const tree = useMemo(() => {
    if (!catalogTree.length) return []
    const treeClone = JSON.parse(JSON.stringify(catalogTree)) as ExplorerNode[]

    // For each database node, if we have cached data, replace placeholder children
    for (const cat of treeClone) {
      for (const dbNode of (cat.children ?? [])) {
        if (dbNode.database && dbCache.has(dbNode.database)) {
          const cached = dbCache.get(dbNode.database)!
          dbNode.children = buildDatabaseChildren(cached)
        }
      }
    }
    return treeClone
  }, [catalogTree, dbCache])

  // Fetch table detail when a table is selected
  const selectedNode = useMemo(() => findNodeById(tree, selectedId), [tree, selectedId])
  const tableQueryDb = selectedNode?.database
  const tableQueryName = selectedNode?.type === 'table' ? selectedNode.label : null

  const { data: tableDetail, isLoading: tableLoading, error: tableError } = useQuery<TableDetailResponse>({
    queryKey: ['explorer-table', tableQueryDb, tableQueryName],
    queryFn: () => api.get(`/explorer/databases/${tableQueryDb}/tables/${tableQueryName}`),
    enabled: !!tableQueryDb && !!tableQueryName,
  })

  // Fetch pipe detail when a pipe is selected
  const pipeQueryDb = selectedNode?.database
  const pipeQueryName = selectedNode?.type === 'pipe' ? selectedNode.label : null

  const { data: pipeDetail, isLoading: pipeLoading, error: pipeError } = useQuery<PipeDetailResponse>({
    queryKey: ['explorer-pipe', pipeQueryDb, pipeQueryName],
    queryFn: () => api.get(`/explorer/databases/${pipeQueryDb}/pipes/${pipeQueryName}`),
    enabled: !!pipeQueryDb && !!pipeQueryName,
  })

  // Fetch view detail when a view is selected
  const viewQueryDb = selectedNode?.database
  const viewQueryName = selectedNode?.type === 'view' ? selectedNode.label : null

  const { data: viewDetail, isLoading: viewLoading, error: viewError } = useQuery<ViewDetailResponse>({
    queryKey: ['explorer-view', viewQueryDb, viewQueryName],
    queryFn: () => api.get(`/explorer/databases/${viewQueryDb}/views/${viewQueryName}`),
    enabled: !!viewQueryDb && !!viewQueryName,
  })

  // Fetch MV detail when a materialized view is selected
  const mvQueryDb = selectedNode?.database
  const mvQueryName = selectedNode?.type === 'materialized_view' ? selectedNode.label : null

  const { data: mvDetail, isLoading: mvLoading, error: mvError } = useQuery<MVDetailResponse>({
    queryKey: ['explorer-mv', mvQueryDb, mvQueryName],
    queryFn: () => api.get(`/explorer/databases/${mvQueryDb}/mvs/${mvQueryName}`),
    enabled: !!mvQueryDb && !!mvQueryName,
  })

  // Fetch function detail when a function is selected
  const fnQueryDb = selectedNode?.database
  const fnQueryName = selectedNode?.type === 'function' ? selectedNode.label : null

  const { data: fnDetail, isLoading: fnLoading, error: fnError } = useQuery<FunctionDetailResponse>({
    queryKey: ['explorer-fn', fnQueryDb, fnQueryName],
    queryFn: () => api.get(`/explorer/databases/${fnQueryDb}/functions/${fnQueryName}`),
    enabled: !!fnQueryDb && !!fnQueryName,
  })

  // Handlers
  const toggleExpanded = useCallback((id: string) => {
    setExpandedIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleRefresh = useCallback(() => {
    // Don't clear the dbCache on refresh — just refetch catalogs
    // The cache is preserved so expanded databases keep their children
    setIsRefreshing(true)
    refetchCatalogs()
    // Minimum 1 second spin animation regardless of API response time
    setTimeout(() => setIsRefreshing(false), 1000)
  }, [refetchCatalogs])

  // Search & filter
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const filteredTree = useMemo(
    () => filterTree(tree, normalizedQuery),
    [tree, normalizedQuery],
  )

  const selectedPath = selectedNode?.path ?? []

  return (
    <div data-layout='fixed' className='flex h-full min-h-0 flex-col'>
      <Header fixed>
        <div className='flex min-w-0 flex-1 items-center gap-3'>
          <img
            src='/images/nova-mark.svg'
            alt=''
            aria-hidden='true'
            className='h-6 w-6 shrink-0'
          />
          <div className='min-w-0'>
            <h1 className='truncate text-lg font-semibold'>Database Explorer</h1>
            <p className='text-sm text-muted-foreground'>Nova Catalog</p>
          </div>
        </div>
        <div className='ml-auto flex items-center gap-2'>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant='outline' size='icon' className='h-8 w-8'>
                <MoreHorizontal className='h-3.5 w-3.5' />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align='end'>
              <DropdownMenuItem onClick={handleRefresh}>Refresh</DropdownMenuItem>
              <DropdownMenuItem>Copy path</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </Header>

      <div className='relative flex min-h-0 flex-1 overflow-hidden border-t'>
        {/* Resizable sidebar state */}
        {(() => {
          // Constants
          const MIN_WIDTH = 180
          const MAX_WIDTH = 480
          const DEFAULT_WIDTH = 288 // w-72
          const AUTO_CLOSE_THRESHOLD = 140

          return (
            <ResizableExplorerSidebar
              minWidth={MIN_WIDTH}
              maxWidth={MAX_WIDTH}
              defaultWidth={DEFAULT_WIDTH}
              autoCloseThreshold={AUTO_CLOSE_THRESHOLD}
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              catalogsLoading={catalogsLoading}
              isRefreshing={isRefreshing}
              catalogsError={catalogsError}
              filteredTree={filteredTree}
              expandedIds={expandedIds}
              selectedId={selectedId}
              toggleExpanded={toggleExpanded}
              setSelectedId={setSelectedId}
              dbLoading={dbLoading}
              activeDb={activeDb}
              handleRefresh={handleRefresh}
              onCreateStage={handleOpenCreateStage}
            />
          )
        })()}

        {/* Content */}
        <section className='flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-3 xl:px-5 xl:py-4'>
          {selectedPath.length > 0 && (
            <div className='mb-3 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground'>
              {selectedPath.map((segment, index) => (
                <Fragment key={`${segment}-${index}`}>
                  {index > 0 ? <ChevronRight className='h-3 w-3' /> : null}
                  <span
                    className={cn(
                      'truncate',
                      index === selectedPath.length - 1 && 'font-medium text-foreground',
                    )}
                  >
                    {segment}
                  </span>
                </Fragment>
              ))}
            </div>
          )}
          <ExplorerDetail
            node={selectedNode}
            tableDetail={tableDetail}
            tableLoading={tableLoading}
            tableError={tableError ? String(tableError) : null}
            pipeDetail={pipeDetail}
            pipeLoading={pipeLoading}
            pipeError={pipeError ? String(pipeError) : null}
            viewDetail={viewDetail}
            viewLoading={viewLoading}
            viewError={viewError ? String(viewError) : null}
            mvDetail={mvDetail}
            mvLoading={mvLoading}
            mvError={mvError ? String(mvError) : null}
            fnDetail={fnDetail}
            fnLoading={fnLoading}
            fnError={fnError ? String(fnError) : null}
            catalogsData={catalogsData}
            dbCache={dbCache}
          />
        </section>
      </div>

      {/* Create Stage Dialog */}
      <Dialog open={createStageOpen} onOpenChange={setCreateStageOpen}>
        <DialogContent className='sm:max-w-md'>
          <DialogHeader>
            <DialogTitle>Create Stage</DialogTitle>
            <DialogDescription>
              Create a new stage in <span className='font-semibold'>{createStageDb}</span>.
              The stage name will be used as the folder name in storage.
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4 py-2'>
            <div className='space-y-2'>
              <Label htmlFor='stage-name'>Stage Name</Label>
              <Input
                id='stage-name'
                placeholder='e.g. raw_data, staging_import'
                value={createStageName}
                onChange={(e) => setCreateStageName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && createStageName.trim()) handleCreateStage() }}
                autoFocus
              />
              <p className='text-xs text-muted-foreground'>
                Only lowercase letters, numbers, and underscores.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setCreateStageOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleCreateStage}
              disabled={!createStageName.trim() || createStageLoading}
            >
              {createStageLoading ? (
                <>
                  <Loader2 className='mr-2 h-4 w-4 animate-spin' />
                  Creating...
                </>
              ) : (
                'Create Stage'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
