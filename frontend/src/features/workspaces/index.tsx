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
import { createPortal } from 'react-dom'
import { toast } from 'sonner'
import { format as formatSql } from 'sql-formatter'
import Editor, { type Monaco } from '@monaco-editor/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BarChart3,
  Braces,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  Database,
  Download,
  FileCode,
  Folder,
  GripHorizontal,
  Hash,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Route,
  Square,
  RotateCcw,
  Search,
  Table2,
  Type,
  UserRoundCog,
  X,
} from 'lucide-react'
import * as XLSX from 'xlsx'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Header } from '@/components/layout/header'
import { DatabaseSchemaSelector } from './database-schema-selector'
import {
  type CompletionResponse,
  buildStageCompletionPath,
  extractStageCompletionContext,
  formatStageCompletionDetail,
  getStageFileExtension,
  getStageCompletionInsertText,
  getStageCompletionKind,
  getStageCompletionReplacementRange,
  getStageCompletionSortText,
  shouldTriggerStageSuggestions,
} from './stage-completion'
import { useTheme } from '@/context/theme-provider'
import { InlineSelect } from './inline-select'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { SidebarMenu, SidebarMenuItem, SidebarMenuSub, SidebarMenuSubItem } from '@/components/ui/sidebar'

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

type HistoryItem = {
  query_id: string
  event_time: string
  sql_text: string
  status: string
  duration_ms: number | null
  rows_affected: number | null
  error_message: string | null
  file_id: string | null
  database_name: string | null
  schema_name: string | null
}

type HistoryResponse = {
  items: HistoryItem[]
  total: number
}

type MonacoEditorInstance = Parameters<NonNullable<ComponentProps<typeof Editor>['onMount']>>[0]

let activeSqlEditorInstance: MonacoEditorInstance | null = null

function getSqlForExecution(fallbackSql: string) {
  const editor = activeSqlEditorInstance
  const selection = editor?.getSelection()
  const selectedSql =
    editor && selection && !selection.isEmpty()
      ? editor.getModel()?.getValueInRange(selection).trim()
      : ''

  return selectedSql || fallbackSql.trim()
}

const SQL_KEYWORDS = [
  // Core DQL
  'SELECT',
  'FROM',
  'WHERE',
  'AND',
  'OR',
  'NOT',
  'IN',
  'NOT IN',
  'BETWEEN',
  'LIKE',
  'IS NULL',
  'IS NOT NULL',
  'AS',
  'DISTINCT',
  'ALL',
  'ANY',
  'EXISTS',
  'CASE',
  'WHEN',
  'THEN',
  'ELSE',
  'END',
  'CAST',
  'COALESCE',
  'NULLIF',
  'IF',
  'IFNULL',
  // Joins
  'JOIN',
  'INNER JOIN',
  'LEFT JOIN',
  'RIGHT JOIN',
  'FULL OUTER JOIN',
  'CROSS JOIN',
  'ON',
  'USING',
  // Aggregation & Grouping
  'GROUP BY',
  'HAVING',
  'ORDER BY',
  'ASC',
  'DESC',
  'LIMIT',
  'OFFSET',
  'WITH ROLLUP',
  'WITH',
  // Set Operations
  'UNION',
  'UNION ALL',
  'INTERSECT',
  'EXCEPT',
  'MINUS',
  // Subquery
  'LATERAL',
  'TABLESAMPLE',
  // Window Functions
  'OVER',
  'PARTITION BY',
  'ROWS',
  'RANGE',
  'UNBOUNDED PRECEDING',
  'UNBOUNDED FOLLOWING',
  'CURRENT ROW',
  // DML
  'INSERT INTO',
  'INSERT INTO ... VALUES',
  'INSERT INTO ... SELECT',
  'UPDATE',
  'DELETE FROM',
  'DELETE',
  'MERGE INTO',
  'VALUES',
  'SET',
  'ON DUPLICATE KEY UPDATE',
  'ON CONFLICT DO UPDATE',
  'ON CONFLICT DO NOTHING',
  // DDL
  'CREATE TABLE',
  'CREATE VIEW',
  'CREATE MATERIALIZED VIEW',
  'CREATE INDEX',
  'CREATE DATABASE',
  'CREATE FUNCTION',
  'CREATE EXTERNAL TABLE',
  'CREATE ROUTINE LOAD',
  'CREATE PIPE',
  'CREATE RESOURCE',
  'CREATE STORAGE VOLUME',
  'ALTER TABLE',
  'ALTER VIEW',
  'ALTER DATABASE',
  'DROP TABLE',
  'DROP VIEW',
  'DROP DATABASE',
  'DROP INDEX',
  'DROP MATERIALIZED VIEW',
  'DROP FUNCTION',
  'TRUNCATE TABLE',
  'RENAME TABLE',
  'ADD COLUMN',
  'DROP COLUMN',
  'MODIFY COLUMN',
  // Transaction & Session
  'BEGIN',
  'COMMIT',
  'ROLLBACK',
  'SAVEPOINT',
  'SET VARIABLE',
  'SET PROPERTY',
  'SET CATALOG',
  // Data Loading
  'LOAD LABEL',
  'CANCEL LOAD',
  'SHOW LOAD',
  'STREAM LOAD',
  'BROKER LOAD',
  'ROUTINE LOAD',
  'SHOW STREAM LOAD',
  'CANCEL STREAM LOAD',
  // StarRocks-specific
  'SHOW DATABASES',
  'SHOW TABLES',
  'SHOW COLUMNS',
  'SHOW CREATE TABLE',
  'SHOW PROCESSLIST',
  'SHOW VARIABLES',
  'SHOW BACKENDS',
  'SHOW FRONTENDS',
  'SHOW BROKER',
  'SHOW RESOURCES',
  'SHOW ROUTINE LOAD',
  'SHOW MATERIALIZED VIEWS',
  'SHOW PARTITIONS',
  'SHOW TABLET',
  'SHOW SNAPSHOT',
  'SHOW CATALOGS',
  'DESC',
  'DESCRIBE',
  'EXPLAIN',
  'EXPLAIN VERBOSE',
  'EXPLAIN COSTS',
  'EXPLAIN ANALYZE',
  'ANALYZE TABLE',
  'ANALYZE PROFILE',
  'KILL QUERY',
  'KILL CONNECTION',
  'GRANT',
  'REVOKE',
  'CREATE USER',
  'DROP USER',
  'ALTER USER',
  'CREATE ROLE',
  'DROP ROLE',
  'GRANT ROLE',
  'SHOW GRANTS',
  'SHOW ROLES',
  'ADMIN',
  'ADMIN SET',
  'ADMIN SHOW',
  'REFRESH',
  'REFRESH MATERIALIZED VIEW',
  'SUBMIT',
  'CANCEL',
  'RECOVER',
  'INSTALL',
  'UNINSTALL',
  'SHOW PLUGINS',
  // Aggregate Functions
  'COUNT',
  'SUM',
  'AVG',
  'MIN',
  'MAX',
  'COUNT(DISTINCT',
  'APPROX_COUNT_DISTINCT',
  'NDV',
  'GROUP_CONCAT',
  'BITMAP_UNION',
  'BITMAP_INTERSECT',
  'HLL_UNION',
  'HLL_CARDINALITY',
  'PERCENTILE_APPROX',
  'VARIANCE',
  'VAR_SAMP',
  'VAR_POP',
  'STDDEV',
  'STDDEV_SAMP',
  'STDDEV_POP',
  'ANY_VALUE',
  'BIT_AND',
  'BIT_OR',
  'BIT_XOR',
  // String Functions
  'CONCAT',
  'CONCAT_WS',
  'LENGTH',
  'CHAR_LENGTH',
  'LOWER',
  'UPPER',
  'LCASE',
  'UCASE',
  'LTRIM',
  'RTRIM',
  'TRIM',
  'LPAD',
  'RPAD',
  'SUBSTR',
  'SUBSTRING',
  'LEFT',
  'RIGHT',
  'REPLACE',
  'REVERSE',
  'REPEAT',
  'SPACE',
  'LOCATE',
  'INSTR',
  'POSITION',
  'HEX',
  'UNHEX',
  'ENCODE',
  'DECODE',
  'SPLIT',
  'SPLIT_PART',
  'REGEXP_REPLACE',
  'REGEXP_EXTRACT',
  'STR_TO_MAP',
  'PARSE_URL',
  'URL_ENCODE',
  'URL_DECODE',
  'CHAR',
  'ASCII',
  'FROM_BASE64',
  'TO_BASE64',
  'MONEY_FORMAT',
  'FORMAT',
  // Date/Time Functions
  'NOW',
  'CURDATE',
  'CURTIME',
  'CURRENT_DATE',
  'CURRENT_TIME',
  'CURRENT_TIMESTAMP',
  'DATE',
  'DATETIME',
  'TIMESTAMP',
  'DATE_ADD',
  'DATE_SUB',
  'DATE_DIFF',
  'DATEDIFF',
  'DATE_FORMAT',
  'DATE_TRUNC',
  'DATE_SLICE',
  'YEAR',
  'MONTH',
  'DAY',
  'HOUR',
  'MINUTE',
  'SECOND',
  'WEEK',
  'WEEKDAY',
  'DAYOFWEEK',
  'DAYOFYEAR',
  'QUARTER',
  'FROM_UNIXTIME',
  'UNIX_TIMESTAMP',
  'TO_DATE',
  'STR_TO_DATE',
  'TIME_TO_SEC',
  'SEC_TO_TIME',
  'MONTHS_ADD',
  'MONTHS_SUB',
  'YEARS_ADD',
  'YEARS_SUB',
  'HOURS_ADD',
  'HOURS_SUB',
  'MINUTES_ADD',
  'MINUTES_SUB',
  'SECONDS_ADD',
  'SECONDS_SUB',
  'MILLISECONDS_ADD',
  'LAST_DAY',
  'NEXT_DAY',
  'TIME_SLICE',
  'NOW',
  'UTC_TIMESTAMP',
  // Math Functions
  'ABS',
  'CEIL',
  'CEILING',
  'FLOOR',
  'ROUND',
  'TRUNCATE',
  'MOD',
  'POWER',
  'POW',
  'SQRT',
  'EXP',
  'LN',
  'LOG',
  'LOG2',
  'LOG10',
  'SIGN',
  'PI',
  'E',
  'RAND',
  'RANDOM',
  'SIN',
  'COS',
  'TAN',
  'ASIN',
  'ACOS',
  'ATAN',
  'ATAN2',
  'DEGREES',
  'RADIANS',
  'COT',
  'CONV',
  'BIN',
  'GREATEST',
  'LEAST',
  'WIDTH_BUCKET',
  'Pmod',
  // Conditional Functions
  'CASE WHEN',
  'IF',
  'IFNULL',
  'NULLIF',
  'COALESCE',
  'NVL',
  'NVL2',
  'DECODE',
  // JSON Functions
  'JSON_OBJECT',
  'JSON_ARRAY',
  'JSON_EXTRACT',
  'JSON_QUERY',
  'JSON_VALUE',
  'JSON_SET',
  'JSON_REPLACE',
  'JSON_REMOVE',
  'JSON_INSERT',
  'JSON_KEYS',
  'JSON_TYPE',
  'GET_JSON_STRING',
  'GET_JSON_INT',
  'GET_JSON_DOUBLE',
  'JSON_EACH',
  'PARSE_JSON',
  'TO_JSON',
  // Array Functions
  'ARRAY',
  'ARRAY_AGG',
  'ARRAY_CONCAT',
  'ARRAY_CONTAINS',
  'ARRAY_LENGTH',
  'ARRAY_SLICE',
  'ARRAY_DISTINCT',
  'ARRAY_SORT',
  'ARRAY_JOIN',
  'ARRAY_MAX',
  'ARRAY_MIN',
  'ARRAY_SUM',
  'ARRAY_AVG',
  'ARRAY_POSITION',
  'ARRAY_REMOVE',
  'ARRAY_MAP',
  'ARRAY_FILTER',
  'ARRAY_GENERATE',
  'ARRAY_TO_STRING',
  'ARRAYS_OVERLAP',
  'ARRAYS_INTERSECT',
  'CARDINALITY',
  // Map Functions
  'MAP',
  'MAP_KEYS',
  'MAP_VALUES',
  'MAP_CONCAT',
  'MAP_FROM_ARRAYS',
  'ELEMENT_AT',
  // Window Functions (named)
  'ROW_NUMBER',
  'RANK',
  'DENSE_RANK',
  'NTILE',
  'LAG',
  'LEAD',
  'FIRST_VALUE',
  'LAST_VALUE',
  'NTH_VALUE',
  'CUME_DIST',
  'PERCENT_RANK',
  // Bitmap Functions
  'BITMAP_EMPTY',
  'BITMAP_HASH',
  'BITMAP_HAS',
  'BITMAP_COUNT',
  'BITMAP_OR',
  'BITMAP_AND',
  'BITMAP_XOR',
  'BITMAP_NOT',
  'BITMAP_AGG',
  'TO_BITMAP',
  'BITMAP_FROM_STRING',
  'BITMAP_TO_STRING',
  'BITMAP_FROM_BINARY',
  'BITMAP_TO_BINARY',
  'BITMAP_FROM_ARRAY',
  'BITMAP_TO_ARRAY',
  'SUB_BITMAP',
  'BITMAP_MAX',
  'BITMAP_MIN',
  'BITMAP_ANDNOT',
  'BITMAP_SUBSET_LIMIT',
  'BITMAP_SUBSET_IN_RANGE',
  'BITMAP_REMOVE',
  // HLL Functions
  'HLL_EMPTY',
  'HLL_HASH',
  'HLL_UNION_AGG',
  'HLL_RAW_AGG',
  'HLL_MERGE',
  // Hash Functions
  'MD5',
  'MD5SUM',
  'MURMUR_HASH3_32',
  'MURMUR_HASH3_64',
  'XXHASH_32',
  'XXHASH_64',
  'SHA2',
  'SHA',
  // Utility
  'SLEEP',
  'UUID',
  'LAST_QUERY_ID',
  'CONNECTION_ID',
  'DATABASE',
  'SCHEMA',
  'VERSION',
  'CURRENT_USER',
  'SESSION_USER',
  'USER',
  // Table Function
  'UNNEST',
  'GENERATE',
  'FILES',
  // ML / AI Functions (StarRocks 3.x)
  'ML_PREDICT',
  'AI_COMPLETE',
]

