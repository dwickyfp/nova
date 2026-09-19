import {
  AlertCircle,
  ArrowRightLeft,
  Blocks,
  Box,
  Database,
  Eye,
  FolderTree,
  KeyRound,
  Layers3,
  Loader2,
  Plus,
  Sigma,
  Table2,
  Trash2,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  formatBytes,
  formatModel,
  getNodeIcon,
  getNodeTypeLabel,
  stripBackticks,
} from './helpers'
import { StageFilesPanel } from './stage-files-panel'
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
}) {
  if (!node) {
    return (
      <div className='flex h-full min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-1'>
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
        <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
        <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
          <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
          <div className='ms-auto flex items-center gap-2'>
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

        <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
                <div key={key} className='rounded-xl border border-border bg-surface-2 p-4'>
                  <p className='text-xs text-muted-foreground'>{key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</p>
                  <p className='mt-1 text-2xl font-semibold'>{count}</p>
                </div>
              ))}
            </section>

            {/* Tables list */}
            {cached.tables.length > 0 && (
              <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
                        <tr key={t.name} className='border-b border-surface-border'>
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
              <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
              <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
              <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
              <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
              <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
              <section className='rounded-2xl border border-dashed border-border bg-surface-1 p-8 text-center'>
                <p className='text-sm text-muted-foreground'>This database has no objects yet.</p>
              </section>
            )}
          </>
        ) : (
          <section className='flex h-full min-h-[240px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-1'>
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
          <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
            <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
            <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
          <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
            <section className='rounded-2xl border border-border bg-surface-2 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>DDL</h2>
              <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
                <code>{viewDetail.create_ddl}</code>
              </pre>
            </section>
          )}

          {/* Definition (fallback if no DDL) */}
          {!viewDetail.create_ddl && viewDetail.definition && (
            <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
          ? 'bg-success/10 text-success-strong border-success/25'
          : 'bg-warning/10 text-warning-strong border-warning/25'

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
          <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
            <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
          <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
            <section className='rounded-2xl border border-border bg-surface-2 p-6'>
              <h2 className='mb-4 text-xl font-semibold'>DDL</h2>
              <pre className='overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs leading-relaxed'>
                <code>{fnDetail.create_ddl}</code>
              </pre>
            </section>
          )}

          {/* Definition fallback */}
          {!fnDetail.create_ddl && fnDetail.definition && (
            <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
        <div className='inline-flex border-b-2 border-primary px-1 py-3 text-sm font-medium text-primary dark:text-foreground'>
          Object Details
        </div>
      </div>

      <section className='rounded-2xl border border-border bg-surface-2 p-6'>
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
        <section className='rounded-2xl border border-border bg-surface-2 p-6'>
          <h2 className='mb-4 text-xl font-semibold'>
            Objects <span className='text-muted-foreground'>({node.children.length})</span>
          </h2>
          <div className='grid gap-2'>
            {node.children.map((child) => {
              const ChildIcon = getNodeIcon(child.type)
              return (
                <div key={child.id} className='flex items-center gap-3 rounded-lg border border-surface-border bg-background/60 px-4 py-2.5'>
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
