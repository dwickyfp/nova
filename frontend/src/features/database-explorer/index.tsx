import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ChevronRight,
  Loader2,
  MoreHorizontal,
  PanelLeftOpen,
  RefreshCw,
  Search,
} from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { SidebarMenu } from '@/components/ui/sidebar'
import { api } from '@/lib/api-client'
import {
  buildCatalogTree,
  buildDatabaseChildren,
  filterTree,
  findNodeById,
} from './helpers'
import { ExplorerDetail } from './explorer-detail'
import { TreeNodeRow } from './tree-node-row'
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

// ── Tree Node Component ───────────────────────────────────────



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