export function WorkspacesPage() {
  const queryClient = useQueryClient()
  const [sidebarTab, setSidebarTab] = useState<'workspaces' | 'databases'>('workspaces')
  const [secondaryCollapsed, setSecondaryCollapsed] = useState(false)
  const [workspaceSearch, setWorkspaceSearch] = useState('')
  const [databaseSearch, setDatabaseSearch] = useState('')
  const [tabs, setTabs] = useState<Record<string, WorkspaceTabState>>({})
  const [openTabIds, setOpenTabIds] = useState<string[]>([])
  const [activeTabId, setActiveTabId] = useState<string | null>(null)
  const [expandedWorkspacePaths, setExpandedWorkspacePaths] = useState<Record<string, boolean>>({ '': true })
  const [expandedDatabases, setExpandedDatabases] = useState<Record<string, boolean>>({})
  const [expandedSchemas, setExpandedSchemas] = useState<Record<string, boolean>>({})
  const [schemasByDatabase, setSchemasByDatabase] = useState<Record<string, Array<{ name: string }>>>({})
  const [resultsHeight, setResultsHeight] = useState(350)
  const [resultsCollapsed, setResultsCollapsed] = useState(false)
  const [resultsTab, setResultsTab] = useState<'results' | 'history' | 'explain' | 'chart'>('results')
  const [isResizingResults, setIsResizingResults] = useState(false)
  const [explainResult, setExplainResult] = useState<string | null>(null)
  const [explaining, setExplaining] = useState(false)
  const [historyFilter, setHistoryFilter] = useState<'file' | 'all'>('file')
  const [queryResults, setQueryResults] = useState<QueryResponse[] | null>(() => {
    try {
      const cached = sessionStorage.getItem('nova:last-query-results')
      if (cached) return JSON.parse(cached) as QueryResponse[]
      // Backward compat: try old single-result key
      const old = sessionStorage.getItem('nova:last-query-result')
      if (old) {
        const r = JSON.parse(old) as QueryResponse
        sessionStorage.removeItem('nova:last-query-result')
        sessionStorage.setItem('nova:last-query-results', JSON.stringify([r]))
        return [r]
      }
      return null
    } catch {
      return null
    }
  })
  const [activeResultIdx, setActiveResultIdx] = useState(0)
  const activeResult = queryResults?.[activeResultIdx] ?? null
  const [running, setRunning] = useState(false)
  const [elapsedMs, setElapsedMs] = useState(0)
  const abortRef = useRef<AbortController | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [renamingTabId, setRenamingTabId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

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

  const historyQuery = useQuery<HistoryResponse>({
    queryKey: ['query-history', historyFilter === 'file' ? activeTabId : 'all'],
    queryFn: () =>
      api.get<HistoryResponse>(
        `/query/history?limit=50${historyFilter === 'file' && activeTabId ? `&file_id=${encodeURIComponent(activeTabId)}` : ''}`
      ),
    enabled: resultsTab === 'history',
    refetchInterval: resultsTab === 'history' ? 5000 : false,
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
          database: tree.defaults.database ?? context.defaults.database ?? context.databases[0] ?? '',
          schema: tree.defaults.schema ?? context.defaults.schema ?? context.schemas[0] ?? 'default',
          role: tree.defaults.role ?? context.defaults.role ?? context.roles[0] ?? '',
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
        .catch(() => {})
    }, 700)
    return () => {
      if (saveTimerRef.current) {
        window.clearTimeout(saveTimerRef.current)
      }
    }
  }, [activeTab])

  // Persist last query result to sessionStorage for page refresh recovery
  useEffect(() => {
    if (queryResults) {
      try {
        sessionStorage.setItem('nova:last-query-results', JSON.stringify(queryResults))
      } catch {
        // Ignore quota errors for large result sets
      }
    }
  }, [queryResults])

  useEffect(() => {
    const onMouseMove = (event: MouseEvent) => {
      if (!dragRef.current) return
      const delta = dragRef.current.startY - event.clientY
      setResultsHeight(Math.max(180, dragRef.current.startHeight + delta))
    }
    const onMouseUp = () => {
      dragRef.current = null
      setIsResizingResults(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
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
        void saveFile(activeTab.id, activeTab.content, activeTab.database, activeTab.schema, activeTab.role)
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
  }, [
    activeTab?.database,
    activeTab?.role,
    activeTab?.schema,
    activeTabId,
    openTabIds,
    queryContextQuery.data,
    secondaryCollapsed,
    workspaceTreeQuery.data,
  ])

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
        database: prev[id]?.database ?? defaults?.database ?? context?.defaults.database ?? context?.databases[0] ?? '',
        schema: prev[id]?.schema ?? defaults?.schema ?? context?.defaults.schema ?? 'default',
        role: prev[id]?.role ?? defaults?.role ?? context?.defaults.role ?? context?.roles[0] ?? '',
        loaded: true,
      },
    }))
  }

  async function saveFile(id: string, content: string, database: string, schema: string, role: string) {
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

  function generateUniqueFileName(): string {
    const existingNames = new Set(Object.values(tabs).map((t) => t.title.toLowerCase()))
    const base = 'Untitled'
    const first = `${base}.sql`
    if (!existingNames.has(first.toLowerCase())) return first
    let i = 2
    while (existingNames.has(`${base}-${i}.sql`.toLowerCase())) i++
    return `${base}-${i}.sql`
  }

  async function createNewFile() {
    const fileName = generateUniqueFileName()
    try {
      const response = await api.post<WorkspaceFileResponse>('/workspaces/files', {
        name: fileName,
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
            schema: activeTab?.schema ?? queryContextQuery.data?.defaults.schema ?? 'default',
            role: activeTab?.role ?? queryContextQuery.data?.defaults.role ?? queryContextQuery.data?.roles[0] ?? '',
            loaded: true,
          },
        }))
      })
    } catch {
      // Silent fail — file creation error
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

  async function renameTabFile(tabId: string, newName: string) {
    const trimmed = newName.trim()
    if (!trimmed) return
    const tab = tabs[tabId]
    if (!tab || trimmed === tab.title) {
      setRenamingTabId(null)
      return
    }
    // Check uniqueness among open tabs
    const existingNames = Object.entries(tabs)
      .filter(([id]) => id !== tabId)
      .map(([, t]) => t.title.toLowerCase())
    if (existingNames.includes(trimmed.toLowerCase())) {
      toast.error(`A file named "${trimmed}" already exists.`)
      return
    }
    try {
      await api.post('/workspaces/rename', {
        id: tabId,
        name: trimmed,
        parent_path: '',
      })
      setTabs((prev) => ({
        ...prev,
        [tabId]: { ...prev[tabId], title: trimmed },
      }))
      await queryClient.invalidateQueries({ queryKey: ['workspace-tree'] })
    } catch {
      toast.error('Failed to rename file.')
    } finally {
      setRenamingTabId(null)
    }
  }

  async function runQuery(confirmDestructive = false, sqlOverride?: string) {
    if (!activeTab) return
    const sql =
      sqlOverride?.trim() ||
      getSqlForExecution(editorContentRef.current || activeTab.content)
    if (!sql) return
    if (!confirmDestructive && isDestructiveSql(sql)) {
      const ok = window.confirm('This query looks destructive. Do you want to run it?')
      if (!ok) return
      return runQuery(true, sql)
    }
    setRunning(true)
    setQueryResults(null)
    setActiveResultIdx(0)
    setElapsedMs(0)
    const controller = new AbortController()
    abortRef.current = controller
    const startTime = Date.now()
    timerRef.current = setInterval(() => setElapsedMs(Date.now() - startTime), 100)
    try {
      const response = await api.post<QueryResponse[]>(
        '/query/execute',
        {
          sql,
          database: activeTab.database || null,
          schema: activeTab.schema || null,
          role: activeTab.role || null,
          max_rows: 500,
          file_id: activeTab.id,
          confirm_destructive: confirmDestructive,
        },
        controller.signal
      )
      setQueryResults(response)
      void queryClient.invalidateQueries({ queryKey: ['query-history'] })
      if (activeTab.content !== activeTab.savedContent) {
        await saveFile(activeTab.id, activeTab.content, activeTab.database, activeTab.schema, activeTab.role)
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setQueryResults([
          {
            success: false,
            columns: [],
            rows: [],
            row_count: 0,
            affected_rows: 0,
            elapsed_ms: Date.now() - startTime,
            original_sql: sql,
            executed_sql: '',
            warnings: [`Query cancelled after ${((Date.now() - startTime) / 1000).toFixed(1)}s`],
            destructive: false,
            needs_confirmation: false,
          },
        ])
      } else {
        setQueryResults([
          {
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
          },
        ])
      }
      void queryClient.invalidateQueries({ queryKey: ['query-history'] })
    } finally {
      if (timerRef.current) clearInterval(timerRef.current)
      abortRef.current = null
      setRunning(false)
    }
  }

  async function runExplain() {
    if (!activeTab) return
    const sql = editorContentRef.current.trim() || activeTab.content.trim()
    if (!sql) return
    setExplaining(true)
    setExplainResult(null)
    setResultsTab('explain')
    try {
      const response = await api.post<QueryResponse>('/query/explain', {
        sql,
        database: activeTab.database || null,
        schema: activeTab.schema || null,
        role: activeTab.role || null,
      })
      // EXPLAIN returns rows with a single column containing plan text
      const planText = response.rows.map((row) => row[0]).join('\n')
      setExplainResult(planText)
    } catch (error) {
      setExplainResult(error instanceof Error ? error.message : 'EXPLAIN failed')
    } finally {
      setExplaining(false)
    }
  }

  async function flushTabSave(tabId: string | null) {
    if (!tabId) return
    const tab = tabs[tabId]
    if (!tab || !tab.loaded || tab.content === tab.savedContent) return
    await saveFile(tab.id, tab.content, tab.database, tab.schema, tab.role)
  }

  function exportToExcel() {
    if (!queryResults?.length) return
    const workbook = XLSX.utils.book_new()
    const baseName = activeTab?.title?.replace(/\.sql$/i, '') ?? 'Query Results'
    queryResults.forEach((qr, idx) => {
      if (!qr.columns.length) return
      const data = qr.rows.map((row) => {
        const obj: Record<string, unknown> = {}
        qr.columns.forEach((col, i) => {
          obj[col] = row[i]
        })
        return obj
      })
      const worksheet = XLSX.utils.json_to_sheet(data)
      const sheetName = queryResults.length === 1 ? baseName.slice(0, 31) : `Result ${idx + 1}`
      XLSX.utils.book_append_sheet(workbook, worksheet, sheetName)
    })
    const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
    XLSX.writeFile(workbook, `${baseName}_${timestamp}.xlsx`)
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
    event.preventDefault()
    dragRef.current = {
      startY: event.clientY,
      startHeight: resultsHeight,
    }
    setIsResizingResults(true)
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
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
        <aside className={cn('border-r bg-muted/20 transition-all duration-200', secondaryCollapsed ? 'w-14' : 'w-80')}>
          <div className='flex h-full min-h-0 flex-col'>
            <div className='border-b px-3 py-3'>
              <div className='flex items-center justify-between gap-2'>
                {!secondaryCollapsed && (
                  <Tabs
                    value={sidebarTab}
                    onValueChange={(value) => setSidebarTab(value as 'workspaces' | 'databases')}
                    className='w-full'
                  >
                    <TabsList className='grid w-full grid-cols-2'>
                      <TabsTrigger value='workspaces'>Workspaces</TabsTrigger>
                      <TabsTrigger value='databases'>Databases</TabsTrigger>
                    </TabsList>
                  </Tabs>
                )}
                <Button variant='outline' size='icon' onClick={() => setSecondaryCollapsed((prev) => !prev)}>
                  <ChevronRight className={cn('size-4 transition-transform', !secondaryCollapsed && 'rotate-180')} />
                </Button>
              </div>
              {!secondaryCollapsed && sidebarTab === 'workspaces' && (
                <div className='mt-3'>
                  <Button
                    size='sm'
                    variant='outline'
                    className='w-full justify-center gap-2'
                    onClick={() => void createNewFile()}
                  >
                    <Plus className='size-4' />
                    New File
                  </Button>
                </div>
              )}
            </div>

            {!secondaryCollapsed && (
              <>
                <div className='border-b px-3 py-3'>
                  <div className='flex items-center gap-1.5'>
                    <div className='relative flex-1'>
                      <Search className='absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground' />
                      <Input
                        value={sidebarTab === 'workspaces' ? workspaceSearch : databaseSearch}
                        onChange={(event) =>
                          sidebarTab === 'workspaces'
                            ? setWorkspaceSearch(event.target.value)
                            : setDatabaseSearch(event.target.value)
                        }
                        placeholder={sidebarTab === 'workspaces' ? 'Search files' : 'Search databases'}
                        className='pl-9'
                      />
                    </div>
                    <button
                      type='button'
                      className='shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground'
                      title='Refresh'
                      onClick={() => {
                        setRefreshing(true)
                        setTimeout(() => setRefreshing(false), 1000)
                        if (sidebarTab === 'workspaces') {
                          void queryClient.invalidateQueries({
                            queryKey: ['workspace-tree'],
                          })
                        } else {
                          void queryClient.invalidateQueries({
                            queryKey: ['object-databases'],
                          })
                          void queryClient.invalidateQueries({
                            queryKey: ['db-schemas'],
                          })
                          void queryClient.invalidateQueries({
                            queryKey: ['schema-tree'],
                          })
                        }
                      }}
                    >
                      <RefreshCw className={cn('size-3.5 transition-transform', refreshing && 'animate-spin')} />
                    </button>
                  </div>
                </div>
                <ScrollArea className='min-h-0 flex-1'>
                  {sidebarTab === 'workspaces' ? (
                    <WorkspaceTree
                      entries={filteredEntries}
                      activeEntryId={activeTabId}
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
                              schema: activeTab?.schema ?? queryContextQuery.data?.defaults.schema ?? 'default',
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
          <div className='flex items-end border-b border-border px-2 pt-2'>
            <div className='flex min-w-0 flex-1 items-end gap-0'>
              {openTabIds.map((id) => {
                const tab = tabs[id]
                if (!tab) return null
                const isActive = activeTabId === id
                const isRenaming = renamingTabId === id
                return (
                  <div key={id} className='group relative flex min-w-[120px] max-w-[260px] items-center'>
                    {isRenaming ? (
                      <Input
                        autoFocus
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') void renameTabFile(id, renameValue)
                          if (e.key === 'Escape') setRenamingTabId(null)
                        }}
                        onBlur={() => void renameTabFile(id, renameValue)}
                        onFocus={(e) => {
                          const dotIdx = renameValue.lastIndexOf('.')
                          e.target.setSelectionRange(0, dotIdx > 0 ? dotIdx : renameValue.length)
                        }}
                        className={cn(
                          'mx-1.5 h-6 flex-1 rounded border py-0 pl-2 pr-1 text-xs shadow-none outline-none focus-visible:ring-0 focus-visible:ring-offset-0',
                          isActive
                            ? 'z-10 -mb-px border-border bg-background text-primary'
                            : 'border-border bg-muted/40 text-foreground'
                        )}
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <button
                        type='button'
                        onClick={() => activateTab(id)}
                        className={cn(
                          'flex flex-1 items-center gap-2 rounded-t-md py-1.5 pr-14 pl-3 text-sm transition-colors',
                          isActive
                            ? 'z-10 -mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary'
                            : 'border-b border-b-border bg-muted/40 text-muted-foreground hover:bg-muted/60'
                        )}
                      >
                        <span className='truncate'>{tab.title}</span>
                      </button>
                    )}
                    {/* 3-dot menu + close — hidden when renaming */}
                    {!isRenaming && (
                      <div
                        className={cn(
                          'absolute right-1 z-20 flex items-center gap-0.5 rounded-sm transition-opacity',
                          isActive ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                        )}
                      >
                        <TabMenuButton
                          onRename={() => {
                            setRenameValue(tab.title)
                            setRenamingTabId(id)
                          }}
                          onDownload={() => {
                            const blob = new Blob([tab.content], {
                              type: 'text/sql',
                            })
                            const url = URL.createObjectURL(blob)
                            const a = document.createElement('a')
                            a.href = url
                            a.download = tab.title.endsWith('.sql') ? tab.title : `${tab.title}.sql`
                            a.click()
                            URL.revokeObjectURL(url)
                          }}
                          onClose={() => closeTab(id)}
                        />
                        <X
                          className='size-3.5 shrink-0 cursor-pointer rounded-sm p-0.5 hover:bg-muted-foreground/20'
                          onClick={(event) => {
                            event.stopPropagation()
                            closeTab(id)
                          }}
                        />
                      </div>
                    )}
                  </div>
                )
              })}
              <button
                type='button'
                className='mb-0.5 ml-0.5 shrink-0 rounded-t-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground'
                title='New file'
                onClick={() => void createNewFile()}
              >
                <Plus className='size-3.5' />
              </button>
            </div>
          </div>

          {activeTab ? (
            <>
              <div className='flex flex-wrap items-center gap-3 px-4 py-3'>
                {running ? (
                  <Button size='sm' variant='destructive' onClick={() => abortRef.current?.abort()}>
                    <Square className='size-4' />
                    Cancel {(elapsedMs / 1000).toFixed(1)}s
                  </Button>
                ) : (
                  <Button size='sm' onClick={() => runQuery()}>
                    <Play className='size-4' />
                    Run
                  </Button>
                )}
                <Button
                  size='sm'
                  variant='outline'
                  title='Format SQL (Ctrl+Shift+F)'
                  onClick={() => {
                    activeSqlEditorInstance?.getAction('editor.action.formatDocument')?.run()
                  }}
                >
                  <Braces className='size-4' />
                  Format
                </Button>
                <Button
                  size='sm'
                  variant='outline'
                  title='Explain query plan'
                  onClick={() => void runExplain()}
                  disabled={explaining}
                >
                  <Route className='size-4' />
                  {explaining ? 'Explaining...' : 'Explain'}
                </Button>
                <div className='flex flex-1 flex-wrap items-center justify-end gap-2'>
                  <InlineSelect
                    label='Role'
                    value={activeTab.role}
                    options={queryContextQuery.data?.roles ?? []}
                    icon={<UserRoundCog className='size-3.5' />}
                    onChange={(value) =>
                      setTabs((prev) => ({
                        ...prev,
                        [activeTab.id]: { ...prev[activeTab.id], role: value },
                      }))
                    }
                  />
                  <DatabaseSchemaSelector
                    databases={queryContextQuery.data?.databases ?? []}
                    selectedDatabase={activeTab.database}
                    schemas={activeTab.database ? (schemasByDatabase[activeTab.database] ?? []) : []}
                    selectedSchema={activeTab.schema}
                    onSelectDatabase={async (value) => {
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
                    onSelectSchema={(value) =>
                      setTabs((prev) => ({
                        ...prev,
                        [activeTab.id]: {
                          ...prev[activeTab.id],
                          schema: value,
                        },
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
                  className={cn(
                    'mx-4 mt-2 flex shrink-0 flex-col overflow-hidden rounded-t-xl border bg-background',
                    isResizingResults ? 'transition-none' : 'transition-[height] duration-200 ease-in-out'
                  )}
                >
                  {!resultsCollapsed && (
                    <button
                      type='button'
                      aria-label='Resize results panel'
                      className='group flex h-3 w-full shrink-0 cursor-row-resize items-center justify-center bg-muted/15 hover:bg-muted/30'
                      onMouseDown={startResultsResize}
                    >
                      <span className='h-1 w-16 rounded-full bg-border transition-colors group-hover:bg-muted-foreground/40' />
                    </button>
                  )}
                  <div className='flex items-end border-b border-border px-2 pt-1'>
                    <button
                      type='button'
                      className='mb-1.5 mr-1 rounded p-0.5 hover:bg-muted'
                      onClick={() => setResultsCollapsed((prev) => !prev)}
                    >
                      <ChevronDown className={cn('size-3.5 transition-transform', resultsCollapsed && '-rotate-90')} />
                    </button>
                    <div className='flex items-end gap-0'>
                      <button
                        type='button'
                        onClick={() => setResultsTab('results')}
                        className={cn(
                          'flex items-center gap-1.5 rounded-t-md px-3 py-1 text-xs transition-colors',
                          resultsTab === 'results'
                            ? 'z-10 -mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary'
                            : 'border-b border-b-border text-muted-foreground hover:bg-muted/50'
                        )}
                      >
                        Results
                        {queryResults && queryResults.length > 0 && (
                          <span className='text-muted-foreground'>
                            {queryResults.reduce((s, r) => s + r.row_count, 0)}r •{' '}
                            {queryResults.reduce((s, r) => s + r.elapsed_ms, 0).toFixed(0)}
                            ms
                          </span>
                        )}
                      </button>
                      <button
                        type='button'
                        onClick={() => setResultsTab('history')}
                        className={cn(
                          'flex items-center gap-1.5 rounded-t-md px-3 py-1 text-xs transition-colors',
                          resultsTab === 'history'
                            ? 'z-10 -mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary'
                            : 'border-b border-b-border text-muted-foreground hover:bg-muted/50'
                        )}
                      >
                        <Clock className='size-3' />
                        History
                      </button>
                      <button
                        type='button'
                        onClick={() => setResultsTab('explain')}
                        className={cn(
                          'flex items-center gap-1.5 rounded-t-md px-3 py-1 text-xs transition-colors',
                          resultsTab === 'explain'
                            ? 'z-10 -mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary'
                            : 'border-b border-b-border text-muted-foreground hover:bg-muted/50'
                        )}
                      >
                        <Route className='size-3' />
                        Explain
                      </button>
                      <button
                        type='button'
                        onClick={() => setResultsTab('chart')}
                        className={cn(
                          'flex items-center gap-1.5 rounded-t-md px-3 py-1 text-xs transition-colors',
                          resultsTab === 'chart'
                            ? 'z-10 -mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary'
                            : 'border-b border-b-border text-muted-foreground hover:bg-muted/50'
                        )}
                      >
                        <BarChart3 className='size-3' />
                        Chart
                      </button>
                    </div>
                    {!resultsCollapsed && (
                      <div className='ml-auto mb-1 flex items-center gap-1'>
                        {resultsTab === 'results' && activeResult?.columns.length ? (
                          <Button
                            type='button'
                            size='sm'
                            variant='ghost'
                            className='h-6 gap-1.5 px-2 text-xs'
                            onClick={exportToExcel}
                          >
                            <Download className='size-3' />
                            Export
                          </Button>
                        ) : null}
                        {resultsTab === 'history' && (
                          <div className='flex items-center gap-1 text-xs'>
                            <button
                              type='button'
                              className={cn(
                                'rounded-md px-2.5 py-0.5 transition-colors',
                                historyFilter === 'file'
                                  ? 'bg-primary text-primary-foreground'
                                  : 'text-muted-foreground hover:bg-muted'
                              )}
                              onClick={() => setHistoryFilter('file')}
                            >
                              Current file
                            </button>
                            <button
                              type='button'
                              className={cn(
                                'rounded-md px-2.5 py-0.5 transition-colors',
                                historyFilter === 'all'
                                  ? 'bg-primary text-primary-foreground'
                                  : 'text-muted-foreground hover:bg-muted'
                              )}
                              onClick={() => setHistoryFilter('all')}
                            >
                              All files
                            </button>
                          </div>
                        )}
                        <Button
                          type='button'
                          size='icon'
                          variant='ghost'
                          className='size-6'
                          onMouseDown={startResultsResize}
                        >
                          <GripHorizontal className='size-3.5' />
                        </Button>
                      </div>
                    )}
                  </div>
                  {!resultsCollapsed && resultsTab === 'results' && (
                    <div className='flex min-h-0 flex-1 flex-col'>
                      {/* Multi-result sub-tabs */}
                      {queryResults && queryResults.length > 1 && (
                        <div className='flex items-center gap-0.5 border-b border-border px-2 py-1'>
                          {queryResults.map((_, idx) => (
                            <button
                              key={idx}
                              type='button'
                              onClick={() => setActiveResultIdx(idx)}
                              className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                                activeResultIdx === idx
                                  ? 'bg-primary/10 text-primary'
                                  : 'text-muted-foreground hover:bg-muted/50'
                              }`}
                            >
                              {!queryResults[idx].success ? '⚠ ' : ''}Result {idx + 1}
                            </button>
                          ))}
                        </div>
                      )}
                      <div className='min-h-0 flex-1 overflow-auto'>
                        <QueryResults queryResult={activeResult} />
                      </div>
                    </div>
                  )}
                  {!resultsCollapsed && resultsTab === 'history' && (
                    <QueryHistory
                      items={historyQuery.data?.items ?? []}
                      loading={historyQuery.isLoading}
                      onLoadSql={(sql) => {
                        if (!activeTab) return
                        editorContentRef.current = sql
                        setTabs((prev) => ({
                          ...prev,
                          [activeTab.id]: {
                            ...prev[activeTab.id],
                            content: sql,
                          },
                        }))
                      }}
                      onReRun={(sql) => {
                        if (!activeTab) return
                        editorContentRef.current = sql
                        setTabs((prev) => ({
                          ...prev,
                          [activeTab.id]: {
                            ...prev[activeTab.id],
                            content: sql,
                          },
                        }))
                        void runQuery(false, sql)
                      }}
                    />
                  )}
                  {!resultsCollapsed && resultsTab === 'explain' && (
                    <div className='h-full overflow-auto p-3'>
                      {explainResult === null && !explaining ? (
                        <div className='text-sm text-muted-foreground'>
                          Click <strong>Explain</strong> in the toolbar to view the query execution plan.
                        </div>
                      ) : explaining ? (
                        <div className='text-sm text-muted-foreground'>Generating execution plan…</div>
                      ) : (
                        <ExplainTreeView planText={explainResult!} />
                      )}
                    </div>
                  )}
                  {!resultsCollapsed && resultsTab === 'chart' && <ChartVisualization queryResult={activeResult} />}
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
    </div>
  )
}

function WorkspaceTree({
  entries,
  activeEntryId,
  expandedPaths,
  onTogglePath,
  onOpen,
  onRename,
  onDelete,
}: {
  entries: WorkspaceEntry[]
  activeEntryId: string | null
  expandedPaths: Record<string, boolean>
  onTogglePath: (path: string) => void
  onOpen: (entry: WorkspaceEntry) => void
  onRename: (entry: WorkspaceEntry) => void
  onDelete: (entry: WorkspaceEntry) => void
}) {
  return (
    <div className='px-2 py-3'>
      <SidebarMenu>
        {renderWorkspaceEntries('', entries, activeEntryId, expandedPaths, onTogglePath, onOpen, onRename, onDelete)}
      </SidebarMenu>
    </div>
  )
}

function renderWorkspaceEntries(
  parentPath: string,
  entries: WorkspaceEntry[],
  activeEntryId: string | null,
  expandedPaths: Record<string, boolean>,
  onTogglePath: (path: string) => void,
  onOpen: (entry: WorkspaceEntry) => void,
  onRename: (entry: WorkspaceEntry) => void,
  onDelete: (entry: WorkspaceEntry) => void
): React.ReactNode {
  return entries
    .filter((entry) => entry.parent_path === parentPath)
    .sort((a, b) => (a.entry_type === b.entry_type ? a.name.localeCompare(b.name) : a.entry_type === 'folder' ? -1 : 1))
    .map((entry) => {
      const isFolder = entry.entry_type === 'folder'
      const isOpen = expandedPaths[entry.path] ?? false

      if (isFolder) {
        return (
          <Collapsible key={entry.id} open={isOpen} onOpenChange={() => onTogglePath(entry.path)}>
            <SidebarMenuItem>
              <CollapsibleTrigger asChild>
                <button
                  type='button'
                  className='flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted'
                >
                  <ChevronRight
                    className={cn('size-4 shrink-0 transition-transform duration-200', isOpen && 'rotate-90')}
                  />
                  <Folder className={cn('size-4 shrink-0 text-primary', isOpen && 'text-primary/80')} />
                  <span className='truncate'>{entry.name}</span>
                </button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <SidebarMenuSub>
                  {renderWorkspaceEntries(
                    entry.path,
                    entries,
                    activeEntryId,
                    expandedPaths,
                    onTogglePath,
                    onOpen,
                    onRename,
                    onDelete
                  )}
                </SidebarMenuSub>
              </CollapsibleContent>
            </SidebarMenuItem>
          </Collapsible>
        )
      }

      return (
        <SidebarMenuItem key={entry.id}>
          <button
            type='button'
            className={cn(
              'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted',
              entry.id === activeEntryId && 'bg-primary/10 font-medium text-primary'
            )}
            onClick={() => onOpen(entry)}
          >
            <FileCode
              className={cn('size-4 shrink-0', entry.id === activeEntryId ? 'text-primary' : 'text-muted-foreground')}
            />
            <span className='truncate'>{entry.name}</span>
          </button>
        </SidebarMenuItem>
      )
    })
}

function TabMenuButton({
  onRename,
  onDownload,
  onClose,
}: {
  onRename: () => void
  onDownload: () => void
  onClose: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={ref} className='relative'>
      <button
        type='button'
        className='rounded-sm p-0.5 hover:bg-muted-foreground/20'
        onClick={(e) => {
          e.stopPropagation()
          setOpen(!open)
        }}
      >
        <MoreHorizontal className='size-3.5' />
      </button>
      {open && (
        <div className='absolute right-0 top-full z-50 mt-1 w-36 rounded-md border bg-popover py-1 shadow-md'>
          <button
            type='button'
            className='flex w-full items-center gap-2 px-3 py-1.5 text-sm hover:bg-muted'
            onClick={(e) => {
              e.stopPropagation()
              onRename()
              setOpen(false)
            }}
          >
            <Pencil className='size-3.5' /> Rename
          </button>
          <button
            type='button'
            className='flex w-full items-center gap-2 px-3 py-1.5 text-sm hover:bg-muted'
            onClick={(e) => {
              e.stopPropagation()
              onDownload()
              setOpen(false)
            }}
          >
            <Download className='size-3.5' /> Download SQL
          </button>
          <div className='my-1 border-t' />
          <button
            type='button'
            className='flex w-full items-center gap-2 px-3 py-1.5 text-sm text-destructive hover:bg-muted'
            onClick={(e) => {
              e.stopPropagation()
              onClose()
              setOpen(false)
            }}
          >
            <X className='size-3.5' /> Close
          </button>
        </div>
      )}
    </div>
  )
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
  setSchemasByDatabase: Dispatch<SetStateAction<Record<string, Array<{ name: string }>>>>
}) {
  return (
    <div className='px-2 py-3'>
      <SidebarMenu>
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
      </SidebarMenu>
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
  setSchemasByDatabase: Dispatch<SetStateAction<Record<string, Array<{ name: string }>>>>
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
    <Collapsible open={expanded} onOpenChange={() => onToggleDatabase(database)}>
      <SidebarMenuItem>
        <CollapsibleTrigger asChild>
          <button
            type='button'
            className='flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted'
          >
            <ChevronRight
              className={cn('size-4 shrink-0 transition-transform duration-200', expanded && 'rotate-90')}
            />
            <Database className='size-4 shrink-0 text-primary' />
            <span className='truncate font-medium'>{database}</span>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <SidebarMenuSub>
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
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
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
    <Collapsible open={expanded} onOpenChange={() => onToggleSchema(schemaKey)}>
      <SidebarMenuItem>
        <CollapsibleTrigger asChild>
          <button
            type='button'
            className='flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted'
          >
            <ChevronRight
              className={cn('size-4 shrink-0 transition-transform duration-200', expanded && 'rotate-90')}
            />
            <Folder className='size-4 shrink-0 text-amber-500' />
            <span className='truncate'>{schema}</span>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          {treeQuery.data && (
            <SidebarMenuSub className='space-y-2 py-1'>
              <ObjectGroup title='Tables' items={treeQuery.data.tables} database={database} schema={schema} />
              <ObjectGroup title='Views' items={treeQuery.data.views} database={database} schema={schema} />
              <ObjectGroup
                title='Materialized Views'
                items={treeQuery.data.materialized_views}
                database={database}
                schema={schema}
              />
              <ObjectGroup title='Stages' items={treeQuery.data.stages} database={database} schema={schema} />
            </SidebarMenuSub>
          )}
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  )
}

function ObjectGroup({
  title,
  items,
  database,
  schema,
}: {
  title: string
  items: Array<{ name: string }>
  database: string
  schema: string
}) {
  if (!items.length) return null
  const isTables = title === 'Tables'
  return (
    <div>
      <div className='px-2 py-0.5 text-xs font-medium tracking-wide text-muted-foreground uppercase'>{title}</div>
      <div className='space-y-0.5'>
        {items.map((item) => (
          <SidebarMenuSubItem key={item.name}>
            {isTables ? (
              <TableItemWithPopover name={item.name} database={database} schema={schema} />
            ) : (
              <button
                type='button'
                className='flex w-full items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted'
              >
                <span className='size-3.5 shrink-0 rounded-sm bg-muted-foreground/20' />
                <span className='truncate'>{item.name}</span>
              </button>
            )}
          </SidebarMenuSubItem>
        ))}
      </div>
    </div>
  )
}

type ColumnInfo = {
  name: string
  type: string
  null: string
  key: string
  default: string | null
  extra: string
}

function TableItemWithPopover({ name, database, schema }: { name: string; database: string; schema: string }) {
  const [hovered, setHovered] = useState(false)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const columnsQuery = useQuery<{ columns: ColumnInfo[]; count: number }>({
    queryKey: ['table-columns', database, schema, name],
    queryFn: () =>
      api.get<{ columns: ColumnInfo[]; count: number }>(
        `/objects/databases/${encodeURIComponent(database)}/tables/${encodeURIComponent(name)}/columns`
      ),
    enabled: hovered,
    staleTime: 60_000,
  })

  function typeIcon(type: string) {
    const t = type.toUpperCase()
    if (/INT|BIGINT|SMALLINT|TINYINT|FLOAT|DOUBLE|DECIMAL|NUMERIC|NUMBER/.test(t))
      return <Hash className='size-3 shrink-0 text-blue-500' />
    if (/DATE|TIME|TIMESTAMP/.test(t)) return <Clock className='size-3 shrink-0 text-emerald-500' />
    if (/BOOL/.test(t))
      return (
        <span className='flex size-3 shrink-0 items-center justify-center text-[9px] font-bold text-orange-500'>B</span>
      )
    return <Type className='size-3 shrink-0 text-violet-500' />
  }

  return (
    <div
      onMouseEnter={() => {
        setHovered(true)
        if (btnRef.current) setRect(btnRef.current.getBoundingClientRect())
      }}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        ref={btnRef}
        type='button'
        className='flex w-full items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted'
      >
        <Table2 className='size-3.5 shrink-0 text-primary' />
        <span className='truncate'>{name}</span>
      </button>
      {hovered &&
        rect &&
        createPortal(
          <div
            className='fixed z-[9999] w-64 rounded-md border bg-popover p-0 shadow-lg'
            style={{ left: rect.right + 8, top: rect.top }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
          >
            <div className='border-b px-3 py-1.5 text-xs font-medium text-muted-foreground'>
              <FileCode className='mr-1.5 inline size-3' />
              {name}
              {columnsQuery.data && (
                <span className='ml-1 text-muted-foreground/60'>• {columnsQuery.data.count} columns</span>
              )}
            </div>
            <div className='max-h-[240px] overflow-auto py-1'>
              {columnsQuery.isLoading && (
                <div className='px-3 py-2 text-xs text-muted-foreground'>Loading columns…</div>
              )}
              {columnsQuery.data?.columns.map((col) => (
                <div key={col.name} className='flex items-center gap-2 px-3 py-1 text-xs'>
                  {typeIcon(col.type)}
                  <span className='flex-1 truncate font-medium'>{col.name}</span>
                  <span className='shrink-0 text-muted-foreground'>{col.type}</span>
                </div>
              ))}
              {columnsQuery.isError && <div className='px-3 py-2 text-xs text-destructive'>Failed to load columns</div>}
            </div>
          </div>,
          document.body
        )}
    </div>
  )
}

function QueryResults({ queryResult }: { queryResult: QueryResponse | null }) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(100)
  const [sortCol, setSortCol] = useState<number | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [ctxMenu, setCtxMenu] = useState<{
    x: number
    y: number
    rowIdx: number
    colIdx: number
  } | null>(null)

  // Close context menu on click outside
  useEffect(() => {
    if (!ctxMenu) return
    const close = () => setCtxMenu(null)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [ctxMenu])

  function copyToClipboard(text: string) {
    navigator.clipboard.writeText(text)
    toast.success('Copied to clipboard')
  }

  function handleCellContext(e: React.MouseEvent, rowIdx: number, colIdx: number) {
    e.preventDefault()
    e.stopPropagation()
    setCtxMenu({ x: e.clientX, y: e.clientY, rowIdx, colIdx })
  }

  // Reset pagination on new results
  const prevRowCount = useRef(0)
  useEffect(() => {
    if (queryResult && queryResult.row_count !== prevRowCount.current) {
      setPage(1)
      setSortCol(null)
      prevRowCount.current = queryResult.row_count
    }
  }, [queryResult])

  if (!queryResult) {
    return <div className='p-4 text-sm text-muted-foreground'>Run a query to inspect table output here.</div>
  }

  if (queryResult.warnings?.length && !queryResult.columns.length) {
    return (
      <div className='p-4 text-sm text-destructive'>
        {queryResult.warnings.map((w, i) => (
          <div key={i}>{w}</div>
        ))}
      </div>
    )
  }

  if (!queryResult.columns.length) {
    return <div className='p-4 text-sm'>Query completed. Affected rows: {queryResult.affected_rows}</div>
  }

  // Sort rows
  const sortedRows =
    sortCol !== null
      ? [...queryResult.rows].sort((a, b) => {
          const av = a[sortCol],
            bv = b[sortCol]
          if (av === null && bv === null) return 0
          if (av === null) return 1
          if (bv === null) return -1
          const cmp = av < bv ? -1 : av > bv ? 1 : 0
          return sortDir === 'asc' ? cmp : -cmp
        })
      : queryResult.rows

  // Paginate
  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize))
  const startIdx = (page - 1) * pageSize
  const pageRows = sortedRows.slice(startIdx, startIdx + pageSize)

  // Warnings indicator (shown alongside data, not replacing it)
  const warningsBanner = queryResult.warnings?.length ? (
    <div className='border-b border-border bg-amber-500/10 px-4 py-2 text-xs text-amber-600 dark:text-amber-400'>
      {queryResult.warnings.map((w, i) => (
        <div key={i}>{w}</div>
      ))}
    </div>
  ) : null

  function handleSort(colIdx: number) {
    if (sortCol === colIdx) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortCol(colIdx)
      setSortDir('asc')
    }
    setPage(1)
  }

  return (
    <div className='flex h-full flex-col'>
      {warningsBanner}
      <div className='min-h-0 flex-1 overflow-auto'>
        <table className='w-full border-separate border-spacing-0 text-sm'>
          <thead className='sticky top-0 z-10 bg-background'>
            <tr>
              <th className='w-10 border-b border-r border-border px-1 py-1.5 text-center text-xs text-muted-foreground font-normal'>
                #
              </th>
              {queryResult.columns.map((column, colIndex) => {
                const sample = queryResult.rows[0]?.[colIndex]
                const typeIcon =
                  typeof sample === 'number'
                    ? '#'
                    : typeof sample === 'boolean'
                      ? '⊙'
                      : typeof sample === 'string'
                        ? String(sample).match(/^\d{4}-\d{2}-\d{2}/)
                          ? '◷'
                          : String(sample).match(/^[\d.,]+$/)
                            ? '#'
                            : 'A'
                        : sample === null
                          ? '∅'
                          : '?'
                const isSorted = sortCol === colIndex
                return (
                  <th
                    key={column}
                    className='cursor-pointer select-none border-b border-r border-border px-2 py-1.5 text-left font-normal whitespace-nowrap hover:bg-muted/50'
                    onClick={() => handleSort(colIndex)}
                  >
                    <span className='mr-1 text-muted-foreground'>{typeIcon}</span>
                    {column}
                    {isSorted && <span className='ml-1 text-primary'>{sortDir === 'asc' ? '↑' : '↓'}</span>}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, rowIndex) => (
              <tr key={rowIndex} className='hover:bg-muted/30'>
                <td className='border-b border-r border-border px-1 py-1 text-center text-xs text-muted-foreground'>
                  {startIdx + rowIndex + 1}
                </td>
                {row.map((cell, cellIndex) => (
                  <td
                    key={`${rowIndex}-${cellIndex}`}
                    className='border-b border-r border-border px-2 py-1 whitespace-nowrap'
                    onContextMenu={(e) => handleCellContext(e, startIdx + rowIndex, cellIndex)}
                  >
                    {cell === null ? <span className='text-muted-foreground italic'>NULL</span> : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Pagination footer */}
      <div className='flex items-center justify-between border-t border-border px-3 py-1.5 text-xs text-muted-foreground'>
        <span>
          Rows {startIdx + 1}–{Math.min(startIdx + pageSize, sortedRows.length)} of {sortedRows.length}
        </span>
        <div className='flex items-center gap-2'>
          <button
            type='button'
            className='rounded px-1.5 py-0.5 hover:bg-muted disabled:opacity-40'
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            ◀ Prev
          </button>
          <span>
            Page {page} of {totalPages}
          </span>
          <button
            type='button'
            className='rounded px-1.5 py-0.5 hover:bg-muted disabled:opacity-40'
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next ▶
          </button>
          <select
            className='rounded border border-border bg-background px-1.5 py-0.5 text-xs'
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value))
              setPage(1)
            }}
          >
            <option value={25}>25 / page</option>
            <option value={50}>50 / page</option>
            <option value={100}>100 / page</option>
            <option value={250}>250 / page</option>
          </select>
        </div>
      </div>
      {/* Right-click context menu */}
      {ctxMenu &&
        queryResult &&
        (() => {
          const { rowIdx, colIdx, x, y } = ctxMenu
          const allRows = sortedRows
          const cellVal = allRows[rowIdx]?.[colIdx]
          const row = allRows[rowIdx]
          const col = allRows.map((r) => r[colIdx])
          const colName = queryResult.columns[colIdx]
          return (
            <div
              className='fixed z-[9999] min-w-[180px] rounded-md border border-border bg-popover p-1 shadow-lg'
              style={{ left: x, top: y }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className='mb-1 truncate border-b border-border px-2 py-1 text-[11px] text-muted-foreground'>
                {colName} — Row {rowIdx + 1}
              </div>
              <button
                type='button'
                className='flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent'
                onClick={() => {
                  copyToClipboard(cellVal === null ? 'NULL' : String(cellVal))
                  setCtxMenu(null)
                }}
              >
                <Copy className='size-3.5' /> Copy cell value
              </button>
              <button
                type='button'
                className='flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent'
                onClick={() => {
                  copyToClipboard(row.map((c) => (c === null ? 'NULL' : String(c))).join('\t'))
                  setCtxMenu(null)
                }}
              >
                <Copy className='size-3.5' /> Copy row (TSV)
              </button>
              <button
                type='button'
                className='flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent'
                onClick={() => {
                  copyToClipboard(col.map((c) => (c === null ? 'NULL' : String(c))).join('\n'))
                  setCtxMenu(null)
                }}
              >
                <Copy className='size-3.5' /> Copy column ({colName})
              </button>
              <div className='my-1 border-t border-border' />
              <button
                type='button'
                className='flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent'
                onClick={() => {
                  const header = queryResult.columns.join('\t')
                  const body = allRows.map((r) => r.map((c) => (c === null ? '' : String(c))).join('\t'))
                  copyToClipboard([header, ...body].join('\n'))
                  setCtxMenu(null)
                }}
              >
                <Copy className='size-3.5' /> Copy all (CSV)
              </button>
            </div>
          )
        })()}
    </div>
  )
}

// ── Chart Visualization ─────────────────────────────────────────────
const CHART_COLORS = ['#5b8def', '#2dd4bf', '#f59e0b', '#f97316', '#a78bfa', '#f472b6', '#38bdf8', '#34d399']
const MONOCHROME_CHART_COLORS = ['#5b8def', '#76a0ef', '#90b4f2', '#aac7f5', '#c4daf8', '#deedfb']
const CHART_EMPTY_OPTION = '__none__'
const CHART_TYPE_OPTIONS = [
  { value: 'bar', label: 'Bar chart' },
  { value: 'stacked-bar', label: 'Stacked bar chart' },
  { value: 'line', label: 'Line chart' },
  { value: 'area', label: 'Area chart' },
  { value: 'pie', label: 'Pie chart' },
] as const
const AGGREGATE_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'count', label: 'Count' },
  { value: 'min', label: 'Min' },
  { value: 'max', label: 'Max' },
  { value: 'sum', label: 'Sum' },
  { value: 'median', label: 'Median' },
  { value: 'average', label: 'Average' },
] as const
const SORT_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'x-asc', label: 'X ascending' },
  { value: 'x-desc', label: 'X descending' },
  { value: 'y-desc', label: 'Y descending' },
  { value: 'y-asc', label: 'Y ascending' },
] as const
const COLOR_OPTIONS = [
  { value: 'palette', label: 'Palette' },
  { value: 'single', label: 'Single tone' },
] as const

type ColumnType = 'number' | 'date' | 'text' | 'boolean' | 'null'
type ChartType = (typeof CHART_TYPE_OPTIONS)[number]['value']
type AggregateType = (typeof AGGREGATE_OPTIONS)[number]['value']
type SortMode = (typeof SORT_OPTIONS)[number]['value']
type ColorMode = (typeof COLOR_OPTIONS)[number]['value']
type ChartRecord = Record<string, string | number | boolean | null>
type ChartSeries = {
  key: string
  label: string
  color: string
}

function inferColumnType(rows: QueryResponse['rows'], colIndex: number): ColumnType {
  for (let i = 0; i < Math.min(rows.length, 20); i++) {
    const val = rows[i]?.[colIndex]
    if (val === null || val === undefined) continue
    if (typeof val === 'number') return 'number'
    if (typeof val === 'boolean') return 'boolean'
    if (typeof val === 'string') {
      if (/^\d{4}-\d{2}-\d{2}/.test(val)) return 'date'
      if (/^[\d.,]+$/.test(val)) return 'number'
      return 'text'
    }
  }
  return 'null'
}

function parseNumericValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const normalized = value.replace(/,/g, '').trim()
    if (!normalized) return null
    const parsed = Number(normalized)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function formatChartLabel(value: unknown) {
  if (value === null || value === undefined || value === '') return 'Unknown'
  return String(value)
}

function summarizeValues(values: number[], aggregate: AggregateType) {
  if (values.length === 0) return 0

  switch (aggregate) {
    case 'count':
      return values.length
    case 'min':
      return Math.min(...values)
    case 'max':
      return Math.max(...values)
    case 'average':
      return values.reduce((sum, value) => sum + value, 0) / values.length
    case 'median': {
      const sorted = [...values].sort((a, b) => a - b)
      const midpoint = Math.floor(sorted.length / 2)
      return sorted.length % 2 === 0 ? (sorted[midpoint - 1] + sorted[midpoint]) / 2 : sorted[midpoint]
    }
    case 'sum':
    default:
      return values.reduce((sum, value) => sum + value, 0)
  }
}

function formatNumberCompact(value: number) {
  return new Intl.NumberFormat('en-US', {
    notation: Math.abs(value) >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: 2,
  }).format(value)
}

function getSeriesColors(colorMode: ColorMode, count: number) {
  const palette = colorMode === 'single' ? MONOCHROME_CHART_COLORS : CHART_COLORS

  return Array.from({ length: count }, (_, index) => palette[index % palette.length])
}

function compareChartValues(
  left: string | number | boolean | null | undefined,
  right: string | number | boolean | null | undefined
) {
  const leftNumber = parseNumericValue(left)
  const rightNumber = parseNumericValue(right)

  if (leftNumber != null && rightNumber != null) return leftNumber - rightNumber

  return formatChartLabel(left).localeCompare(formatChartLabel(right))
}

function formatAxisTick(value: unknown, maxLength: number = 18) {
  const label = formatChartLabel(value)
  return label.length > maxLength ? `${label.slice(0, maxLength)}...` : label
}

function ChartVisualization({ queryResult }: { queryResult: QueryResponse | null }) {
  const { resolvedTheme } = useTheme()
  const [builderCollapsed, setBuilderCollapsed] = useState(false)
  const [chartType, setChartType] = useState<ChartType>('bar')
  const [xCol, setXCol] = useState('')
  const [yCols, setYCols] = useState<string[]>([])
  const [aggregate, setAggregate] = useState<AggregateType>('none')
  const [groupBy, setGroupBy] = useState('')
  const [sortMode, setSortMode] = useState<SortMode>('none')
  const [colorMode, setColorMode] = useState<ColorMode>('palette')
  const [showXAxisLabel, setShowXAxisLabel] = useState(true)
  const [showYAxisLabel, setShowYAxisLabel] = useState(true)
  const [xAxisLabel, setXAxisLabel] = useState('')
  const [yAxisLabel, setYAxisLabel] = useState('')

  const columnTypes = useMemo(() => {
    if (!queryResult?.columns.length) return []
    return queryResult.columns.map((_, i) => inferColumnType(queryResult.rows, i))
  }, [queryResult])

  const rawRecords = useMemo<ChartRecord[]>(() => {
    if (!queryResult) return []

    return queryResult.rows.map((row) => {
      const record: ChartRecord = {}
      queryResult.columns.forEach((column, index) => {
        const rawValue = row[index]
        const columnType = columnTypes[index]

        if (columnType === 'number') {
          record[column] = parseNumericValue(rawValue)
        } else {
          record[column] = rawValue
        }
      })
      return record
    })
  }, [columnTypes, queryResult])

  const xCandidates = useMemo(
    () => queryResult?.columns.filter((_, index) => columnTypes[index] !== 'null') ?? [],
    [columnTypes, queryResult]
  )

  const numericCandidates = useMemo(
    () => queryResult?.columns.filter((_, index) => columnTypes[index] === 'number') ?? [],
    [columnTypes, queryResult]
  )

  // Auto-detect best chart config on new results
  useEffect(() => {
    if (!queryResult?.columns.length) return

    const textColumns = queryResult.columns.filter((_, index) => columnTypes[index] === 'text')
    const dateColumns = queryResult.columns.filter((_, index) => columnTypes[index] === 'date')
    const numberColumns = queryResult.columns.filter((_, index) => columnTypes[index] === 'number')

    const nextXColumn =
      dateColumns[0] ??
      textColumns[0] ??
      queryResult.columns.find((column) => !numberColumns.includes(column)) ??
      queryResult.columns[0] ??
      ''

    const nextYColumns =
      numberColumns.length > 0
        ? numberColumns.slice(0, dateColumns.length > 0 ? 2 : 3)
        : queryResult.columns.slice(1, 2)

    if (dateColumns.length >= 1 && numberColumns.length >= 1) {
      setChartType('line')
    } else if (numberColumns.length >= 1) {
      setChartType('bar')
    } else {
      setChartType('pie')
    }

    setXCol(nextXColumn)
    setYCols(nextYColumns)
    setAggregate('none')
    setGroupBy('')
    setSortMode('none')
    setColorMode('palette')
    setShowXAxisLabel(true)
    setShowYAxisLabel(true)
    setXAxisLabel(nextXColumn)
    setYAxisLabel(nextYColumns[0] ?? '')
  }, [columnTypes, queryResult])

  useEffect(() => {
    if (chartType === 'pie' && yCols.length > 1) {
      setYCols((current) => current.slice(0, 1))
    }
  }, [chartType, yCols])

  useEffect(() => {
    if (groupBy && groupBy === xCol) {
      setGroupBy('')
    }
  }, [groupBy, xCol])

  const effectiveYColumns = useMemo(() => {
    if (numericCandidates.length === 0) return []

    const normalized = yCols.filter(
      (column, index) => column && numericCandidates.includes(column) && yCols.indexOf(column) === index
    )

    return chartType === 'pie' ? normalized.slice(0, 1) : normalized
  }, [chartType, numericCandidates, yCols])

  const activeAggregate = useMemo<AggregateType>(() => {
    if (aggregate !== 'none') return aggregate
    if (chartType === 'pie') return 'sum'
    if (groupBy) return 'sum'
    return 'none'
  }, [aggregate, chartType, groupBy])

  const chartModel = useMemo(() => {
    if (!queryResult || !xCol) {
      return {
        data: [] as Array<Record<string, string | number>>,
        series: [] as ChartSeries[],
        emptyReason: 'Select an X-axis and at least one numeric metric to build the chart.',
      }
    }

    if (numericCandidates.length === 0) {
      return {
        data: [] as Array<Record<string, string | number>>,
        series: [] as ChartSeries[],
        emptyReason: 'This result set has no numeric columns that can be visualized.',
      }
    }

    if (effectiveYColumns.length === 0 && activeAggregate !== 'count') {
      return {
        data: [] as Array<Record<string, string | number>>,
        series: [] as ChartSeries[],
        emptyReason: 'Pick at least one numeric Y-axis column to render the chart.',
      }
    }

    const sortData = (rows: Array<Record<string, string | number>>) => {
      if (sortMode === 'none') return rows

      const primarySeriesKey = Object.keys(rows[0] ?? {}).filter((key) => key !== xCol)

      return [...rows].sort((left, right) => {
        if (sortMode === 'x-asc') return compareChartValues(left[xCol], right[xCol])
        if (sortMode === 'x-desc') return compareChartValues(right[xCol], left[xCol])

        const leftTotal = primarySeriesKey.reduce((sum, key) => sum + (parseNumericValue(left[key]) ?? 0), 0)
        const rightTotal = primarySeriesKey.reduce((sum, key) => sum + (parseNumericValue(right[key]) ?? 0), 0)

        return sortMode === 'y-asc' ? leftTotal - rightTotal : rightTotal - leftTotal
      })
    }

    if (groupBy) {
      const primaryMetric = effectiveYColumns[0]
      if (!primaryMetric) {
        return {
          data: [] as Array<Record<string, string | number>>,
          series: [] as ChartSeries[],
          emptyReason: 'Grouped charts need one numeric Y-axis metric.',
        }
      }

      const groupValues = Array.from(new Set(rawRecords.map((record) => formatChartLabel(record[groupBy])))).slice(0, 8)

      const bucketMap = new Map<string, Record<string, number[]>>()
      rawRecords.forEach((record) => {
        const xValue = formatChartLabel(record[xCol])
        const groupValue = formatChartLabel(record[groupBy])
        if (!groupValues.includes(groupValue)) return

        const metricValue = parseNumericValue(record[primaryMetric])
        if (metricValue == null && activeAggregate !== 'count') return

        const bucketKey = `${xValue}:::${groupValue}`
        const existing = bucketMap.get(bucketKey) ?? {}
        existing[groupValue] = [...(existing[groupValue] ?? []), metricValue ?? 0]
        bucketMap.set(bucketKey, existing)
      })

      const xValues = Array.from(new Set(rawRecords.map((record) => formatChartLabel(record[xCol]))))

      const seriesColors = getSeriesColors(colorMode, groupValues.length)
      const data = sortData(
        xValues.map((xValue) => {
          const row: Record<string, string | number> = { [xCol]: xValue }

          groupValues.forEach((groupValue) => {
            const bucket = bucketMap.get(`${xValue}:::${groupValue}`)
            const values = bucket?.[groupValue] ?? []
            row[groupValue] = activeAggregate === 'count' ? values.length : summarizeValues(values, activeAggregate)
          })

          return row
        })
      )

      return {
        data,
        series: groupValues.map((groupValue, index) => ({
          key: groupValue,
          label: groupValue,
          color: seriesColors[index],
        })),
        emptyReason: '',
      }
    }

    if (activeAggregate !== 'none' || chartType === 'pie') {
      const groupedRows = new Map<
        string,
        {
          xLabel: string
          metrics: Record<string, number[]>
        }
      >()

      rawRecords.forEach((record) => {
        const xValue = formatChartLabel(record[xCol])
        const bucket = groupedRows.get(xValue) ?? {
          xLabel: xValue,
          metrics: {},
        }

        if (activeAggregate === 'count') {
          bucket.metrics.__count__ = [...(bucket.metrics.__count__ ?? []), 1]
        } else {
          effectiveYColumns.forEach((column) => {
            const numericValue = parseNumericValue(record[column])
            if (numericValue == null) return
            bucket.metrics[column] = [...(bucket.metrics[column] ?? []), numericValue]
          })
        }

        groupedRows.set(xValue, bucket)
      })

      const seriesKeys = activeAggregate === 'count' ? ['__count__'] : effectiveYColumns
      const seriesColors = getSeriesColors(colorMode, seriesKeys.length)
      const data = sortData(
        Array.from(groupedRows.values()).map((bucket) => {
          const row: Record<string, string | number> = { [xCol]: bucket.xLabel }

          seriesKeys.forEach((key) => {
            const values = bucket.metrics[key] ?? []
            row[key] = activeAggregate === 'count' ? values.length : summarizeValues(values, activeAggregate)
          })

          return row
        })
      )

      return {
        data,
        series: seriesKeys.map((key, index) => ({
          key,
          label: key === '__count__' ? 'Count' : key,
          color: seriesColors[index],
        })),
        emptyReason: '',
      }
    }

    const seriesColors = getSeriesColors(colorMode, effectiveYColumns.length)
    const data = sortData(
      rawRecords.map((record) => {
        const row: Record<string, string | number> = {
          [xCol]: formatChartLabel(record[xCol]),
        }

        effectiveYColumns.forEach((column) => {
          row[column] = parseNumericValue(record[column]) ?? 0
        })

        return row
      })
    )

    return {
      data,
      series: effectiveYColumns.map((column, index) => ({
        key: column,
        label: column,
        color: seriesColors[index],
      })),
      emptyReason: '',
    }
  }, [
    activeAggregate,
    colorMode,
    effectiveYColumns,
    groupBy,
    numericCandidates,
    queryResult,
    rawRecords,
    sortMode,
    xCol,
    chartType,
  ])

  if (!queryResult?.columns.length || !queryResult.rows.length) {
    return (
      <div className='flex h-full items-center justify-center p-4 text-sm text-muted-foreground'>
        Run a query with results to visualize as a chart.
      </div>
    )
  }

  const availableMetricOptions = numericCandidates
  const canAddMetric =
    chartType !== 'pie' &&
    !groupBy &&
    availableMetricOptions.some((column) => !effectiveYColumns.includes(column)) &&
    effectiveYColumns.length < 4
  const chartTitle = CHART_TYPE_OPTIONS.find((option) => option.value === chartType)?.label ?? 'Chart'
  const hasChartData = chartModel.data.length > 0 && chartModel.series.length > 0
  const isDarkMode = resolvedTheme === 'dark'
  const axisTickStyle = {
    fontSize: 11,
    fill: isDarkMode ? '#94a3b8' : 'hsl(var(--muted-foreground))',
  }
  const axisLineStyle = {
    stroke: isDarkMode ? 'rgba(148, 163, 184, 0.26)' : 'hsl(var(--border))',
  }
  const gridStroke = isDarkMode ? 'rgba(71, 85, 105, 0.38)' : 'hsl(var(--border))'
  const tooltipContentStyle = {
    backgroundColor: isDarkMode ? 'rgba(15, 23, 42, 0.96)' : 'hsl(var(--popover))',
    border: `1px solid ${isDarkMode ? 'rgba(71, 85, 105, 0.7)' : 'hsl(var(--border))'}`,
    borderRadius: 12,
    color: isDarkMode ? '#e2e8f0' : 'hsl(var(--foreground))',
    fontSize: 12,
    boxShadow: isDarkMode ? '0 18px 40px rgba(2, 6, 23, 0.38)' : '0 12px 28px rgba(15, 23, 42, 0.08)',
  }
  const tooltipLabelStyle = {
    color: isDarkMode ? '#f8fafc' : 'hsl(var(--foreground))',
    fontWeight: 600,
  }
  const tooltipItemStyle = {
    color: isDarkMode ? '#cbd5e1' : 'hsl(var(--foreground))',
  }
  const hoverCursorStyle = {
    fill: isDarkMode ? 'rgba(148, 163, 184, 0.12)' : 'rgba(15, 23, 42, 0.06)',
  }

  return (
    <div className='flex h-full min-h-0 flex-col lg:flex-row'>
      <div
        className={cn(
          'relative border-b border-border bg-background transition-[width] duration-200 lg:border-b-0 lg:border-r',
          builderCollapsed ? 'lg:w-14' : 'lg:w-[320px]'
        )}
      >
        <button
          type='button'
          aria-label={builderCollapsed ? 'Open chart builder' : 'Collapse chart builder'}
          className='absolute right-2 top-2 z-10 inline-flex size-8 items-center justify-center rounded-md border border-border bg-background/90 text-muted-foreground shadow-sm transition-colors hover:bg-muted hover:text-foreground'
          onClick={() => setBuilderCollapsed((current) => !current)}
        >
          <ChevronLeft className={cn('size-4 transition-transform', builderCollapsed && 'rotate-180')} />
        </button>

        {builderCollapsed ? (
          <div className='flex h-full min-h-0 flex-col items-center justify-start gap-3 px-2 py-12'>
            <div className='flex size-9 items-center justify-center rounded-lg border border-border bg-muted/20'>
              <BarChart3 className='size-4 text-primary' />
            </div>
            <div className='-rotate-180 text-[11px] font-medium tracking-[0.2em] text-muted-foreground [writing-mode:vertical-rl]'>
              CHART
            </div>
          </div>
        ) : (
          <ScrollArea className='h-full'>
            <div className='space-y-5 p-4'>
              <div className='space-y-1 pr-10'>
                <h3 className='text-sm font-semibold text-foreground'>Chart Builder</h3>
                <p className='text-xs text-muted-foreground'>
                  Built directly from the current query result set. No additional query is executed.
                </p>
              </div>

              <div className='space-y-4'>
                <div className='space-y-2'>
                  <label className='text-xs font-medium text-muted-foreground'>Chart type</label>
                  <Select value={chartType} onValueChange={(value) => setChartType(value as ChartType)}>
                    <SelectTrigger className='h-10 bg-muted/30'>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CHART_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className='border-t border-border pt-4'>
                  <div className='space-y-2'>
                    <label className='text-xs font-medium text-muted-foreground'>X-axis</label>
                    <Select value={xCol} onValueChange={setXCol}>
                      <SelectTrigger className='h-10 bg-muted/30'>
                        <div className='flex min-w-0 items-center gap-2'>
                          <Type className='size-3.5 text-muted-foreground' />
                          <SelectValue />
                        </div>
                      </SelectTrigger>
                      <SelectContent>
                        {xCandidates.map((column) => (
                          <SelectItem key={column} value={column}>
                            {column}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className='mt-4 space-y-2'>
                    <label className='text-xs font-medium text-muted-foreground'>Sort</label>
                    <Select value={sortMode} onValueChange={(value) => setSortMode(value as SortMode)}>
                      <SelectTrigger className='h-10 bg-muted/30'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {SORT_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className='border-t border-border pt-4'>
                  <div className='flex items-center justify-between'>
                    <label className='text-xs font-medium text-muted-foreground'>Y-axis</label>
                    {canAddMetric ? (
                      <Button
                        type='button'
                        variant='ghost'
                        size='sm'
                        className='h-7 px-2 text-xs'
                        onClick={() => {
                          const nextMetric = availableMetricOptions.find(
                            (column) => !effectiveYColumns.includes(column)
                          )
                          if (!nextMetric) return
                          setYCols((current) => [...current, nextMetric])
                        }}
                      >
                        <Plus className='mr-1 size-3.5' />
                        Add column
                      </Button>
                    ) : null}
                  </div>

                  <div className='mt-2 space-y-2'>
                    {effectiveYColumns.map((column, index) => (
                      <div key={`${column}-${index}`} className='flex items-center gap-2'>
                        <Select
                          value={column}
                          onValueChange={(value) =>
                            setYCols((current) => {
                              const next = [...current]
                              next[index] = value
                              return next
                            })
                          }
                        >
                          <SelectTrigger className='h-10 flex-1 bg-muted/30'>
                            <div className='flex min-w-0 items-center gap-2'>
                              <Hash className='size-3.5 text-muted-foreground' />
                              <SelectValue />
                            </div>
                          </SelectTrigger>
                          <SelectContent>
                            {availableMetricOptions.map((metric) => (
                              <SelectItem key={metric} value={metric}>
                                {metric}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        {effectiveYColumns.length > 1 ? (
                          <Button
                            type='button'
                            variant='ghost'
                            size='icon'
                            className='size-9 shrink-0'
                            onClick={() =>
                              setYCols((current) => current.filter((_, metricIndex) => metricIndex !== index))
                            }
                          >
                            <X className='size-3.5' />
                          </Button>
                        ) : null}
                      </div>
                    ))}
                  </div>

                  {groupBy ? (
                    <p className='mt-2 text-[11px] text-muted-foreground'>
                      Grouped charts use the first Y-axis metric as the measure for each split series.
                    </p>
                  ) : null}
                </div>

                <div className='space-y-4 border-t border-border pt-4'>
                  <div className='space-y-2'>
                    <label className='text-xs font-medium text-muted-foreground'>Aggregate</label>
                    <Select value={aggregate} onValueChange={(value) => setAggregate(value as AggregateType)}>
                      <SelectTrigger className='h-10 bg-muted/30'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {AGGREGATE_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className='space-y-2'>
                    <label className='text-xs font-medium text-muted-foreground'>Group by</label>
                    <Select
                      value={groupBy || CHART_EMPTY_OPTION}
                      onValueChange={(value) => setGroupBy(value === CHART_EMPTY_OPTION ? '' : value)}
                    >
                      <SelectTrigger className='h-10 bg-muted/30'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={CHART_EMPTY_OPTION}>None</SelectItem>
                        {xCandidates
                          .filter((column) => column !== xCol)
                          .map((column) => (
                            <SelectItem key={column} value={column}>
                              {column}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className='space-y-2'>
                    <label className='text-xs font-medium text-muted-foreground'>Color</label>
                    <Select value={colorMode} onValueChange={(value) => setColorMode(value as ColorMode)}>
                      <SelectTrigger className='h-10 bg-muted/30'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {COLOR_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className='space-y-4 border-t border-border pt-4'>
                  <div className='space-y-2'>
                    <div className='flex items-center justify-between'>
                      <label className='text-xs font-medium text-muted-foreground'>X-axis label</label>
                      <Switch checked={showXAxisLabel} onCheckedChange={setShowXAxisLabel} />
                    </div>
                    <Input
                      value={xAxisLabel}
                      onChange={(event) => setXAxisLabel(event.target.value)}
                      placeholder={xCol}
                      disabled={!showXAxisLabel}
                      className='h-10'
                    />
                  </div>

                  <div className='space-y-2'>
                    <div className='flex items-center justify-between'>
                      <label className='text-xs font-medium text-muted-foreground'>Y-axis label</label>
                      <Switch checked={showYAxisLabel} onCheckedChange={setShowYAxisLabel} />
                    </div>
                    <Input
                      value={yAxisLabel}
                      onChange={(event) => setYAxisLabel(event.target.value)}
                      placeholder={effectiveYColumns[0] ?? 'Value'}
                      disabled={!showYAxisLabel}
                      className='h-10'
                    />
                  </div>
                </div>
              </div>
            </div>
          </ScrollArea>
        )}
      </div>

      <div className='flex min-h-0 flex-1 flex-col bg-background'>
        <div className='flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4'>
          <div className='space-y-1'>
            <div className='flex items-center gap-2'>
              <BarChart3 className='size-4 text-primary' />
              <h3 className='text-sm font-semibold text-foreground'>{chartTitle}</h3>
            </div>
            <p className='text-xs text-muted-foreground'>
              {queryResult.row_count.toLocaleString()} rows in result • {chartModel.data.length.toLocaleString()}{' '}
              plotted groups
            </p>
          </div>

          {chartModel.series.length > 0 ? (
            <div className='flex flex-wrap items-center gap-2'>
              {chartModel.series.map((series) => (
                <div
                  key={series.key}
                  className='inline-flex items-center gap-2 rounded-full border border-border/80 bg-muted/25 px-2.5 py-1 text-[11px] text-muted-foreground'
                >
                  <span className='size-2 rounded-full' style={{ backgroundColor: series.color }} />
                  <span className='max-w-[140px] truncate'>{series.label}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        <div className='min-h-0 flex-1 p-5'>
          <div className='h-full rounded-xl border border-border/80 bg-muted/10 p-4'>
            {hasChartData ? (
              <ResponsiveContainer width='100%' height='100%'>
                {chartType === 'line' ? (
                  <LineChart data={chartModel.data}>
                    <CartesianGrid stroke={gridStroke} opacity={0.7} vertical={false} />
                    <XAxis
                      dataKey={xCol}
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatAxisTick(value)}
                      minTickGap={18}
                      label={
                        showXAxisLabel && xAxisLabel
                          ? {
                              value: xAxisLabel,
                              position: 'insideBottom',
                              offset: -6,
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <YAxis
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatNumberCompact(Number(value))}
                      width={68}
                      label={
                        showYAxisLabel && yAxisLabel
                          ? {
                              value: yAxisLabel,
                              angle: -90,
                              position: 'insideLeft',
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <Tooltip
                      formatter={(value) => formatNumberCompact(Number(value ?? 0))}
                      contentStyle={tooltipContentStyle}
                      labelStyle={tooltipLabelStyle}
                      itemStyle={tooltipItemStyle}
                      cursor={{
                        stroke: isDarkMode ? 'rgba(148, 163, 184, 0.24)' : 'rgba(15, 23, 42, 0.1)',
                        strokeWidth: 1,
                      }}
                    />
                    {chartModel.series.map((series) => (
                      <Line
                        key={series.key}
                        type='monotone'
                        dataKey={series.key}
                        stroke={series.color}
                        strokeWidth={2.5}
                        dot={{ r: 0 }}
                        activeDot={{
                          r: 4,
                          fill: series.color,
                          stroke: 'hsl(var(--background))',
                          strokeWidth: 2,
                        }}
                      />
                    ))}
                  </LineChart>
                ) : chartType === 'area' ? (
                  <AreaChart data={chartModel.data}>
                    <defs>
                      {chartModel.series.map((series) => (
                        <linearGradient key={series.key} id={`area-gradient-${series.key}`} x1='0' y1='0' x2='0' y2='1'>
                          <stop offset='0%' stopColor={series.color} stopOpacity={0.55} />
                          <stop offset='100%' stopColor={series.color} stopOpacity={0.04} />
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid stroke={gridStroke} opacity={0.7} vertical={false} />
                    <XAxis
                      dataKey={xCol}
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatAxisTick(value)}
                      minTickGap={18}
                      label={
                        showXAxisLabel && xAxisLabel
                          ? {
                              value: xAxisLabel,
                              position: 'insideBottom',
                              offset: -6,
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <YAxis
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatNumberCompact(Number(value))}
                      width={68}
                      label={
                        showYAxisLabel && yAxisLabel
                          ? {
                              value: yAxisLabel,
                              angle: -90,
                              position: 'insideLeft',
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <Tooltip
                      formatter={(value) => formatNumberCompact(Number(value ?? 0))}
                      contentStyle={tooltipContentStyle}
                      labelStyle={tooltipLabelStyle}
                      itemStyle={tooltipItemStyle}
                      cursor={{
                        stroke: isDarkMode ? 'rgba(148, 163, 184, 0.24)' : 'rgba(15, 23, 42, 0.1)',
                        strokeWidth: 1,
                      }}
                    />
                    {chartModel.series.map((series) => (
                      <Area
                        key={series.key}
                        type='monotone'
                        dataKey={series.key}
                        stroke={series.color}
                        strokeWidth={2.5}
                        fill={`url(#area-gradient-${series.key})`}
                        fillOpacity={1}
                      />
                    ))}
                  </AreaChart>
                ) : chartType === 'pie' ? (
                  <PieChart>
                    <Tooltip
                      formatter={(value) => formatNumberCompact(Number(value ?? 0))}
                      contentStyle={tooltipContentStyle}
                      labelStyle={tooltipLabelStyle}
                      itemStyle={tooltipItemStyle}
                    />
                    <Pie
                      data={chartModel.data}
                      dataKey={chartModel.series[0]?.key}
                      nameKey={xCol}
                      cx='50%'
                      cy='50%'
                      innerRadius='48%'
                      outerRadius='74%'
                      paddingAngle={2}
                      stroke='hsl(var(--background))'
                      strokeWidth={2}
                    >
                      {chartModel.data.map((_, index) => (
                        <Cell
                          key={`pie-cell-${index}`}
                          fill={
                            chartModel.series.length > 1
                              ? chartModel.series[index % chartModel.series.length]?.color
                              : getSeriesColors(colorMode, chartModel.data.length)[index]
                          }
                        />
                      ))}
                    </Pie>
                  </PieChart>
                ) : (
                  <BarChart data={chartModel.data} barGap={10}>
                    <defs>
                      {chartModel.series.map((series) => (
                        <linearGradient key={series.key} id={`bar-gradient-${series.key}`} x1='0' y1='0' x2='0' y2='1'>
                          <stop offset='0%' stopColor={series.color} stopOpacity={0.95} />
                          <stop offset='100%' stopColor={series.color} stopOpacity={0.75} />
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid stroke={gridStroke} opacity={0.7} vertical={false} />
                    <XAxis
                      dataKey={xCol}
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatAxisTick(value)}
                      minTickGap={16}
                      angle={chartModel.data.length > 8 ? -90 : 0}
                      textAnchor={chartModel.data.length > 8 ? 'end' : 'middle'}
                      height={chartModel.data.length > 8 ? 92 : 46}
                      label={
                        showXAxisLabel && xAxisLabel
                          ? {
                              value: xAxisLabel,
                              position: 'insideBottom',
                              offset: chartModel.data.length > 8 ? -2 : -6,
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <YAxis
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatNumberCompact(Number(value))}
                      width={68}
                      label={
                        showYAxisLabel && yAxisLabel
                          ? {
                              value: yAxisLabel,
                              angle: -90,
                              position: 'insideLeft',
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <Tooltip
                      formatter={(value) => formatNumberCompact(Number(value ?? 0))}
                      contentStyle={tooltipContentStyle}
                      labelStyle={tooltipLabelStyle}
                      itemStyle={tooltipItemStyle}
                      cursor={hoverCursorStyle}
                    />
                    {chartModel.series.map((series) => (
                      <Bar
                        key={series.key}
                        dataKey={series.key}
                        fill={`url(#bar-gradient-${series.key})`}
                        radius={[8, 8, 0, 0]}
                        stackId={chartType === 'stacked-bar' ? 'chart-stack' : undefined}
                        maxBarSize={chartType === 'stacked-bar' ? 44 : 56}
                      />
                    ))}
                  </BarChart>
                )}
              </ResponsiveContainer>
            ) : (
              <div className='flex h-full items-center justify-center text-sm text-muted-foreground'>
                {chartModel.emptyReason}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── EXPLAIN Tree View ───────────────────────────────────────────────
const OPERATOR_COLORS: Record<string, string> = {
  OlapScanNode: 'text-blue-500',
  OlapScan: 'text-blue-500',
  EXCHANGE: 'text-emerald-500',
  'HASH JOIN': 'text-orange-500',
  'NESTLOOP JOIN': 'text-orange-500',
  'MERGE JOIN': 'text-orange-500',
  JOIN: 'text-orange-500',
  AGGREGATE: 'text-violet-500',
  AGGREGATE_NODE: 'text-violet-500',
  SORT: 'text-amber-500',
  'TOP-N': 'text-amber-500',
  ANALYTIC: 'text-pink-500',
  'RESULT SINK': 'text-muted-foreground',
  'STREAM DATA SINK': 'text-muted-foreground',
  UNION: 'text-cyan-500',
  INTERSECT: 'text-cyan-500',
  EXCEPT: 'text-cyan-500',
  FILTER: 'text-yellow-600 dark:text-yellow-500',
  PROJECT: 'text-yellow-600 dark:text-yellow-500',
}

function getOperatorColor(line: string): string {
  // Match operator patterns like "0:OlapScanNode", "1:EXCHANGE", "HASH JOIN", etc.
  const opMatch = line.match(/^\d+:(\w+)/)
  if (opMatch && OPERATOR_COLORS[opMatch[1]]) return OPERATOR_COLORS[opMatch[1]]
  // Check for standalone operator names
  for (const [op, color] of Object.entries(OPERATOR_COLORS)) {
    if (line.trim().startsWith(op)) return color
  }
  return 'text-foreground'
}

interface PlanNode {
  line: string
  indent: number
  children: PlanNode[]
}

function parseExplainPlan(text: string): PlanNode[] {
  const lines = text.split('\n')
  const root: PlanNode = { line: '', indent: -1, children: [] }
  const stack: PlanNode[] = [root]

  for (const rawLine of lines) {
    if (rawLine.trim() === '') continue
    const indent = rawLine.search(/\S/)
    const line = rawLine.trim()
    const node: PlanNode = { line, indent, children: [] }

    // Pop stack until we find a parent with less indentation
    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) {
      stack.pop()
    }
    stack[stack.length - 1].children.push(node)
    stack.push(node)
  }

  return root.children
}

function ExplainTreeView({ planText }: { planText: string }) {
  const tree = useMemo(() => parseExplainPlan(planText), [planText])
  const [collapsedFragments, setCollapsedFragments] = useState<Set<string>>(new Set())

  function toggleFragment(label: string) {
    setCollapsedFragments((prev) => {
      const next = new Set(prev)
      next.has(label) ? next.delete(label) : next.add(label)
      return next
    })
  }

  // Group top-level nodes into PLAN FRAGMENTs
  const fragments: { label: string; children: PlanNode[] }[] = []
  let currentFragment: { label: string; children: PlanNode[] } | null = null

  for (const node of tree) {
    if (node.line.startsWith('PLAN FRAGMENT')) {
      currentFragment = { label: node.line, children: node.children }
      fragments.push(currentFragment)
    } else if (currentFragment) {
      currentFragment.children.push(node)
    } else {
      // Lines before any PLAN FRAGMENT — create an implicit fragment
      currentFragment = { label: 'Execution Plan', children: [node] }
      fragments.push(currentFragment)
    }
  }

  return (
    <div className='space-y-1 font-mono text-xs'>
      {fragments.map((frag, fi) => {
        const isCollapsed = collapsedFragments.has(frag.label)
        return (
          <div key={fi} className='rounded-md border border-border overflow-hidden'>
            <button
              type='button'
              className='flex w-full items-center gap-1.5 bg-muted/40 px-2.5 py-1.5 text-left font-semibold text-foreground hover:bg-muted/60 transition-colors'
              onClick={() => toggleFragment(frag.label)}
            >
              <ChevronRight
                className={cn('size-3.5 shrink-0 transition-transform duration-200', !isCollapsed && 'rotate-90')}
              />
              <span className='text-primary'>{frag.label}</span>
            </button>
            {!isCollapsed && (
              <div className='px-2 py-1.5'>
                {frag.children.map((node, ni) => (
                  <ExplainNode key={ni} node={node} depth={0} />
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ExplainNode({ node, depth }: { node: PlanNode; depth: number }) {
  const isOperator = /^\d+:\w/.test(node.line)
  const isSink = /^RESULT SINK|^STREAM DATA SINK/.test(node.line)
  const colorClass = getOperatorColor(node.line)
  const hasChildren = node.children.length > 0

  return (
    <div style={{ paddingLeft: depth * 16 }}>
      <div
        className={cn(
          'flex items-start gap-1 rounded px-1.5 py-0.5 leading-relaxed',
          isOperator || isSink ? 'font-semibold' : '',
          isOperator || isSink ? colorClass : 'text-muted-foreground'
        )}
      >
        {hasChildren && isOperator && <ChevronRight className='mt-0.5 size-3 shrink-0 opacity-50' />}
        <span className='whitespace-pre-wrap break-all'>{node.line}</span>
      </div>
      {node.children.map((child, ci) => (
        <ExplainNode key={ci} node={child} depth={depth + 1} />
      ))}
    </div>
  )
}

function QueryHistory({
  items,
  loading,
  onLoadSql,
  onReRun,
}: {
  items: HistoryItem[]
  loading: boolean
  onLoadSql: (sql: string) => void
  onReRun: (sql: string) => void
}) {
  if (loading) {
    return <div className='flex items-center justify-center p-6 text-sm text-muted-foreground'>Loading history...</div>
  }

  if (!items.length) {
    return (
      <div className='flex flex-col items-center justify-center gap-2 p-6 text-sm text-muted-foreground'>
        <Clock className='size-8 opacity-40' />
        <span>No query history yet.</span>
      </div>
    )
  }

  function formatTime(iso: string) {
    if (!iso) return ''
    const d = new Date(iso)
    const now = new Date()
    const isToday =
      d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
    const time = d.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    if (isToday) return time
    return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${time}`
  }

  function formatDuration(ms: number | null) {
    if (ms == null) return '—'
    if (ms < 1000) return `${ms}ms`
    return `${(ms / 1000).toFixed(1)}s`
  }

  return (
    <div className='min-h-0 flex-1 overflow-auto'>
      {items.map((item) => (
        <div
          key={item.query_id}
          className='group flex items-start gap-3 border-b px-4 py-2.5 last:border-b-0 hover:bg-muted/50'
        >
          <div className='flex flex-1 flex-col gap-1 overflow-hidden'>
            <div className='flex items-center gap-2'>
              <span
                className={cn(
                  'inline-flex h-4 items-center rounded px-1 text-[10px] font-medium',
                  item.status === 'SUCCESS' ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600'
                )}
              >
                {item.status === 'SUCCESS' ? 'OK' : 'ERR'}
              </span>
              <span className='text-xs text-muted-foreground'>{formatDuration(item.duration_ms)}</span>
              {item.rows_affected != null && (
                <span className='text-xs text-muted-foreground'>{item.rows_affected} rows</span>
              )}
              {item.database_name && (
                <span className='text-xs text-muted-foreground'>
                  {item.database_name}
                  {item.schema_name ? `.${item.schema_name}` : ''}
                </span>
              )}
              <span className='ml-auto text-[11px] text-muted-foreground'>{formatTime(item.event_time)}</span>
            </div>
            <button
              type='button'
              className='w-full cursor-pointer text-left'
              onClick={() => onLoadSql(item.sql_text)}
              title={item.sql_text}
            >
              <code className='block truncate font-mono text-xs text-foreground/80'>{item.sql_text}</code>
            </button>
            {item.error_message && <p className='truncate text-[11px] text-destructive'>{item.error_message}</p>}
          </div>
          <div className='flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100'>
            <Button
              type='button'
              size='icon'
              variant='ghost'
              className='size-6'
              title='Copy SQL'
              onClick={() => navigator.clipboard.writeText(item.sql_text)}
            >
              <Copy className='size-3' />
            </Button>
            <Button
              type='button'
              size='icon'
              variant='ghost'
              className='size-6'
              title='Load into editor & run'
              onClick={() => onReRun(item.sql_text)}
            >
              <RotateCcw className='size-3' />
            </Button>
          </div>
        </div>
      ))}
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
  const completionRequestRef = useRef<{
    id: number
    controller: AbortController
  } | null>(null)
  const onRunRef = useRef(onRun)
  onRunRef.current = onRun
  // Refs for completion provider — prevents stale closure (provider registered once at mount)
  const valueRef = useRef(value)
  valueRef.current = value
  const databaseRef = useRef(database)
  databaseRef.current = database
  const schemaRef = useRef(schema)
  schemaRef.current = schema
  const roleRef = useRef(role)
  roleRef.current = role
  const { resolvedTheme } = useTheme()

  useEffect(() => {
    return () => {
      completionRequestRef.current?.controller.abort()
      providerRef.current?.dispose()
      if (activeSqlEditorInstance) {
        activeSqlEditorInstance = null
      }
    }
  }, [])

  useEffect(() => {
    completionRequestRef.current?.controller.abort()
    completionRequestRef.current = null
  }, [database, schema])

  async function provideCompletions(
    textUntilPosition: string,
    signal?: AbortSignal
  ): Promise<CompletionResponse['items']> {
    const curValue = valueRef.current
    const curDb = databaseRef.current
    const curSchema = schemaRef.current
    const curRole = roleRef.current

    // @stage context is exclusive: never mix stage suggestions with SQL
    // relations, keywords, or Monaco word-based suggestions.
    const stageContext = extractStageCompletionContext(textUntilPosition)
    if (stageContext) {
      const response = await api.get<CompletionResponse>(
        buildStageCompletionPath(curDb, stageContext),
        signal
      )
      return response.items
    }

    const relationContext = extractRelationCompletionContext(textUntilPosition)
    if (relationContext?.type === 'database') {
      const [databases, objects] = await Promise.all([
        api.get<CompletionResponse>(
          `/query/completions?kind=database&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(relationContext.prefix)}`,
          signal
        ),
        api.get<CompletionResponse>(
          `/query/completions?kind=object&database=${encodeURIComponent(curDb)}&schema=${encodeURIComponent(curSchema)}&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(relationContext.prefix)}`,
          signal
        ),
      ])
      return dedupeCompletionItems([...databases.items, ...objects.items])
    }

    if (relationContext?.type === 'schema') {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=schema&database=${encodeURIComponent(relationContext.database)}&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(relationContext.prefix)}`,
        signal
      )
      return response.items
    }

    if (relationContext?.type === 'object') {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=object&database=${encodeURIComponent(relationContext.database)}&schema=${encodeURIComponent(relationContext.schema)}&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(relationContext.prefix)}`,
        signal
      )
      return response.items
    }

    const aliasMatch = textUntilPosition.match(/([A-Za-z_][\w]*)\.$/)
    if (aliasMatch) {
      const tableName = resolveAliasTable(curValue, aliasMatch[1])
      if (tableName) {
        const response = await api.get<CompletionResponse>(
          `/query/completions?kind=column&database=${encodeURIComponent(curDb)}&role=${encodeURIComponent(curRole)}&table=${encodeURIComponent(tableName)}`,
          signal
        )
        return response.items
      }
    }

    const objectMatch = textUntilPosition.match(/\b(FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]*)$/i)
    if (objectMatch) {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=object&database=${encodeURIComponent(curDb)}&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(objectMatch[2] ?? '')}`,
        signal
      )
      return response.items
    }

    // Expression context: suggest columns from tables in FROM/JOIN scope
    const expressionKeywords = /\b(?:SELECT|WHERE|AND|OR|ON|HAVING|GROUP\s+BY|ORDER\s+BY|SET|WHEN|THEN|ELSE)\s/i
    if (expressionKeywords.test(textUntilPosition) && curDb) {
      const tables = extractTablesInScope(curValue)
      if (tables.length > 0) {
        // Fetch columns from all in-scope tables (limit to 5 to avoid too many requests)
        const limitedTables = tables.slice(0, 5)
        const allColumnItems: CompletionResponse['items'] = []
        const fetches = limitedTables.map(async ({ tableName }) => {
          try {
            const response = await api.get<CompletionResponse>(
              `/query/completions?kind=column&database=${encodeURIComponent(curDb)}&role=${encodeURIComponent(curRole)}&table=${encodeURIComponent(tableName)}`,
              signal
            )
            return response.items
          } catch {
            return []
          }
        })
        const results = await Promise.all(fetches)
        for (const items of results) {
          allColumnItems.push(...items)
        }
        // Deduplicate column labels
        const seenCols = new Set<string>()
        const uniqueCols = allColumnItems.filter((item) => {
          if (seenCols.has(item.label)) return false
          seenCols.add(item.label)
          return true
        })
        // Merge with keyword suggestions
        const keywordItems = SQL_KEYWORDS.filter((keyword) =>
          keyword.toLowerCase().startsWith(lastWord(textUntilPosition).toLowerCase())
        ).map((keyword) => ({ label: keyword, type: 'keyword' }))
        return [...uniqueCols, ...keywordItems]
      }
    }

    return SQL_KEYWORDS.filter((keyword) =>
      keyword.toLowerCase().startsWith(lastWord(textUntilPosition).toLowerCase())
    ).map((keyword) => ({ label: keyword, type: 'keyword' }))
  }

  function handleMount(editor: Parameters<NonNullable<ComponentProps<typeof Editor>['onMount']>>[0], monaco: Monaco) {
    activeSqlEditorInstance = editor
    // Define custom Nova themes — light and dark
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
    monaco.editor.defineTheme('nova-dark', {
      base: 'vs-dark',
      inherit: true,
      rules: [
        { token: 'keyword', foreground: 'f36b5b', fontStyle: 'bold' },
        { token: 'keyword.sql', foreground: 'f36b5b', fontStyle: 'bold' },
        { token: 'predefined.sql', foreground: 'f36b5b' },
        { token: 'operator.sql', foreground: 'f36b5b' },
        { token: 'string', foreground: '2cc6b6' },
        { token: 'string.sql', foreground: '2cc6b6' },
        { token: 'number', foreground: 'f0ad3d' },
        { token: 'comment', foreground: '6b7a8d', fontStyle: 'italic' },
        { token: 'type', foreground: 'a78bfa' },
        { token: 'identifier', foreground: 'e2e8f0' },
        { token: 'predefined', foreground: 'f36b5b' },
      ],
      colors: {
        'editor.background': '#0f1117',
        'editor.foreground': '#e2e8f0',
        'editor.lineHighlightBackground': '#1a1d2e',
        'editor.selectionBackground': '#d0473840',
        'editorCursor.foreground': '#e45a49',
      },
    })
    monaco.editor.setTheme(resolvedTheme === 'dark' ? 'nova-dark' : 'nova-light')

    providerRef.current?.dispose()
    providerRef.current = monaco.languages.registerCompletionItemProvider('sql', {
      triggerCharacters: ['@', '.', ' '],
      provideCompletionItems: async (
        model: Parameters<Monaco['languages']['registerCompletionItemProvider']>[1] extends {
          provideCompletionItems: infer T
        }
          ? T extends (...args: infer A) => unknown
            ? A[0]
            : never
          : never,
        position: Parameters<Monaco['languages']['registerCompletionItemProvider']>[1] extends {
          provideCompletionItems: infer T
        }
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
        completionRequestRef.current?.controller.abort()
        const request = {
          id: (completionRequestRef.current?.id ?? 0) + 1,
          controller: new AbortController(),
        }
        completionRequestRef.current = request

        try {
          const items = await provideCompletions(textUntilPosition, request.controller.signal)
          if (completionRequestRef.current?.id !== request.id) {
            return { suggestions: [] }
          }

          const stageContext = extractStageCompletionContext(textUntilPosition)
          const stageRange = stageContext
            ? getStageCompletionReplacementRange(
                position.lineNumber,
                position.column,
                stageContext
              )
            : undefined

          return {
            suggestions: items.map((item) => ({
              label:
                getStageCompletionKind(item) === 'file'
                  ? {
                      label: item.label,
                      description: getStageFileExtension(item) ?? 'FILE',
                    }
                  : item.label,
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
                          : getStageCompletionKind(item) === 'stage'
                            ? monaco.languages.CompletionItemKind.Module
                            : getStageCompletionKind(item) === 'folder'
                              ? monaco.languages.CompletionItemKind.Folder
                              : getStageCompletionKind(item) === 'file'
                                ? monaco.languages.CompletionItemKind.File
                                : monaco.languages.CompletionItemKind.Variable,
              insertText: getStageCompletionInsertText(item),
              sortText:
                item.type === 'stage' ||
                item.type === 'stage_folder' ||
                item.type === 'stage_file'
                  ? getStageCompletionSortText(item)
                  : undefined,
              detail:
                item.type === 'stage' || item.type === 'stage_folder' || item.type === 'stage_file'
                  ? formatStageCompletionDetail(item)
                  : item.detail,
              range:
                item.type === 'stage' || item.type === 'stage_folder' || item.type === 'stage_file'
                  ? stageRange
                  : undefined,
              command: shouldTriggerStageSuggestions(item)
                ? {
                    id: 'editor.action.triggerSuggest',
                    title: 'Show stage contents',
                  }
                : undefined,
            })),
          }
        } catch {
          return { suggestions: [] }
        }
      },
    })

    // SQL Formatting — Format Document + Format Selection
    const formatOpts = {
      language: 'mysql' as const,
      keywordCase: 'upper' as const,
      tabWidth: 2,
    }
    monaco.languages.registerDocumentFormattingEditProvider('sql', {
      provideDocumentFormattingEdits(model: any) {
        try {
          const formatted = formatSql(model.getValue(), formatOpts)
          return [{ range: model.getFullModelRange(), text: formatted }]
        } catch {
          return []
        }
      },
    })
    monaco.languages.registerDocumentRangeFormattingEditProvider('sql', {
      provideDocumentRangeFormattingEdits(model: any, range: any) {
        try {
          const text = model.getValueInRange(range)
          const formatted = formatSql(text, formatOpts)
          return [{ range, text: formatted }]
        } catch {
          return []
        }
      },
    })
    // Ctrl+Shift+F keyboard shortcut for format
    editor.addAction({
      id: 'format-sql',
      label: 'Format SQL',
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyF],
      run: (ed) => {
        ed.getAction('editor.action.formatDocument')?.run()
      },
    })

    // Ctrl/Cmd+Enter to run query — DOM listener is more reliable than
    // Monaco's addCommand which can be swallowed by the keybinding service
    const editorDomNode = editor.getDomNode()
    if (editorDomNode) {
      const handler = (e: KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          onRunRef.current()
        }
      }
      editorDomNode.addEventListener('keydown', handler, true)
    }
  }

  return (
    <Editor
      height='100%'
      language='sql'
      theme={resolvedTheme === 'dark' ? 'nova-dark' : 'nova-light'}
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
        wordBasedSuggestions: 'off',
        suggest: {
          showWords: false,
        },
        suggestOnTriggerCharacters: true,
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

function extractRelationCompletionContext(
  text: string
):
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

  const schemaMatch = text.match(/\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]+)\.([A-Za-z0-9_]*)$/i)
  if (schemaMatch) {
    return {
      type: 'schema',
      database: schemaMatch[1],
      prefix: schemaMatch[2] ?? '',
    }
  }

  const databaseMatch = text.match(/\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]*)$/i)
  if (databaseMatch) {
    return {
      type: 'database',
      prefix: databaseMatch[1] ?? '',
    }
  }

  return null
}

function resolveAliasTable(sql: string, alias: string) {
  const regex = /\b(?:FROM|JOIN)\s+([A-Za-z0-9_.`]+)(?:\s+(?:AS\s+)?([A-Za-z0-9_]+))?/gi
  let match: RegExpExecArray | null = regex.exec(sql)
  while (match) {
    if (match[2] === alias) {
      return match[1].replace(/`/g, '').split('.').pop() ?? null
    }
    match = regex.exec(sql)
  }
  return null
}

function extractTablesInScope(sql: string): Array<{ tableName: string; alias: string | null }> {
  const regex = /\b(?:FROM|JOIN)\s+([A-Za-z0-9_.`]+)(?:\s+(?:AS\s+)?([A-Za-z0-9_]+))?/gi
  const tables: Array<{ tableName: string; alias: string | null }> = []
  const seen = new Set<string>()
  let match: RegExpExecArray | null = regex.exec(sql)
  while (match) {
    const raw = match[1].replace(/`/g, '')
    const tableName = raw.split('.').pop() ?? raw
    const alias = match[2] ?? null
    if (!seen.has(tableName)) {
      seen.add(tableName)
      tables.push({ tableName, alias })
    }
    match = regex.exec(sql)
  }
  return tables
}

function lastWord(text: string) {
  return text.split(/\s+/).pop() ?? ''
}

function isDestructiveSql(sql: string) {
  const pattern = /^\s*(DROP|TRUNCATE|ALTER\s+TABLE\s+.+\s+DROP|DELETE\s+FROM|UPDATE\s+)/i
  // Check each statement in multi-statement SQL
  return sql.split(';').some((stmt) => pattern.test(stmt))
}
