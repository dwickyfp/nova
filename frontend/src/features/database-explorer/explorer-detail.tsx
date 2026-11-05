import { useState } from 'react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import {
  AlertCircle,
  ArrowRightLeft,
  Blocks,
  Check,
  Copy,
  Database,
  Eye,
  FolderTree,
  Info,
  KeyRound,
  Layers3,
  Loader2,
  Plus,
  RefreshCw,
  Sigma,
  Table2,
  Trash2,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatDateTime } from '@/lib/datetime'
import { PreviewDataTab } from './preview-data-tab'
import {
  catalogObjectGroups,
  formatBytes,
  formatModel,
  getNodeIcon,
  getNodeTypeLabel,
  objectAncestorIds,
  objectNodeId,
  OBJECT_GROUP_DEFS,
  qualifiedNameOf,
  stripBackticks,
} from './helpers'
import { CatalogMetadataSection } from './catalog-metadata-section'
import { StageFilesPanel } from './stage-files-panel'
import { SqlCodeBlock } from './sql-code-block'
import type {
  CatalogsResponse,
  DatabaseObjectsResponse,
  ExplorerNode,
  FunctionDetailResponse,
  MVDetailResponse,
  PipeDetailResponse,
  TableDetailResponse,
  ViewDetailResponse,
} from './types'

// ── Detail Panel ──────────────────────────────────────────────

