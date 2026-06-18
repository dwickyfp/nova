import {
  type ComponentProps,
  type CSSProperties,
  type Dispatch,
  type MouseEvent as ReactMouseEvent,
  type SetStateAction,
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import Editor, { type Monaco } from '@monaco-editor/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown,
  ChevronRight,
  Database,
  Folder,
  FolderOpen,
  GripHorizontal,
  Play,
  Plus,
  Save,
  Search,
  Table2,
  Trash2,
  UserRoundCog,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Header } from '@/components/layout/header'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

type WorkspaceEntry = {
  id: string
  name: string
  parent_path: string
  path: string
  entry_type: 'file' | 'folder'
  size_bytes: number
}

type WorkspaceTreeResponse = {
  root_name: string
  entries: WorkspaceEntry[]
  open_tabs: string[]
  active_tab: string | null
  sidebar_collapsed: boolean
  defaults: {
    database?: string | null
    schema?: string | null
    role?: string | null
  }
}

type WorkspaceFileResponse = {
  entry: WorkspaceEntry
  content: string
}

type QueryContextResponse = {
  roles: string[]
  databases: string[]
  schemas: string[]
  defaults: {
    database?: string | null
    schema?: string | null
    role?: string | null
  }
}

type QueryResponse = {
  success: boolean
  columns: string[]
  rows: Array<Array<string | number | boolean | null>>
  row_count: number
  affected_rows: number
  elapsed_ms: number
  original_sql: string
  executed_sql: string
  warnings: string[]
  destructive?: boolean
  needs_confirmation?: boolean
}

type CompletionResponse = {
  items: Array<{ label: string; type: string }>
}

type SchemaResponse = {
  schemas: Array<{ name: string }>
}

type SchemaTreeResponse = {
  database: string
  schema: string
  tables: Array<{ name: string; type: string }>
  views: Array<{ name: string; type: string }>
  materialized_views: Array<{ name: string; type: string }>
  stages: Array<{ id: string; name: string; type: string }>
}

type WorkspaceTabState = {
  id: string
  title: string
  content: string
  savedContent: string
  database: string
  schema: string
  role: string
  loaded: boolean
}

const SQL_KEYWORDS = [
  'SELECT',
  'FROM',
  'WHERE',
  'JOIN',
  'LEFT JOIN',
  'RIGHT JOIN',
  'GROUP BY',
  'ORDER BY',
  'LIMIT',
  'INSERT INTO',
  'UPDATE',
  'DELETE FROM',
  'CREATE TABLE',
  'ALTER TABLE',
  'DROP TABLE',
  'SHOW DATABASES',
  'DESC',
  'EXPLAIN',
]