export function ExplorerDetail({
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
  managedCatalogNames,
  onCreateCatalog,
  onDropCatalog,
  onRefreshMetadata,
  refreshingCatalog,
  onOpenNode,
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
  managedCatalogNames?: Set<string>
  onCreateCatalog?: () => void
  onDropCatalog?: (name: string) => void
  /** Present only for a security-admin role: re-sync one catalog's metadata. */
  onRefreshMetadata?: (name: string) => void
  refreshingCatalog?: string | null
  /** Select a tree node and expand its ancestors. */
  onOpenNode?: (id: string, expand: string[]) => void
}) {
  const [tableTab, setTableTab] = useState<'information' | 'preview'>('information')

  if (!node) {
    return (
      <div className='flex h-full min-h-[420px] items-center justify-center rounded-xl border border-dashed border-surface-border bg-surface-1'>
        <div className='space-y-2 text-center'>
          <div className='mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-muted'>
            <Blocks className='h-5 w-5 text-muted-foreground' />
          </div>
          <p className='text-sm font-medium'>No object selected</p>
          <p className='text-sm text-muted-foreground'>
            Pick a catalog, database, or object in the tree to see its details
          </p>
        </div>
      </div>
    )
  }

  const Icon = getNodeIcon(node.type)
  // A relation of a source catalog (PostgreSQL, Snowflake, another StarRocks):
  // its tables, views, and materialized views share one detail page.
  const isSourceRelation =
    !!node.catalog &&
    node.catalog !== 'default_catalog' &&
    (node.type === 'table' || node.type === 'view' || node.type === 'materialized_view')
  const sourceCatalog = isSourceRelation
    ? catalogsData?.catalogs.find((c) => c.name === node.catalog)
    : undefined

  // Table detail view
  if ((node.type === 'table' || isSourceRelation) && tableDetail) {
    return (
      <div className='space-y-5'>
        <div className='flex flex-wrap items-center gap-3'>
          <div className='flex size-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-2 text-muted-foreground'>
            <Icon className='size-4' />
          </div>
          <div className='space-y-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h1 className='text-xl font-semibold tracking-tight'>{tableDetail.name}</h1>
              <Badge variant='secondary'>
                {isSourceRelation
                  ? tableDetail.kind || getNodeTypeLabel(node.type)
                  : 'Table'}
              </Badge>
              {isSourceRelation && sourceCatalog?.source_label ? (
                <Badge variant='outline'>{sourceCatalog.source_label}</Badge>
              ) : null}
              {!isSourceRelation && tableDetail.properties.table_model && (
                <Badge variant='outline'>{formatModel(tableDetail.properties.table_model)}</Badge>
              )}
            </div>
            <QualifiedName node={node} />
          </div>
        </div>

        <Tabs
          value={tableTab}
          onValueChange={(value) => setTableTab(value as 'information' | 'preview')}
        >
          <TabsList>
            <TabsTrigger value='information' className='gap-1.5'>
              <Info className='size-3.5' />
              Information
            </TabsTrigger>
            <TabsTrigger value='preview' className='gap-1.5'>
              <Table2 className='size-3.5' />
              Preview Data
            </TabsTrigger>
          </TabsList>

          <TabsContent value='information' className='mt-4 space-y-5'>
            <CatalogMetadataSection
              catalog={node.catalog ?? 'default_catalog'}
              database={tableDetail.database}
              object={tableDetail.name}
            />

            {/* Properties */}
            {isSourceRelation ? (
              <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
                <h2 className='mb-4 text-base font-semibold'>Properties</h2>
                <dl className='grid gap-x-6 gap-y-4 md:grid-cols-3'>
                  {[
                    ['Source', sourceCatalog?.source_label ?? node.catalog ?? '—'],
                    ['Kind', tableDetail.kind || getNodeTypeLabel(node.type)],
                    ['Catalog', node.catalog ?? '—'],
                    ['Schema', tableDetail.database],
                    ['Columns', String(tableDetail.columns.length)],
                    ['Row Count', tableDetail.row_count?.toLocaleString() ?? '—'],
                  ].map(([label, value]) => (
                    <div key={label} className='space-y-1'>
                      <dt className='text-sm text-muted-foreground'>{label}</dt>
                      <dd className='text-base font-medium'>{value}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ) : (
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>Properties</h2>
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
            )}

            {/* Columns */}
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>
                Columns <span className='font-normal text-muted-foreground tabular-nums'>{tableDetail.columns.length}</span>
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
                      <tr key={col.name} className='border-b border-surface-border'>
                        <td className='py-2 pr-4 font-medium'>{col.name}</td>
                        <td className='py-2 pr-4 text-muted-foreground'>{col.column_type || col.data_type}</td>
                        <td className='py-2 pr-4 text-muted-foreground'>{col.is_nullable}</td>
                        <td className='py-2 pr-4'>
                          {col.column_key === 'PRI' ? (
                            <span title='Primary Key'><KeyRound className='h-4 w-4 text-primary' /></span>
                          ) : col.column_key === 'UNI' ? (
                            <span title='Unique Key'><KeyRound className='h-4 w-4 text-warning-strong' /></span>
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
              <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
                <h2 className='mb-4 text-base font-semibold'>DDL</h2>
                <SqlCodeBlock
                  source={tableDetail.properties.create_ddl}
                  label='Table DDL'
                />
              </section>
            )}
          </TabsContent>

          <TabsContent value='preview' className='mt-4'>
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <PreviewDataTab
                catalog={node.catalog ?? 'default_catalog'}
                database={tableDetail.database}
                table={tableDetail.name}
              />
            </section>
          </TabsContent>
        </Tabs>
      </div>
    )
  }

  // Table loading/error state
  if (node.type === 'table' || isSourceRelation) {
    if (tableLoading) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center'>
          <div className='flex items-center gap-2 text-sm text-muted-foreground'>
            <Loader2 className='h-5 w-5 animate-spin' />
            Loading table details
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
    // Match the node's own catalog first: `default_catalog` always sits at
    // index 0, so an OR clause would make every catalog node resolve to it.
    // `buildCatalogTree` renames that label to "Nova Catalog", hence the fallback.
    const catalog =
      catalogsData?.catalogs.find((c) => c.name === node.label) ??
      catalogsData?.catalogs.find((c) => c.name === 'default_catalog') ??
      null
    const databases = catalog?.databases ?? []
    const catalogName = catalog?.name ?? node.label
    const isManaged = managedCatalogNames?.has(catalogName) ?? false

    return (
      <div className='space-y-5'>
        <div className='flex flex-wrap items-center gap-3'>
          <div className='flex size-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-2 text-muted-foreground'>
            <FolderTree className='size-4' />
          </div>
          <div className='space-y-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h1 className='text-xl font-semibold tracking-tight'>{node.label}</h1>
              <Badge variant='secondary'>Catalog</Badge>
              {catalog && (
                <Badge variant='outline'>{catalog.source_label ?? catalog.type}</Badge>
              )}
            </div>
            {catalog?.comment && (
              <p className='max-w-2xl text-sm text-muted-foreground'>{catalog.comment}</p>
            )}
            <p className='text-xs text-muted-foreground' data-testid='catalog-sync-status'>
              {catalog?.last_sync_at
                ? `Metadata synced ${formatDateTime(catalog.last_sync_at)}`
                : 'Metadata not synced yet'}
              {catalog?.sync_status === 'error' && ' · last sync failed'}
              {catalog?.source === 'engine' && catalog.type.toLowerCase() !== 'internal' &&
                ' · created outside Nova'}
            </p>
          </div>
          <div className='ms-auto flex items-center gap-2'>
            {onRefreshMetadata && (
              <Button
                variant='outline'
                size='sm'
                disabled={refreshingCatalog === catalogName}
                onClick={() => onRefreshMetadata(catalogName)}
              >
                {refreshingCatalog === catalogName ? (
                  <Loader2 className='me-1.5 h-3.5 w-3.5 animate-spin' />
                ) : (
                  <RefreshCw className='me-1.5 h-3.5 w-3.5' />
                )}
                Refresh metadata
              </Button>
            )}
            {onCreateCatalog && (
              <Button variant='outline' size='sm' onClick={onCreateCatalog}>
                <Plus className='me-1.5 h-3.5 w-3.5' />
                Add catalog
              </Button>
            )}
            {isManaged && onDropCatalog && (
              <Button
                variant='outline'
                size='sm'
                className='text-destructive hover:text-destructive'
                onClick={() => onDropCatalog(catalogName)}
              >
                <Trash2 className='me-1.5 h-3.5 w-3.5' />
                Drop catalog
              </Button>
            )}
          </div>
        </div>

        <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
          <h2 className='mb-4 text-base font-semibold'>
            Databases <span className='font-normal text-muted-foreground tabular-nums'>{databases.length}</span>
          </h2>
          <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
            {databases.map((db) => {
              const cached = dbCache?.get(`${catalogName}/${db}`)
              const counts = cached
                ? [
                    [cached.summary.tables, 'table', 'tables'],
                    [cached.summary.views, 'view', 'views'],
                    [cached.summary.materialized_views, 'materialized view', 'materialized views'],
                  ].filter(([count]) => Number(count) > 0)
                : []
              const dbId = `db-${catalogName}-${db}`
              return (
                <button
                  key={db}
                  type='button'
                  onClick={() => onOpenNode?.(dbId, [`catalog-${catalogName}`, dbId])}
                  className='flex items-start gap-3 rounded-lg border border-surface-border bg-surface-1 p-4 text-left transition-colors hover:bg-surface-3 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
                >
                  <Database className='mt-0.5 size-4 shrink-0 text-muted-foreground' />
                  <span className='min-w-0 flex-1'>
                    <span className='block truncate text-sm font-medium'>{db}</span>
                    <span className='mt-1 block text-xs text-muted-foreground tabular-nums'>
                      {!cached
                        ? 'Open to load its objects'
                        : counts.length === 0
                          ? 'No objects'
                          : counts
                              .map(([count, one, many]) => `${count} ${count === 1 ? one : many}`)
                              .join(' · ')}
                    </span>
                  </span>
                </button>
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
    // The cache is keyed by `catalog/database` so an external database and a
    // self-managed one sharing a name do not collide (see index.tsx).
    const cached = node.catalog
      ? dbCache?.get(`${node.catalog}/${dbName}`)
      : dbCache?.get(dbName)

    return (
      <div className='space-y-5'>
        <div className='flex flex-wrap items-center gap-3'>
          <div className='flex size-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-2 text-muted-foreground'>
            <Database className='size-4' />
          </div>
          <div className='space-y-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h1 className='text-xl font-semibold tracking-tight'>{dbName}</h1>
              <Badge variant='secondary'>Database</Badge>
            </div>
            <QualifiedName node={node} />
          </div>
        </div>

        {cached ? (
          <>
            {(() => {
              const catalogName = node.catalog ?? 'default_catalog'
              const groups = catalogObjectGroups(
                catalogsData?.catalogs.find((c) => c.name === catalogName),
              )
              const external = catalogName !== 'default_catalog'
              const open = (group: string, itemId: string) =>
                onOpenNode?.(
                  objectNodeId(catalogName, dbName, group, itemId),
                  objectAncestorIds(catalogName, dbName, group),
                )
              const listed = groups
                .filter((group) => group !== 'tables')
                .map((group) => ({ group, def: OBJECT_GROUP_DEFS[group] }))
                .filter(({ def }) => !!def)
                .map(({ group, def }) => ({ group, def, items: def.items(cached, dbName, external) }))
                .filter(({ items }) => items.length > 0)
              return (
                <>
                  {/* Counts of the groups this catalog shows */}
                  <dl className='grid grid-cols-[repeat(auto-fill,minmax(8rem,1fr))] gap-x-6 gap-y-3 rounded-xl border border-surface-border bg-surface-2 px-5 py-4'>
                    {groups.map((group) => (
                      <div key={group}>
                        <dt className='text-xs text-muted-foreground'>
                          {OBJECT_GROUP_DEFS[group]?.label ?? group}
                        </dt>
                        <dd className='mt-0.5 text-lg font-semibold tabular-nums'>
                          {cached.summary[group as keyof typeof cached.summary] ?? 0}
                        </dd>
                      </div>
                    ))}
                  </dl>

                  {cached.tables.length > 0 && (
                    <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
                      <h2 className='mb-3 text-base font-semibold'>
                        Tables <span className='font-normal text-muted-foreground tabular-nums'>{cached.tables.length}</span>
                      </h2>
                      <div className='overflow-x-auto'>
                        <table className='w-full text-sm'>
                          <thead>
                            <tr className='border-b border-surface-border text-left text-xs text-muted-foreground'>
                              <th className='pb-2 pr-4 font-medium'>Name</th>
                              {external ? (
                                <th className='pb-2 pr-4 font-medium'>Kind</th>
                              ) : (
                                <>
                                  <th className='pb-2 pr-4 font-medium'>Model</th>
                                  <th className='pb-2 pr-4 text-right font-medium'>Size</th>
                                  <th className='pb-2 pr-4 font-medium'>Created</th>
                                </>
                              )}
                              <th className='pb-2 text-right font-medium'>Rows</th>
                            </tr>
                          </thead>
                          <tbody>
                            {cached.tables.map((t) => (
                              <tr key={t.name} className='border-b border-surface-border last:border-0'>
                                <td className='py-2 pr-4'>
                                  <button
                                    type='button'
                                    onClick={() => open('tables', t.name)}
                                    className='rounded-sm font-medium hover:text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
                                  >
                                    {t.name}
                                  </button>
                                </td>
                                {external ? (
                                  <td className='py-2 pr-4 text-muted-foreground'>{t.engine ?? 'Table'}</td>
                                ) : (
                                  <>
                                    <td className='py-2 pr-4 text-muted-foreground'>{formatModel(t.table_model)}</td>
                                    <td className='py-2 pr-4 text-right text-muted-foreground tabular-nums'>
                                      {t.data_size ? formatBytes(t.data_size) : '—'}
                                    </td>
                                    <td className='py-2 pr-4 text-muted-foreground tabular-nums'>
                                      {t.create_time ? t.create_time.split('T')[0] : '—'}
                                    </td>
                                  </>
                                )}
                                <td className='py-2 text-right text-muted-foreground tabular-nums'>
                                  {t.row_count?.toLocaleString() ?? '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </section>
                  )}

                  {listed.map(({ group, def, items }) => {
                    const ItemIcon = getNodeIcon(def.type)
                    return (
                      <section
                        key={group}
                        className='rounded-xl border border-surface-border bg-surface-2 p-5'
                      >
                        <h2 className='mb-3 text-base font-semibold'>
                          {def.label}{' '}
                          <span className='font-normal text-muted-foreground tabular-nums'>{items.length}</span>
                        </h2>
                        <div className='grid gap-2 sm:grid-cols-2 xl:grid-cols-3'>
                          {items.map((item) => (
                            <button
                              key={item.id}
                              type='button'
                              onClick={() => open(group, item.id)}
                              className='flex min-w-0 items-center gap-2.5 rounded-lg border border-surface-border bg-surface-1 px-3 py-2.5 text-left transition-colors hover:bg-surface-3 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
                            >
                              <ItemIcon className='size-4 shrink-0 text-muted-foreground' />
                              <span className='min-w-0'>
                                <span className='block truncate text-sm font-medium'>
                                  {def.type === 'stage' ? `@${item.name}` : item.name}
                                </span>
                                {item.metadata[0] ? (
                                  <span className='block truncate text-xs text-muted-foreground'>
                                    {item.metadata[0].value}
                                  </span>
                                ) : null}
                              </span>
                            </button>
                          ))}
                        </div>
                      </section>
                    )
                  })}
                </>
              )
            })()}

            {/* Empty database */}
            {cached.summary.tables === 0 && cached.summary.views === 0 && cached.summary.materialized_views === 0 && (
              <section className='rounded-xl border border-dashed border-surface-border bg-surface-1 p-8 text-center'>
                <p className='text-sm text-muted-foreground'>No tables, views, or materialized views yet</p>
              </section>
            )}
          </>
        ) : (
          <section className='flex h-full min-h-[240px] items-center justify-center rounded-xl border border-dashed border-surface-border bg-surface-1'>
            <div className='flex items-center gap-2 text-sm text-muted-foreground'>
              <Loader2 className='h-4 w-4 animate-spin' />
              Loading database objects
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
            Loading pipe details
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
          ? 'bg-success/10 text-success-strong border-success/25'
          : pipeDetail.state === 'SUSPENDED'
            ? 'bg-warning/10 text-warning-strong border-warning/25'
            : pipeDetail.state === 'ERROR'
              ? 'bg-destructive/10 text-destructive border-destructive/25'
              : ''

      return (
        <div className='space-y-5'>
          {/* Header */}
          <div className='flex flex-wrap items-center gap-3'>
            <div className='flex size-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-2 text-muted-foreground'>
              <ArrowRightLeft className='size-4' />
            </div>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h1 className='text-xl font-semibold tracking-tight'>{pipeDetail.name}</h1>
                <Badge variant='secondary'>Pipe</Badge>
                {pipeDetail.state && (
                  <Badge variant='outline' className={stateColor}>
                    {pipeDetail.state}
                  </Badge>
                )}
              </div>
              <QualifiedName node={node} />
            </div>
          </div>

          {/* Properties */}
          <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
            <h2 className='mb-4 text-base font-semibold'>Properties</h2>
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
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>Pipe Configuration</h2>
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
            <section className='rounded-xl border border-destructive/30 bg-destructive/5 p-6'>
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
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>DDL</h2>
              <SqlCodeBlock source={pipeDetail.create_ddl} label='Pipe DDL' />
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
            Loading view details
          </div>
        </div>
      )
    }
    if (viewError) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center rounded-xl border border-destructive/30 bg-destructive/5 p-6'>
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
            <div className='flex size-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-2 text-muted-foreground'>
              <Eye className='size-4' />
            </div>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h1 className='text-xl font-semibold tracking-tight'>{viewDetail.name}</h1>
                <Badge variant='secondary'>View</Badge>
                {viewDetail.is_updatable === 'YES' && (
                  <Badge variant='outline'>Updatable</Badge>
                )}
              </div>
              <QualifiedName node={node} />
            </div>
          </div>

          {node.database && (
            <CatalogMetadataSection
              catalog={node.catalog ?? 'default_catalog'}
              database={node.database}
              object={viewDetail.name}
            />
          )}

          {/* Properties */}
          <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
            <h2 className='mb-4 text-base font-semibold'>Properties</h2>
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
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>DDL</h2>
              <SqlCodeBlock source={viewDetail.create_ddl} label='View DDL' />
            </section>
          )}

          {/* Definition (fallback if no DDL) */}
          {!viewDetail.create_ddl && viewDetail.definition && (
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>Definition</h2>
              <SqlCodeBlock source={viewDetail.definition} label='View definition' />
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
            Loading materialized view details
          </div>
        </div>
      )
    }
    if (mvError) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center rounded-xl border border-destructive/30 bg-destructive/5 p-6'>
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
          ? 'bg-success/10 text-success-strong border-success/25'
          : 'bg-warning/10 text-warning-strong border-warning/25'

      return (
        <div className='space-y-5'>
          {/* Header */}
          <div className='flex flex-wrap items-center gap-3'>
            <div className='flex size-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-2 text-muted-foreground'>
              <Layers3 className='size-4' />
            </div>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h1 className='text-xl font-semibold tracking-tight'>{mvDetail.name}</h1>
                <Badge variant='secondary'>Materialized View</Badge>
                {mvDetail.is_active && (
                  <Badge variant='outline' className={activeColor}>
                    {mvDetail.is_active === 'true' ? 'Active' : 'Inactive'}
                  </Badge>
                )}
              </div>
              <QualifiedName node={node} />
            </div>
          </div>

          {node.database && (
            <CatalogMetadataSection
              catalog={node.catalog ?? 'default_catalog'}
              database={node.database}
              object={mvDetail.name}
            />
          )}

          {/* Properties */}
          <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
            <h2 className='mb-4 text-base font-semibold'>Properties</h2>
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
            <section className='rounded-xl border border-destructive/30 bg-destructive/5 p-6'>
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
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>Definition</h2>
              <SqlCodeBlock source={mvDetail.definition} label='Materialized view definition' />
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
            Loading function details
          </div>
        </div>
      )
    }
    if (fnError) {
      return (
        <div className='flex h-full min-h-[420px] items-center justify-center rounded-xl border border-destructive/30 bg-destructive/5 p-6'>
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
            <div className='flex size-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-2 text-muted-foreground'>
              <Sigma className='size-4' />
            </div>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h1 className='text-xl font-semibold tracking-tight'>{fnDetail.name}</h1>
                <Badge variant='secondary'>{fnDetail.function_type || 'Function'}</Badge>
                {fnDetail.return_type && (
                  <Badge variant='outline'>→ {fnDetail.return_type}</Badge>
                )}
              </div>
              <QualifiedName node={node} />
            </div>
          </div>

          {/* Properties */}
          <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
            <h2 className='mb-4 text-base font-semibold'>Properties</h2>
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
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>DDL</h2>
              <SqlCodeBlock source={fnDetail.create_ddl} label='Function DDL' />
            </section>
          )}

          {/* Definition fallback */}
          {!fnDetail.create_ddl && fnDetail.definition && (
            <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
              <h2 className='mb-4 text-base font-semibold'>Definition</h2>
              <SqlCodeBlock
                source={fnDetail.definition}
                label='Function definition'
              />
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
        <div className='flex size-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-2 text-muted-foreground'>
          <Icon className='size-4' />
        </div>
        <div className='space-y-1'>
          <div className='flex flex-wrap items-center gap-2'>
            <h1 className='text-xl font-semibold tracking-tight'>{node.label}</h1>
            <Badge variant='secondary'>{getNodeTypeLabel(node.type)}</Badge>
          </div>
          <QualifiedName node={node} />
        </div>
      </div>

      <div className='border-b border-border'>
        <div className='inline-flex border-b-2 border-primary px-1 py-3 text-sm font-medium text-primary dark:text-foreground'>
          Object Details
        </div>
      </div>

      <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
        <h2 className='mb-4 text-base font-semibold'>Overview</h2>
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
        <section className='rounded-xl border border-surface-border bg-surface-2 p-5'>
          <h2 className='mb-4 text-base font-semibold'>
            Objects <span className='font-normal text-muted-foreground tabular-nums'>{node.children.length}</span>
          </h2>
          <div className='grid gap-2'>
            {node.children.map((child) => {
              const ChildIcon = getNodeIcon(child.type)
              return (
                <div key={child.id} className='flex items-center gap-3 rounded-lg border border-surface-border bg-surface-1 px-4 py-2.5'>
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

/** The name a query uses for this object, set in mono, with a copy button. */
function QualifiedName({ node }: { node: ExplorerNode }) {
  const [copied, setCopied] = useState(false)
  const name = qualifiedNameOf(node)
  if (!name) return <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(name)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('Could not copy to the clipboard')
    }
  }
  return (
    <div className='flex min-w-0 items-center gap-1'>
      <code className='truncate font-mono text-xs text-muted-foreground'>{name}</code>
      <button
        type='button'
        onClick={copy}
        aria-label={copied ? `Copied ${name}` : `Copy ${name}`}
        className={cn(
          'inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
          copied && 'text-success-strong',
        )}
      >
        {copied ? <Check className='size-3.5' /> : <Copy className='size-3.5' />}
      </button>
    </div>
  )
}