export function WorkspacesPage() {
  const queryClient = useQueryClient()
  const [sidebarTab, setSidebarTab] = useState<'workspaces' | 'databases'>(
    'workspaces'
  )
  const [secondaryCollapsed, setSecondaryCollapsed] = useState(false)
  const [workspaceSearch, setWorkspaceSearch] = useState('')
  const [databaseSearch, setDatabaseSearch] = useState('')
  const [tabs, setTabs] = useState<Record<string, WorkspaceTabState>>({})
  const [openTabIds, setOpenTabIds] = useState<string[]>([])
  const [activeTabId, setActiveTabId] = useState<string | null>(null)
  const [expandedWorkspacePaths, setExpandedWorkspacePaths] = useState<
    Record<string, boolean>
  >({ '': true })
  const [expandedDatabases, setExpandedDatabases] = useState<
    Record<string, boolean>
  >({})
  const [expandedSchemas, setExpandedSchemas] = useState<Record<string, boolean>>(
    {}
  )
  const [schemasByDatabase, setSchemasByDatabase] = useState<
    Record<string, Array<{ name: string }>>
  >({})
  const [resultsHeight, setResultsHeight] = useState(260)
  const [resultsCollapsed, setResultsCollapsed] = useState(false)
  const [queryResult, setQueryResult] = useState<QueryResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [createFileOpen, setCreateFileOpen] = useState(false)
  const [newFileName, setNewFileName] = useState('Untitled.sql')
  const [createFileError, setCreateFileError] = useState<string | null>(null)
  const [creatingFile, setCreatingFile] = useState(false)
  const activeTab = activeTabId ? tabs[activeTabId] : null
  const deferredWorkspaceSearch = useDeferredValue(workspaceSearch)
  const deferredDatabaseSearch = useDeferredValue(databaseSearch)
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null)
  const saveTimerRef = useRef<number | null>(null)
  const stateSaveTimerRef = useRef<number | null>(null)
  const editorContentRef = useRef('')

  const workspaceTreeQuery = useQuery<WorkspaceTreeResponse>({
    queryKey: ['workspace-tree'],
    queryFn: () => api.get<WorkspaceTreeResponse>('/workspaces/tree'),
  })

  const queryContextQuery = useQuery<QueryContextResponse>({
    queryKey: ['query-context'],
    queryFn: () => api.get<QueryContextResponse>('/query/context'),
  })

  const databasesQuery = useQuery<{ databases: Array<{ name: string }> }>({
    queryKey: ['object-databases'],
    queryFn: () => api.get<{ databases: Array<{ name: string }> }>('/objects/databases'),
  })

  useEffect(() => {
    const tree = workspaceTreeQuery.data
    const context = queryContextQuery.data
    if (!tree || !context) return
    setSecondaryCollapsed(tree.sidebar_collapsed)
    setOpenTabIds((prev) => (prev.length ? prev : tree.open_tabs))
    setActiveTabId((prev) => prev ?? tree.active_tab ?? tree.open_tabs[0] ?? null)

    if (!tree.open_tabs.length) return
    setTabs((prev) => {
      if (Object.keys(prev).length) return prev
      const next = { ...prev }
      for (const id of tree.open_tabs) {
        const entry = tree.entries.find((item) => item.id === id)
        if (!entry) continue
        next[id] = {
          id,
          title: entry.name,
          content: '',
          savedContent: '',
          database:
            tree.defaults.database ??
            context.defaults.database ??
            context.databases[0] ??
            '',
          schema:
            tree.defaults.schema ??
            context.defaults.schema ??
            context.schemas[0] ??
            'default',
          role:
            tree.defaults.role ?? context.defaults.role ?? context.roles[0] ?? '',
          loaded: false,
        }
      }
      return next
    })
  }, [queryContextQuery.data, workspaceTreeQuery.data])

  useEffect(() => {
    if (!activeTabId) return
    const tab = tabs[activeTabId]
    if (!tab || tab.loaded) return
    void openTab(activeTabId)
  }, [activeTabId, tabs])

  // Pre-load schemas when active tab's database changes
  useEffect(() => {
    if (!activeTab?.database || schemasByDatabase[activeTab.database]) return
    void api
      .get<SchemaResponse>(
        `/objects/databases/${encodeURIComponent(activeTab.database)}/schemas${activeTab.role ? `?role=${encodeURIComponent(activeTab.role)}` : ''}`
      )
      .then((response) => {
        setSchemasByDatabase((prev) => ({
          ...prev,
          [activeTab.database]: response.schemas,
        }))
      })
      .catch(() => {})
  }, [activeTab?.database, activeTab?.role])

  useEffect(() => {
    if (!activeTab) return
    if (!activeTab.loaded || activeTab.content === activeTab.savedContent) return
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = window.setTimeout(() => {
      void saveFile(activeTab.id, activeTab.content, activeTab.database, activeTab.schema, activeTab.role)
    }, 700)
    return () => {
      if (saveTimerRef.current) {
        window.clearTimeout(saveTimerRef.current)
      }
    }
  }, [activeTab])

  useEffect(() => {
    const onMouseMove = (event: MouseEvent) => {
      if (!dragRef.current) return
      const delta = dragRef.current.startY - event.clientY
      setResultsHeight(Math.max(180, dragRef.current.startHeight + delta))
    }
    const onMouseUp = () => {
      dragRef.current = null
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
  }, [])

  useEffect(() => {
    const handleBeforeUnload = () => {
      if (!activeTab) return
      if (activeTab.loaded && activeTab.content !== activeTab.savedContent) {
        void saveFile(
          activeTab.id,
          activeTab.content,
          activeTab.database,
          activeTab.schema,
          activeTab.role
        )
      }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [activeTab])

  const filteredEntries = useMemo(() => {
    const entries = workspaceTreeQuery.data?.entries ?? []
    if (!deferredWorkspaceSearch) return entries
    const query = deferredWorkspaceSearch.toLowerCase()
    const matchedPaths = new Set<string>()
    for (const entry of entries) {
      if (!entry.path.toLowerCase().includes(query)) continue
      matchedPaths.add(entry.path)
      let currentParent = entry.parent_path
      while (currentParent) {
        matchedPaths.add(currentParent)
        const parentEntry = entries.find((item) => item.path === currentParent)
        currentParent = parentEntry?.parent_path ?? ''
      }
    }
    return entries.filter((entry) => matchedPaths.has(entry.path))
  }, [deferredWorkspaceSearch, workspaceTreeQuery.data?.entries])

  const filteredDatabases = useMemo(() => {
    const databases = databasesQuery.data?.databases ?? []
    if (!deferredDatabaseSearch) return databases
    const query = deferredDatabaseSearch.toLowerCase()
    return databases.filter((db) => db.name.toLowerCase().includes(query))
  }, [databasesQuery.data?.databases, deferredDatabaseSearch])

  const saveStateMutation = useMutation({
    mutationFn: (state: {
      open_tabs: string[]
      active_tab: string | null
      sidebar_collapsed: boolean
      last_database: string | null
      last_schema: string | null
      last_role: string | null
    }) => api.put<{ success: boolean }>('/workspaces/state', state),
  })

  useEffect(() => {
    if (!workspaceTreeQuery.data || !queryContextQuery.data) return
    if (stateSaveTimerRef.current) {
      window.clearTimeout(stateSaveTimerRef.current)
    }
    stateSaveTimerRef.current = window.setTimeout(() => {
      void saveStateMutation.mutateAsync({
        open_tabs: openTabIds,
        active_tab: activeTabId,
        sidebar_collapsed: secondaryCollapsed,
        last_database: activeTab?.database ?? null,
        last_schema: activeTab?.schema ?? null,
        last_role: activeTab?.role ?? null,
      })
    }, 300)
    return () => {
      if (stateSaveTimerRef.current) {
        window.clearTimeout(stateSaveTimerRef.current)
      }
    }
  }, [activeTab?.database, activeTab?.role, activeTab?.schema, activeTabId, openTabIds, queryContextQuery.data, secondaryCollapsed, workspaceTreeQuery.data])

  async function openTab(id: string) {
    const file = await api.get<WorkspaceFileResponse>(`/workspaces/files/${id}`)
    const context = queryContextQuery.data
    const defaults = workspaceTreeQuery.data?.defaults
    editorContentRef.current = file.content
    setTabs((prev) => ({
      ...prev,
      [id]: {
        id,
        title: file.entry.name,
        content: file.content,
        savedContent: file.content,
        database:
          prev[id]?.database ??
          defaults?.database ??
          context?.defaults.database ??
          context?.databases[0] ??
          '',
        schema:
          prev[id]?.schema ??
          defaults?.schema ??
          context?.defaults.schema ??
          'default',
        role:
          prev[id]?.role ??
          defaults?.role ??
          context?.defaults.role ??
          context?.roles[0] ??
          '',
        loaded: true,
      },
    }))
  }

  async function saveFile(
    id: string,
    content: string,
    database: string,
    schema: string,
    role: string
  ) {
    const response = await api.put<WorkspaceFileResponse>(`/workspaces/files/${id}`, {
      content,
      database,
      schema,
      role,
    })
    setTabs((prev) => ({
      ...prev,
      [id]: {
        ...prev[id],
        title: response.entry.name,
        savedContent: content,
      },
    }))
    await queryClient.invalidateQueries({ queryKey: ['workspace-tree'] })
  }

  async function createFile() {
    const trimmedName = newFileName.trim()
    if (!trimmedName) {
      setCreateFileError('File name is required.')
      return
    }
    setCreatingFile(true)
    setCreateFileError(null)
    try {
      const response = await api.post<WorkspaceFileResponse>('/workspaces/files', {
        name: trimmedName,
        parent_path: '',
        content: '',
      })
      await queryClient.invalidateQueries({ queryKey: ['workspace-tree'] })
      startTransition(() => {
        setOpenTabIds((prev) => [...new Set([...prev, response.entry.id])])
        setActiveTabId(response.entry.id)
        setTabs((prev) => ({
          ...prev,
          [response.entry.id]: {
            id: response.entry.id,
            title: response.entry.name,
            content: '',
            savedContent: '',
            database:
              activeTab?.database ??
              queryContextQuery.data?.defaults.database ??
              queryContextQuery.data?.databases[0] ??
              '',
            schema:
              activeTab?.schema ??
              queryContextQuery.data?.defaults.schema ??
              'default',
            role:
              activeTab?.role ??
              queryContextQuery.data?.defaults.role ??
              queryContextQuery.data?.roles[0] ??
              '',
            loaded: true,
          },
        }))
      })
      setCreateFileOpen(false)
      setNewFileName('Untitled.sql')
    } catch (error) {
      setCreateFileError(
        error instanceof Error ? error.message : 'Failed to create SQL file.'
      )
    } finally {
      setCreatingFile(false)
    }
  }

  async function renameEntry(entry: WorkspaceEntry) {
    const name = window.prompt('Rename entry', entry.name)
    if (!name || name === entry.name) return
    await api.post<{ entry: WorkspaceEntry }>('/workspaces/rename', {
      id: entry.id,
      name,
      parent_path: entry.parent_path,
    })
    await queryClient.invalidateQueries({ queryKey: ['workspace-tree'] })
  }

  async function deleteEntry(entry: WorkspaceEntry) {
    if (!window.confirm(`Delete ${entry.name}?`)) return
    await api.delete<{ success: boolean }>(`/workspaces/files/${entry.id}`)
    setOpenTabIds((prev) => prev.filter((id) => id !== entry.id))
    if (activeTabId === entry.id) {
      setActiveTabId((prev) => openTabIds.find((id) => id !== prev) ?? null)
    }
    await queryClient.invalidateQueries({ queryKey: ['workspace-tree'] })
  }

  async function runQuery(confirmDestructive = false) {
    if (!activeTab) return
    const sql = editorContentRef.current.trim() || activeTab.content.trim()
    if (!sql) return
    if (!confirmDestructive && isDestructiveSql(sql)) {
      const ok = window.confirm(
        'This query looks destructive. Do you want to run it?'
      )
      if (!ok) return
      return runQuery(true)
    }
    setRunning(true)
    setQueryResult(null)
    try {
      const response = await api.post<QueryResponse>('/query/execute', {
        sql,
        database: activeTab.database || null,
        schema: activeTab.schema || null,
        role: activeTab.role || null,
        max_rows: 500,
        file_id: activeTab.id,
        confirm_destructive: confirmDestructive,
      })
      setQueryResult(response)
      if (activeTab.content !== activeTab.savedContent) {
        await saveFile(
          activeTab.id,
          activeTab.content,
          activeTab.database,
          activeTab.schema,
          activeTab.role
        )
      }
    } catch (error) {
      setQueryResult({
        success: false,
        columns: [],
        rows: [],
        row_count: 0,
        affected_rows: 0,
        elapsed_ms: 0,
        original_sql: sql,
        executed_sql: '',
        warnings: [error instanceof Error ? error.message : 'Query failed'],
        destructive: false,
        needs_confirmation: false,
      })
    } finally {
      setRunning(false)
    }
  }

  async function flushTabSave(tabId: string | null) {
    if (!tabId) return
    const tab = tabs[tabId]
    if (!tab || !tab.loaded || tab.content === tab.savedContent) return
    await saveFile(tab.id, tab.content, tab.database, tab.schema, tab.role)
  }

  function activateTab(nextTabId: string) {
    void flushTabSave(activeTabId)
    setActiveTabId(nextTabId)
  }

  function closeTab(id: string) {
    void flushTabSave(id)
    setOpenTabIds((prev) => prev.filter((tabId) => tabId !== id))
    setTabs((prev) => {
      const next = { ...prev }
      delete next[id]
      return next
    })
    if (activeTabId === id) {
      const nextId = openTabIds.find((tabId) => tabId !== id) ?? null
      setActiveTabId(nextId)
    }
  }

  function startResultsResize(event: ReactMouseEvent<HTMLButtonElement>) {
    dragRef.current = {
      startY: event.clientY,
      startHeight: resultsHeight,
    }
  }

  return (
    <div data-layout='fixed' className='flex h-full min-h-0 flex-col'>
      <Header fixed>
        <div className='flex min-w-0 flex-1 items-center gap-3'>
          <div className='min-w-0'>
            <h1 className='truncate text-lg font-semibold'>Workspaces</h1>
            <p className='text-sm text-muted-foreground'>
              SQL workspace with per-tab context, object browsing, and results.
            </p>
          </div>
        </div>
      </Header>

      <div className='flex min-h-0 flex-1 overflow-hidden border-t'>
        <aside
          className={cn(
            'border-r bg-muted/20 transition-all duration-200',
            secondaryCollapsed ? 'w-14' : 'w-80'
          )}
        >
          <div className='flex h-full min-h-0 flex-col'>
            <div className='border-b px-3 py-3'>
              <div className='flex items-center justify-between gap-2'>
                {!secondaryCollapsed && (
                  <Tabs
                    value={sidebarTab}
                    onValueChange={(value) =>
                      setSidebarTab(value as 'workspaces' | 'databases')
                    }
                    className='w-full'
                  >
                    <TabsList className='grid w-full grid-cols-2'>
                      <TabsTrigger value='workspaces'>Workspaces</TabsTrigger>
                      <TabsTrigger value='databases'>Databases</TabsTrigger>
                    </TabsList>
                  </Tabs>
                )}
                <Button
                  variant='outline'
                  size='icon'
                  onClick={() => setSecondaryCollapsed((prev) => !prev)}
                >
                  <ChevronRight
                    className={cn(
                      'size-4 transition-transform',
                      !secondaryCollapsed && 'rotate-180'
                    )}
                  />
                </Button>
              </div>
              {!secondaryCollapsed && sidebarTab === 'workspaces' && (
                <div className='mt-3 flex gap-2'>
                  <Button
                    size='sm'
                    variant='outline'
                    onClick={() => setCreateFileOpen(true)}
                  >
                    <Plus className='size-4' />
                    File
                  </Button>
                </div>
              )}
            </div>

            {!secondaryCollapsed && (
              <>
                <div className='border-b px-3 py-3'>
                  <div className='relative'>
                    <Search className='absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground' />
                    <Input
                      value={
                        sidebarTab === 'workspaces'
                          ? workspaceSearch
                          : databaseSearch
                      }
                      onChange={(event) =>
                        sidebarTab === 'workspaces'
                          ? setWorkspaceSearch(event.target.value)
                          : setDatabaseSearch(event.target.value)
                      }
                      placeholder={
                        sidebarTab === 'workspaces'
                          ? 'Search files'
                          : 'Search databases'
                      }
                      className='pl-9'
                    />
                  </div>
                </div>
                <ScrollArea className='min-h-0 flex-1'>
                  {sidebarTab === 'workspaces' ? (
                    <WorkspaceTree
                      entries={filteredEntries}
                      expandedPaths={expandedWorkspacePaths}
                      onTogglePath={(path) =>
                        setExpandedWorkspacePaths((prev) => ({
                          ...prev,
                          [path]: !prev[path],
                        }))
                      }
                      onOpen={(entry) => {
                        if (entry.entry_type !== 'file') return
                        setOpenTabIds((prev) => [...new Set([...prev, entry.id])])
                        activateTab(entry.id)
                        setTabs((prev) => ({
                          ...prev,
                          [entry.id]:
                            prev[entry.id] ??
                            ({
                              id: entry.id,
                              title: entry.name,
                              content: '',
                              savedContent: '',
                              database:
                                activeTab?.database ??
                                queryContextQuery.data?.defaults.database ??
                                queryContextQuery.data?.databases[0] ??
                                '',
                              schema:
                                activeTab?.schema ??
                                queryContextQuery.data?.defaults.schema ??
                                'default',
                              role:
                                activeTab?.role ??
                                queryContextQuery.data?.defaults.role ??
                                queryContextQuery.data?.roles[0] ??
                                '',
                              loaded: false,
                            } satisfies WorkspaceTabState),
                        }))
                      }}
                      onRename={renameEntry}
                      onDelete={deleteEntry}
                    />
                  ) : (
                    <DatabaseExplorer
                      databases={filteredDatabases}
                      expandedDatabases={expandedDatabases}
                      expandedSchemas={expandedSchemas}
                      onToggleDatabase={(name) =>
                        setExpandedDatabases((prev) => ({
                          ...prev,
                          [name]: !prev[name],
                        }))
                      }
                      onToggleSchema={(key) =>
                        setExpandedSchemas((prev) => ({
                          ...prev,
                          [key]: !prev[key],
                        }))
                      }
                      role={activeTab?.role ?? queryContextQuery.data?.defaults.role ?? undefined}
                      setSchemasByDatabase={setSchemasByDatabase}
                    />
                  )}
                </ScrollArea>
              </>
            )}
          </div>
        </aside>

        <section className='flex min-h-0 min-w-0 flex-1 flex-col bg-background'>
          <div className='border-b px-4 py-3'>
            <div className='flex flex-wrap items-center gap-2'>
              {openTabIds.map((id) => {
                const tab = tabs[id]
                if (!tab) return null
                return (
                  <button
                    key={id}
                    type='button'
                    onClick={() => activateTab(id)}
                    className={cn(
                      'flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm',
                      activeTabId === id
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-border bg-background hover:bg-muted'
                    )}
                  >
                    <span className='truncate'>{tab.title}</span>
                    <X
                      className='size-3.5'
                      onClick={(event) => {
                        event.stopPropagation()
                        closeTab(id)
                      }}
                    />
                  </button>
                )
              })}
              <Button
                size='icon'
                variant='outline'
                onClick={() => setCreateFileOpen(true)}
              >
                <Plus className='size-4' />
              </Button>
            </div>
          </div>

          {activeTab ? (
            <>
              <div className='flex flex-wrap items-center gap-3 border-b px-4 py-3'>
                <Button size='sm' onClick={() => runQuery()} disabled={running}>
                  <Play className='size-4' />
                  {running ? 'Running...' : 'Run'}
                </Button>
                <div className='flex flex-1 flex-wrap items-center justify-end gap-2'>
                  <ContextSelect
                    label='Role'
                    value={activeTab.role}
                    options={queryContextQuery.data?.roles ?? []}
                    icon={<UserRoundCog className='size-4' />}
                    onChange={(value) =>
                      setTabs((prev) => ({
                        ...prev,
                        [activeTab.id]: { ...prev[activeTab.id], role: value },
                      }))
                    }
                  />
                  <ContextSelect
                    label='Database'
                    value={activeTab.database}
                    options={queryContextQuery.data?.databases ?? []}
                    icon={<Database className='size-4' />}
                    onChange={async (value) => {
                      const schemas = await api.get<SchemaResponse>(
                        `/objects/databases/${encodeURIComponent(value)}/schemas${activeTab.role ? `?role=${encodeURIComponent(activeTab.role)}` : ''}`
                      )
                      setSchemasByDatabase((prev) => ({
                        ...prev,
                        [value]: schemas.schemas,
                      }))
                      setTabs((prev) => ({
                        ...prev,
                        [activeTab.id]: {
                          ...prev[activeTab.id],
                          database: value,
                          schema: schemas.schemas[0]?.name ?? 'default',
                        },
                      }))
                    }}
                  />
                  <ContextSelect
                    label='Schema'
                    value={activeTab.schema}
                    options={
                      activeTab.database
                        ? ((schemasByDatabase[activeTab.database] ?? []).map(
                            (item) => item.name
                          ) as string[])
                        : ['default']
                    }
                    icon={<FolderOpen className='size-4' />}
                    onOpen={async () => {
                      if (!activeTab.database || schemasByDatabase[activeTab.database])
                        return
                      const response = await api.get<SchemaResponse>(
                        `/objects/databases/${encodeURIComponent(activeTab.database)}/schemas${activeTab.role ? `?role=${encodeURIComponent(activeTab.role)}` : ''}`
                      )
                      setSchemasByDatabase((prev) => ({
                        ...prev,
                        [activeTab.database]: response.schemas,
                      }))
                    }}
                    onChange={(value) =>
                      setTabs((prev) => ({
                        ...prev,
                        [activeTab.id]: { ...prev[activeTab.id], schema: value },
                      }))
                    }
                  />
                </div>
              </div>

              <div className='grid min-h-0 flex-1 grid-rows-[1fr_auto] overflow-hidden'>
                <div className='min-h-0 overflow-hidden pt-3'>
                  <MonacoSqlEditor
                    key={activeTab.id}
                    value={activeTab.content}
                    database={activeTab.database}
                    schema={activeTab.schema}
                    role={activeTab.role}
                    onChange={(value) => {
                      const content = value ?? ''
                      editorContentRef.current = content
                      setTabs((prev) => ({
                        ...prev,
                        [activeTab.id]: {
                          ...prev[activeTab.id],
                          content,
                        },
                      }))
                    }}
                    onRun={() => runQuery()}
                  />
                </div>

                <div
                  style={
                    {
                      height: `${resultsCollapsed ? 44 : resultsHeight}px`,
                    } satisfies CSSProperties
                  }
                  className='mx-4 mt-2 flex shrink-0 flex-col overflow-hidden rounded-t-xl border bg-background transition-[height] duration-200 ease-in-out'
                >
                  <div className='flex items-center justify-between border-b px-4 py-2'>
                    <div className='flex items-center gap-3'>
                      <button
                        type='button'
                        className='rounded p-1 hover:bg-muted'
                        onClick={() => setResultsCollapsed((prev) => !prev)}
                      >
                        <ChevronDown
                          className={cn(
                            'size-4 transition-transform',
                            resultsCollapsed && '-rotate-90'
                          )}
                        />
                      </button>
                      <div>
                        <div className='font-medium'>Results</div>
                        {queryResult && (
                          <div className='text-xs text-muted-foreground'>
                            {queryResult.row_count} rows • {queryResult.elapsed_ms}
                            ms
                          </div>
                        )}
                      </div>
                    </div>
                    {!resultsCollapsed && (
                      <Button
                        type='button'
                        size='icon'
                        variant='ghost'
                        onMouseDown={startResultsResize}
                      >
                        <GripHorizontal className='size-4' />
                      </Button>
                    )}
                  </div>
                  {!resultsCollapsed && (
                    <QueryResults queryResult={queryResult} />
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className='flex flex-1 items-center justify-center text-muted-foreground'>
              Open or create a SQL file to start querying.
            </div>
          )}
        </section>
      </div>
      <Dialog
        open={createFileOpen}
        onOpenChange={(open) => {
          setCreateFileOpen(open)
          if (open) return
          setCreateFileError(null)
          setNewFileName('Untitled.sql')
        }}
      >
        <DialogContent className='sm:max-w-md'>
          <DialogHeader className='text-start'>
            <DialogTitle>New SQL File</DialogTitle>
            <DialogDescription>
              Create a new SQL file in your workspace.
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-2'>
            <Input
              autoFocus
              value={newFileName}
              onChange={(event) => {
                setNewFileName(event.target.value)
                if (createFileError) setCreateFileError(null)
              }}
              placeholder='Untitled.sql'
            />
            {createFileError ? (
              <p className='text-sm text-destructive'>{createFileError}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => setCreateFileOpen(false)}
              disabled={creatingFile}
            >
              Cancel
            </Button>
            <Button onClick={() => void createFile()} disabled={creatingFile}>
              {creatingFile ? 'Creating...' : 'Create file'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function ContextSelect({
  label,
  value,
  options,
  icon,
  onChange,
  onOpen,
}: {
  label: string
  value: string
  options: string[]
  icon: React.ReactNode
  onChange: (value: string) => void
  onOpen?: () => void | Promise<void>
}) {
  return (
    <Select
      value={value}
      onValueChange={onChange}
      onOpenChange={(open) => {
        if (open) void onOpen?.()
      }}
    >
      <SelectTrigger className='w-52'>
        <div className='flex min-w-0 items-center gap-2'>
          {icon}
          <SelectValue placeholder={label} />
        </div>
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option} value={option}>
            {option}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function WorkspaceTree({
  entries,
  expandedPaths,
  onTogglePath,
  onOpen,
  onRename,
  onDelete,
}: {
  entries: WorkspaceEntry[]
  expandedPaths: Record<string, boolean>
  onTogglePath: (path: string) => void
  onOpen: (entry: WorkspaceEntry) => void
  onRename: (entry: WorkspaceEntry) => void
  onDelete: (entry: WorkspaceEntry) => void
}) {
  return (
    <div className='p-3'>
      <div className='space-y-1'>
        {renderWorkspaceEntries(
          '',
          entries,
          expandedPaths,
          onTogglePath,
          onOpen,
          onRename,
          onDelete
        )}
      </div>
    </div>
  )
}

function renderWorkspaceEntries(
  parentPath: string,
  entries: WorkspaceEntry[],
  expandedPaths: Record<string, boolean>,
  onTogglePath: (path: string) => void,
  onOpen: (entry: WorkspaceEntry) => void,
  onRename: (entry: WorkspaceEntry) => void,
  onDelete: (entry: WorkspaceEntry) => void
): React.ReactNode {
  return entries
    .filter((entry) => entry.parent_path === parentPath)
    .sort((a, b) =>
      a.entry_type === b.entry_type
        ? a.name.localeCompare(b.name)
        : a.entry_type === 'folder'
          ? -1
          : 1
    )
    .map((entry) => {
      const isFolder = entry.entry_type === 'folder'
      const isOpen = expandedPaths[entry.path] ?? false
      return (
        <div key={entry.id}>
          <div className='group flex items-center gap-1 rounded-md px-2 py-1.5 hover:bg-muted'>
            {isFolder ? (
              <button
                type='button'
                className='rounded p-0.5 hover:bg-background'
                onClick={() => onTogglePath(entry.path)}
              >
                {isOpen ? (
                  <ChevronDown className='size-4' />
                ) : (
                  <ChevronRight className='size-4' />
                )}
              </button>
            ) : (
              <span className='w-4' />
            )}
            {isFolder ? (
              <Folder className='size-4 text-primary' />
            ) : (
              <Table2 className='size-4 text-muted-foreground' />
            )}
            <button
              type='button'
              className='min-w-0 flex-1 truncate text-left text-sm'
              onClick={() => (isFolder ? onTogglePath(entry.path) : onOpen(entry))}
            >
              {entry.name}
            </button>
            <Button
              size='icon'
              variant='ghost'
              className='size-7 opacity-0 group-hover:opacity-100'
              onClick={() => onRename(entry)}
            >
              <Trash2 className='size-3.5' />
            </Button>
          </div>
          {isFolder && isOpen && (
            <div className='ml-3 border-l pl-2'>
              {renderWorkspaceEntries(
                entry.path,
                entries,
                expandedPaths,
                onTogglePath,
                onOpen,
                onRename,
                onDelete
              )}
            </div>
          )}
        </div>
      )
    })
}

function DatabaseExplorer({
  databases,
  expandedDatabases,
  expandedSchemas,
  onToggleDatabase,
  onToggleSchema,
  role,
  setSchemasByDatabase,
}: {
  databases: Array<{ name: string }>
  expandedDatabases: Record<string, boolean>
  expandedSchemas: Record<string, boolean>
  onToggleDatabase: (name: string) => void
  onToggleSchema: (key: string) => void
  role?: string
  setSchemasByDatabase: Dispatch<
    SetStateAction<Record<string, Array<{ name: string }>>>
  >
}) {
  return (
    <div className='p-3'>
      <div className='mb-3 text-sm font-medium'>Database Explorer</div>
      <div className='space-y-1'>
        {databases.map((database) => (
          <DatabaseNode
            key={database.name}
            database={database.name}
            expanded={expandedDatabases[database.name] ?? false}
            expandedSchemas={expandedSchemas}
            onToggleDatabase={onToggleDatabase}
            onToggleSchema={onToggleSchema}
            role={role}
            setSchemasByDatabase={setSchemasByDatabase}
          />
        ))}
      </div>
    </div>
  )
}

function DatabaseNode({
  database,
  expanded,
  expandedSchemas,
  onToggleDatabase,
  onToggleSchema,
  role,
  setSchemasByDatabase,
}: {
  database: string
  expanded: boolean
  expandedSchemas: Record<string, boolean>
  onToggleDatabase: (name: string) => void
  onToggleSchema: (key: string) => void
  role?: string
  setSchemasByDatabase: Dispatch<
    SetStateAction<Record<string, Array<{ name: string }>>>
  >
}) {
  const schemasQuery = useQuery<SchemaResponse>({
    queryKey: ['db-schemas', database, role],
    queryFn: () =>
      api.get<SchemaResponse>(
        `/objects/databases/${encodeURIComponent(database)}/schemas${role ? `?role=${encodeURIComponent(role)}` : ''}`
      ),
    enabled: expanded,
  })

  useEffect(() => {
    if (schemasQuery.data) {
      setSchemasByDatabase((prev) => ({
        ...prev,
        [database]: schemasQuery.data.schemas,
      }))
    }
  }, [database, schemasQuery.data, setSchemasByDatabase])

  return (
    <div>
      <button
        type='button'
        className='flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted'
        onClick={() => onToggleDatabase(database)}
      >
        {expanded ? (
          <ChevronDown className='size-4' />
        ) : (
          <ChevronRight className='size-4' />
        )}
        <Database className='size-4 text-primary' />
        <span className='truncate text-sm'>{database}</span>
      </button>
      {expanded && (
        <div className='ml-5 border-l pl-2'>
          {schemasQuery.data?.schemas.map((schema) => (
            <SchemaNode
              key={`${database}:${schema.name}`}
              database={database}
              schema={schema.name}
              expanded={expandedSchemas[`${database}:${schema.name}`] ?? false}
              onToggleSchema={onToggleSchema}
              role={role}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SchemaNode({
  database,
  schema,
  expanded,
  onToggleSchema,
  role,
}: {
  database: string
  schema: string
  expanded: boolean
  onToggleSchema: (key: string) => void
  role?: string
}) {
  const schemaKey = `${database}:${schema}`
  const treeQuery = useQuery<SchemaTreeResponse>({
    queryKey: ['schema-tree', database, schema, role],
    queryFn: () =>
      api.get<SchemaTreeResponse>(
        `/objects/databases/${encodeURIComponent(database)}/schemas/${encodeURIComponent(schema)}/tree${role ? `?role=${encodeURIComponent(role)}` : ''}`
      ),
    enabled: expanded,
  })

  return (
    <div>
      <button
        type='button'
        className='flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted'
        onClick={() => onToggleSchema(schemaKey)}
      >
        {expanded ? (
          <ChevronDown className='size-4' />
        ) : (
          <ChevronRight className='size-4' />
        )}
        <Folder className='size-4 text-amber-500' />
        <span className='truncate text-sm'>{schema}</span>
      </button>
      {expanded && treeQuery.data && (
        <div className='ml-5 space-y-2 border-l pl-2 py-1'>
          <ObjectGroup title='Tables' items={treeQuery.data.tables} />
          <ObjectGroup title='Views' items={treeQuery.data.views} />
          <ObjectGroup
            title='Materialized Views'
            items={treeQuery.data.materialized_views}
          />
          <ObjectGroup title='Stages' items={treeQuery.data.stages} />
        </div>
      )}
    </div>
  )
}

function ObjectGroup({
  title,
  items,
}: {
  title: string
  items: Array<{ name: string }>
}) {
  if (!items.length) return null
  const isTables = title === 'Tables'
  return (
    <div>
      <div className='px-2 text-xs font-medium tracking-wide text-muted-foreground uppercase'>
        {title}
      </div>
      <div className='mt-1 space-y-1'>
        {items.map((item) => (
          <div
            key={item.name}
            className='flex items-center gap-2 truncate rounded-md px-2 py-1 text-sm hover:bg-muted'
          >
            {isTables ? <Table2 className='size-4 shrink-0 text-primary' /> : null}
            {item.name}
          </div>
        ))}
      </div>
    </div>
  )
}

function QueryResults({ queryResult }: { queryResult: QueryResponse | null }) {
  if (!queryResult) {
    return (
      <div className='p-4 text-sm text-muted-foreground'>
        Run a query to inspect table output here.
      </div>
    )
  }

  if (queryResult.warnings?.length) {
    return (
      <div className='p-4 text-sm text-destructive'>
        {queryResult.warnings.map((w, i) => (
          <div key={i}>{w}</div>
        ))}
      </div>
    )
  }

  if (!queryResult.columns.length) {
    return (
      <div className='p-4 text-sm'>
        Query completed. Affected rows: {queryResult.affected_rows}
      </div>
    )
  }

  return (
    <div className='h-full overflow-auto'>
      <table className='w-full border-separate border-spacing-0 text-sm'>
        <thead className='sticky top-0 z-10 bg-white'>
          <tr>
            {queryResult.columns.map((column, colIndex) => {
              const sample = queryResult.rows[0]?.[colIndex]
              const typeIcon = typeof sample === 'number' ? '#'
                : typeof sample === 'boolean' ? '⊙'
                : typeof sample === 'string'
                  ? (String(sample).match(/^\d{4}-\d{2}-\d{2}/) ? '◷'
                    : String(sample).match(/^[\d.,]+$/) ? '#'
                    : 'A')
                : sample === null ? '∅'
                : '?'
              return (
                <th
                  key={column}
                  className={`border-b border-r border-border px-2 py-1.5 text-left font-normal whitespace-nowrap${colIndex === 0 ? ' border-l' : ''}`}
                >
                  <span className='mr-1.5 text-muted-foreground'>{typeIcon}</span>
                  {column}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {queryResult.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td
                  key={`${rowIndex}-${cellIndex}`}
                  className={`border-b border-r border-border px-2 py-1 whitespace-nowrap${cellIndex === 0 ? ' border-l' : ''}`}
                >
                  {cell === null ? (
                    <span className='text-muted-foreground italic'>NULL</span>
                  ) : (
                    String(cell)
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MonacoSqlEditor({
  value,
  database,
  schema,
  role,
  onChange,
  onRun,
}: {
  value: string
  database: string
  schema: string
  role: string
  onChange: (value?: string) => void
  onRun: () => void
}) {
  const providerRef = useRef<{ dispose(): void } | null>(null)
  const onRunRef = useRef(onRun)
  onRunRef.current = onRun

  async function provideCompletions(
    textUntilPosition: string
  ): Promise<CompletionResponse['items']> {
    const relationContext = extractRelationCompletionContext(textUntilPosition)
    if (relationContext?.type === 'database') {
      const [databases, objects] = await Promise.all([
        api.get<CompletionResponse>(
          `/query/completions?kind=database&role=${encodeURIComponent(role)}&prefix=${encodeURIComponent(relationContext.prefix)}`
        ),
        api.get<CompletionResponse>(
          `/query/completions?kind=object&database=${encodeURIComponent(database)}&schema=${encodeURIComponent(schema)}&role=${encodeURIComponent(role)}&prefix=${encodeURIComponent(relationContext.prefix)}`
        ),
      ])
      return dedupeCompletionItems([...databases.items, ...objects.items])
    }

    if (relationContext?.type === 'schema') {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=schema&database=${encodeURIComponent(relationContext.database)}&role=${encodeURIComponent(role)}&prefix=${encodeURIComponent(relationContext.prefix)}`
      )
      return response.items
    }

    if (relationContext?.type === 'object') {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=object&database=${encodeURIComponent(relationContext.database)}&schema=${encodeURIComponent(relationContext.schema)}&role=${encodeURIComponent(role)}&prefix=${encodeURIComponent(relationContext.prefix)}`
      )
      return response.items
    }

    const stageFileMatch = textUntilPosition.match(/@([A-Za-z0-9_]+)\.([\w.-]*)$/)
    if (stageFileMatch) {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=stage_file&database=${encodeURIComponent(database)}&schema=${encodeURIComponent(schema)}&stage=${encodeURIComponent(stageFileMatch[1])}&prefix=${encodeURIComponent(stageFileMatch[2] ?? '')}`
      )
      return response.items
    }

    const stageMatch = textUntilPosition.match(/@([\w-]*)$/)
    if (stageMatch) {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=stage&database=${encodeURIComponent(database)}&schema=${encodeURIComponent(schema)}&prefix=${encodeURIComponent(stageMatch[1] ?? '')}`
      )
      return response.items
    }

    const aliasMatch = textUntilPosition.match(/([A-Za-z_][\w]*)\.$/)
    if (aliasMatch) {
      const tableName = resolveAliasTable(value, aliasMatch[1])
      if (tableName) {
        const response = await api.get<CompletionResponse>(
          `/query/completions?kind=column&database=${encodeURIComponent(database)}&role=${encodeURIComponent(role)}&table=${encodeURIComponent(tableName)}`
        )
        return response.items
      }
    }

    const objectMatch = textUntilPosition.match(
      /\b(FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]*)$/i
    )
    if (objectMatch) {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=object&database=${encodeURIComponent(database)}&role=${encodeURIComponent(role)}&prefix=${encodeURIComponent(objectMatch[2] ?? '')}`
      )
      return response.items
    }

    return SQL_KEYWORDS.filter((keyword) =>
      keyword.toLowerCase().startsWith(lastWord(textUntilPosition).toLowerCase())
    ).map((keyword) => ({ label: keyword, type: 'keyword' }))
  }

  function handleMount(
    editor: Parameters<NonNullable<ComponentProps<typeof Editor>['onMount']>>[0],
    monaco: Monaco
  ) {
    // Define custom Nova theme with primary color for SQL keywords
    monaco.editor.defineTheme('nova-light', {
      base: 'vs',
      inherit: true,
      rules: [
        { token: 'keyword', foreground: 'd04738', fontStyle: 'bold' },
        { token: 'keyword.sql', foreground: 'd04738', fontStyle: 'bold' },
        { token: 'predefined.sql', foreground: 'd04738' },
        { token: 'operator.sql', foreground: 'd04738' },
        { token: 'string', foreground: '1a8974' },
        { token: 'string.sql', foreground: '1a8974' },
        { token: 'number', foreground: 'c9662e' },
        { token: 'comment', foreground: '8e99a4', fontStyle: 'italic' },
        { token: 'type', foreground: '6b46c1' },
        { token: 'identifier', foreground: '2c3e50' },
        { token: 'predefined', foreground: 'd04738' },
      ],
      colors: {
        'editor.background': '#ffffff',
        'editor.foreground': '#2c3e50',
        'editor.lineHighlightBackground': '#f8f9fa',
        'editor.selectionBackground': '#d0473820',
        'editorCursor.foreground': '#d04738',
      },
    })
    monaco.editor.setTheme('nova-light')

    providerRef.current?.dispose()
    providerRef.current = monaco.languages.registerCompletionItemProvider('sql', {
      triggerCharacters: ['@', '.', ' '],
      provideCompletionItems: async (
        model: Parameters<
          Monaco['languages']['registerCompletionItemProvider']
        >[1] extends { provideCompletionItems: infer T }
          ? T extends (...args: infer A) => unknown
            ? A[0]
            : never
          : never,
        position: Parameters<
          Monaco['languages']['registerCompletionItemProvider']
        >[1] extends { provideCompletionItems: infer T }
          ? T extends (...args: infer A) => unknown
            ? A[1]
            : never
          : never
      ) => {
        const textUntilPosition = model.getValueInRange({
          startLineNumber: 1,
          startColumn: 1,
          endLineNumber: position.lineNumber,
          endColumn: position.column,
        })
        const items = await provideCompletions(textUntilPosition)
      return {
          suggestions: items.map((item) => ({
            label: item.label,
            kind:
              item.type === 'keyword'
                ? monaco.languages.CompletionItemKind.Keyword
                : item.type === 'database'
                  ? monaco.languages.CompletionItemKind.Module
                  : item.type === 'schema'
                    ? monaco.languages.CompletionItemKind.Folder
                    : item.type === 'object'
                      ? monaco.languages.CompletionItemKind.Field
                : item.type === 'column'
                  ? monaco.languages.CompletionItemKind.Field
                  : item.type === 'stage' || item.type === 'stage_file'
                    ? monaco.languages.CompletionItemKind.File
                    : monaco.languages.CompletionItemKind.Variable,
            insertText: item.label,
          })),
        }
      },
    })

    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => onRunRef.current())
  }

  return (
    <Editor
      height='100%'
      language='sql'
      theme='nova-light'
      value={value}
      onChange={onChange}
      onMount={handleMount}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
        fontLigatures: true,
        fontWeight: '400',
        lineHeight: 22,
        automaticLayout: true,
        wordWrap: 'on',
        scrollBeyondLastLine: false,
        renderLineHighlight: 'none',
        overviewRulerBorder: false,
        hideCursorInOverviewRuler: true,
        padding: { top: 10, bottom: 12 },
      }}
    />
  )
}

function dedupeCompletionItems(items: CompletionResponse['items']) {
  const seen = new Set<string>()
  return items.filter((item) => {
    const key = `${item.type}:${item.label}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function extractRelationCompletionContext(text: string):
  | { type: 'database'; prefix: string }
  | { type: 'schema'; database: string; prefix: string }
  | { type: 'object'; database: string; schema: string; prefix: string }
  | null {
  const objectMatch = text.match(
    /\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]*)$/i
  )
  if (objectMatch) {
    return {
      type: 'object',
      database: objectMatch[1],
      schema: objectMatch[2],
      prefix: objectMatch[3] ?? '',
    }
  }

  const schemaMatch = text.match(
    /\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]+)\.([A-Za-z0-9_]*)$/i
  )
  if (schemaMatch) {
    return {
      type: 'schema',
      database: schemaMatch[1],
      prefix: schemaMatch[2] ?? '',
    }
  }

  const databaseMatch = text.match(
    /\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]*)$/i
  )
  if (databaseMatch) {
    return {
      type: 'database',
      prefix: databaseMatch[1] ?? '',
    }
  }

  return null
}

function resolveAliasTable(sql: string, alias: string) {
  const regex =
    /\b(?:FROM|JOIN)\s+([A-Za-z0-9_.`]+)(?:\s+(?:AS\s+)?([A-Za-z0-9_]+))?/gi
  let match: RegExpExecArray | null = regex.exec(sql)
  while (match) {
    if (match[2] === alias) {
      return match[1].replace(/`/g, '').split('.').pop() ?? null
    }
    match = regex.exec(sql)
  }
  return null
}

function lastWord(text: string) {
  return text.split(/\s+/).pop() ?? ''
}

function isDestructiveSql(sql: string) {
  return /^\s*(DROP|TRUNCATE|ALTER\s+TABLE\s+.+\s+DROP|DELETE\s+FROM|UPDATE\s+)/i.test(
    sql
  )
}
