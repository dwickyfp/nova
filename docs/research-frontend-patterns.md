# Nova Frontend Patterns — Deep Research Report

> Complete analysis of the workspaces page architecture, API client, React Query patterns, TypeScript types, and real StarRocks data fetching.

---

## 1. Project Stack & Architecture Overview

```
Frontend:  React 19 + Vite 8 + TanStack Router + TanStack Query v5 + Tailwind 4 + shadcn/ui
Backend:   FastAPI + asyncmy (MySQL protocol → StarRocks)
State:     Zustand (auth only) + React Query (server state) + useState (local UI)
Routing:   TanStack Router (file-based, code-split)
Editor:    Monaco Editor (@monaco-editor/react)
Charts:    Recharts 3
```

### Directory Structure
```
frontend/src/
├── main.tsx                          # QueryClient + Router setup
├── lib/
│   ├── api-client.ts                 # Centralized fetch wrapper
│   ├── utils.ts                      # cn() utility
│   ├── cookies.ts                    # Cookie helpers
│   └── handle-server-error.ts        # Error toast helper
├── stores/
│   └── auth-store.ts                 # Zustand auth store (token + user)
├── routes/
│   ├── __root.tsx                    # Root layout (Toaster, progress bar)
│   ├── _authenticated/
│   │   ├── route.tsx                 # Auth guard (redirect if no token)
│   │   └── workspaces/
│   │       └── index.tsx             # Route → WorkspacesPage component
├── features/
│   └── workspaces/
│       ├── index.tsx                 # 4127-line mega-component (THE workspaces page)
│       ├── database-schema-selector.tsx  # DB+Schema popover picker
│       └── inline-select.tsx         # Role dropdown picker
└── components/
    ├── layout/                       # AuthenticatedLayout, AppSidebar, Header
    └── ui/                           # shadcn primitives (Button, Input, etc.)
```

---

## 2. API Client (`src/lib/api-client.ts`)

A **lightweight fetch wrapper** (NOT axios for actual requests, despite axios being in dependencies). Only 56 lines.

```typescript
const API_BASE = '/api/v1'

async function request<T>(path: string, options: RequestInit & { signal?: AbortSignal } = {}): Promise<T> {
  const token = useAuthStore.getState().auth.accessToken   // ← Reads token from Zustand
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`           // ← JWT Bearer auth
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, signal: options.signal })
  
  if (res.status === 401) {                                // ← Auto-logout on 401
    useAuthStore.getState().auth.reset()
    window.location.href = '/sign-in'
    throw new Error('Session expired')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  if (res.status === 204) return undefined as T
  // Handle non-JSON responses
  const contentType = res.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    const text = await res.text()
    return (text ? text : undefined) as T
  }
  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) => request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined, signal }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string, body?: unknown) => request<T>(path, { method: 'DELETE', body: body ? JSON.stringify(body) : undefined }),
}
```

### Key Patterns:
- **Generic `<T>` typing** — caller specifies expected response type
- **Token from Zustand store** — `useAuthStore.getState().auth.accessToken` (outside React)
- **Auto-redirect on 401** — clears auth state + redirects
- **AbortSignal support** — POST accepts a signal for query cancellation
- **Vite dev proxy** — `/api` proxied to `http://localhost:8000` in dev

---

## 3. React Query Patterns

### QueryClient Configuration (`main.tsx`)
```typescript
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (failureCount >= 0 && import.meta.env.DEV) return false  // No retries in dev
        if (failureCount > 3 && import.meta.env.PROD) return false  // Max 3 in prod
        return !(error instanceof AxiosError && [401, 403].includes(error.response?.status ?? 0))
      },
      refetchOnWindowFocus: import.meta.env.PROD,  // Only refetch on focus in prod
      staleTime: 10 * 1000,                         // 10 seconds stale time
    },
  },
})
```

### All React Query Keys Used in Workspaces:

| Query Key | Endpoint | Purpose | Enabled Condition |
|---|---|---|---|
| `['workspace-tree']` | `GET /workspaces/tree` | File tree + open tabs + defaults | Always |
| `['query-context']` | `GET /query/context` | Roles, databases, schemas, defaults | Always |
| `['object-databases']` | `GET /objects/databases` | Database list for sidebar | Always |
| `['db-schemas', database, role]` | `GET /objects/databases/{db}/schemas` | Schemas for a specific database | When DB expanded |
| `['schema-tree', database, schema, role]` | `GET /objects/databases/{db}/schemas/{schema}/tree` | Tables, views, MVs, stages | When schema expanded |
| `['table-columns', database, schema, name]` | `GET /objects/databases/{db}/tables/{table}/columns` | Column info for popover | On hover |
| `['query-history', filter]` | `GET /query/history` | Query execution history | When history tab active |

### Data Fetching Pattern — Progressive Disclosure:

```
Page Load:
  ├── useQuery(['workspace-tree'])        → file tree + tab state
  ├── useQuery(['query-context'])         → roles + databases + schemas + defaults
  └── useQuery(['object-databases'])      → all databases

User expands a database in sidebar:
  └── useQuery(['db-schemas', db, role])  → schemas for that database (enabled: expanded)

User expands a schema in sidebar:
  └── useQuery(['schema-tree', db, schema, role]) → tables/views/MVs/stages (enabled: expanded)

User hovers over a table:
  └── useQuery(['table-columns', db, schema, table]) → column details (enabled: hovered, staleTime: 60s)
```

### Query Pattern Example — DatabaseNode:
```typescript
function DatabaseNode({ database, expanded, ... }) {
  const schemasQuery = useQuery<SchemaResponse>({
    queryKey: ['db-schemas', database, role],
    queryFn: () =>
      api.get<SchemaResponse>(
        `/objects/databases/${encodeURIComponent(database)}/schemas${role ? `?role=${encodeURIComponent(role)}` : ''}`
      ),
    enabled: expanded,     // ← Lazy: only fetches when user expands the node
  })
  // Syncs schemas to parent state for the DatabaseSchemaSelector
  useEffect(() => {
    if (schemasQuery.data) {
      setSchemasByDatabase((prev) => ({ ...prev, [database]: schemasQuery.data.schemas }))
    }
  }, [database, schemasQuery.data, setSchemasByDatabase])
}
```

### Mutation Pattern Example — Save File:
```typescript
const saveStateMutation = useMutation({
  mutationFn: (state: { open_tabs: string[], active_tab: string | null, ... }) =>
    api.put<{ success: boolean }>('/workspaces/state', state),
})
```

### Invalidation Pattern:
```typescript
// After save, invalidate tree
await queryClient.invalidateQueries({ queryKey: ['workspace-tree'] })

// Refresh button invalidates all database-related queries
void queryClient.invalidateQueries({ queryKey: ['object-databases'] })
void queryClient.invalidateQueries({ queryKey: ['db-schemas'] })
void queryClient.invalidateQueries({ queryKey: ['schema-tree'] })
```

---

## 4. TypeScript Types (Complete Catalog)

### API Response Types (defined in `features/workspaces/index.tsx`):

```typescript
// ── Workspace File System ────────────────────────
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

// ── Query Context (roles, databases, schemas) ───
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

// ── Query Execution ──────────────────────────────
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

// ── Schema Browsing ──────────────────────────────
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

// ── Autocomplete ─────────────────────────────────
type CompletionResponse = {
  items: Array<{ label: string; type: string }>
}

// ── Column Info (for table popover) ──────────────
type ColumnInfo = {
  name: string
  type: string
  null: string
  key: string
  default: string | null
  extra: string
}

// ── Query History ────────────────────────────────
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

// ── Tab State (local UI state) ───────────────────
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
```

### Auth Types (defined in `stores/auth-store.ts`):
```typescript
interface AuthUser {
  username: string
  roles: string[]
  activeRole?: string | null
}
```

### Database Explorer Types (defined in `features/database-explorer/index.tsx` — STATIC/DUMMY):
```typescript
type ExplorerNodeType = 'catalog' | 'database' | 'group' | 'table' | 'view' | 'materialized_view' | 'function' | 'pipe' | 'stage' | 'workspace_entry'

type ExplorerNode = {
  id: string
  label: string
  type: ExplorerNodeType
  path: string[]
  owner: string
  updatedAt: string
  description: string
  status: 'Active' | 'Planned' | 'Virtual'
  tags: string[]
  metadata: Array<{ label: string; value: string }>
  children?: ExplorerNode[]
}
```

> ⚠️ **Important distinction**: The `features/database-explorer/index.tsx` is a **separate standalone page** with 100% **hardcoded dummy data**. It does NOT connect to StarRocks. The REAL data fetching happens in the workspaces page's sidebar `DatabaseExplorer` component (defined inline in `features/workspaces/index.tsx`).

---

## 5. Data Flow: How the Workspaces Page Fetches Real StarRocks Data

### Complete Request Chain:

```
┌─────────────────────────────────────────────────────────────────────┐
│ FRONTEND (React)                                                     │
│                                                                      │
│  WorkspacesPage                                                      │
│  ├── useQuery(['workspace-tree'])    → GET /api/v1/workspaces/tree   │
│  ├── useQuery(['query-context'])     → GET /api/v1/query/context     │
│  ├── useQuery(['object-databases'])  → GET /api/v1/objects/databases │
│  │                                                                    │
│  ├── DatabaseExplorer (sidebar "Databases" tab)                      │
│  │   └── DatabaseNode (per database, lazy)                           │
│  │       └── useQuery(['db-schemas', db])  → GET .../schemas         │
│  │           └── SchemaNode (per schema, lazy)                       │
│  │               └── useQuery(['schema-tree', db, schema])           │
│  │                   → GET .../schemas/{schema}/tree                 │
│  │                   └── ObjectGroup (Tables/Views/MVs/Stages)       │
│  │                       └── TableItemWithPopover (on hover)         │
│  │                           └── useQuery(['table-columns', ...])    │
│  │                               → GET .../tables/{table}/columns    │
│  │                                                                    │
│  ├── DatabaseSchemaSelector (toolbar popover)                        │
│  │   ├── databases: from queryContextQuery.data.databases            │
│  │   └── schemas: from schemasByDatabase[activeTab.database]         │
│  │       (fetched on-demand when user selects a database)            │
│  │                                                                    │
│  └── MonacoSqlEditor (autocomplete)                                  │
│      └── api.get('/query/completions?kind=...') on keystroke         │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTP (Bearer JWT)
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ BACKEND (FastAPI)                                                    │
│                                                                      │
│  deps.get_current_user() → JWT decode → Redis session → encrypted PW │
│                                                                      │
│  objects/router.py:                                                  │
│  ├── GET /objects/databases       → object_service.browse_tree()     │
│  ├── GET /objects/databases/{db}/schemas → object_service.list_schemas() │
│  ├── GET /objects/databases/{db}/schemas/{schema}/tree → get_schema_tree()│
│  └── GET /objects/databases/{db}/tables/{table}/columns → get_columns()  │
│                                                                      │
│  query/router.py:                                                    │
│  ├── GET  /query/context         → query_service.get_context()       │
│  ├── GET  /query/completions     → query_service.get_completions()   │
│  ├── POST /query/execute         → query_service.execute_statements()│
│  └── GET  /query/history         → query_service.get_history()       │
│                                                                      │
│  workspaces/router.py:                                               │
│  ├── GET  /workspaces/tree       → workspace_service.get_tree()      │
│  ├── POST /workspaces/files      → create file                       │
│  ├── GET  /workspaces/files/{id} → get file content                  │
│  ├── PUT  /workspaces/files/{id} → save file content                 │
│  └── PUT  /workspaces/state      → persist UI state                  │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ asyncmy (MySQL protocol)
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STARROCKS (port 9030 MySQL protocol)                                 │
│                                                                      │
│  SHOW DATABASES                                                      │
│  SHOW TABLES FROM `db`                                               │
│  SHOW CREATE VIEW `db`.`view`  (to distinguish views from tables)    │
│  DESC `db`.`table`             (column info)                         │
│  SHOW CREATE TABLE `db`.`table` (DDL)                                │
│  SHOW ALTER MATERIALIZED VIEW FROM `db`                              │
│  SET ROLE {role}               (per-connection RBAC)                 │
│                                                                      │
│  + NOVA_SYSTEM.CONFIG_STAGES    (stage configs, schema info)         │
│  + NOVA_SYSTEM.AUDIT_LOG        (query history)                      │
│  + NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES (saved files)                │
│  + NOVA_SYSTEM.CONFIG_USER_PREFERENCES  (user prefs)                │
└─────────────────────────────────────────────────────────────────────┘
```

### Connection Architecture:
- **System pool** (`db.system_conn()`): Admin connection for `NOVA_SYSTEM` queries (workspace files, audit logs, stage configs)
- **User connections** (`db.user_conn(username, password)`): Per-request, no pool. Each API call creates a fresh MySQL connection with the user's credentials, ensuring **RBAC is enforced by StarRocks itself**.

---

## 6. Authentication Flow

```
1. User signs in → POST /api/v1/auth/login
   → Returns JWT access token
   → Token stored in cookie (nova_access_token) via Zustand

2. AuthenticatedLayout (route guard):
   → beforeLoad: checks useAuthStore.auth.accessToken exists
   → If missing: redirect to /sign-in
   → On mount: GET /api/v1/auth/me → sets user (username, roles)

3. API Client:
   → Every request reads token from Zustand store
   → Sets Authorization: Bearer {token} header
   → On 401 response: clears auth state, redirects to /sign-in

4. Backend dependency injection:
   → get_current_user() extracts JWT → verifies session in Redis
   → Returns {username, session_id, roles, encrypted_password}
   → encrypted_password is Fernet-encrypted, decrypted per-request for StarRocks
```

---

## 7. Key Architectural Patterns

### A. Per-Tab Context Isolation
Each workspace tab has its own database/schema/role context:
```typescript
type WorkspaceTabState = {
  id: string
  title: string
  content: string
  database: string    // ← Per-tab database
  schema: string      // ← Per-tab schema
  role: string        // ← Per-tab role
  loaded: boolean
}
```

### B. Progressive Disclosure (Lazy Loading)
- Databases load on page mount
- Schemas load when user expands a database node
- Schema tree (tables/views) loads when user expands a schema node
- Column info loads on hover with 60s staleTime

### C. State Persistence
- Tab state (open tabs, active tab, sidebar collapsed) auto-saves via debounced mutation (300ms)
- Last query results persisted to `sessionStorage` for page refresh recovery
- File content auto-saves after 700ms of inactivity

### D. Dual Sidebar Modes
The sidebar has two tabs:
- **Workspaces**: File tree (saved SQL files in NOVA_SYSTEM)
- **Databases**: Live StarRocks object browser (progressive disclosure)

### E. SQL Autocomplete
The Monaco editor fetches completions contextually:
- After `FROM`/`JOIN` → object names (tables/views)
- After `database.` → schema names
- After `schema.` → object names in that schema
- After `@` → stage names
- After `@stage.` → stage file names
- After `alias.` → column names (resolves table aliases)
- In expression context → columns from in-scope tables + SQL keywords

### F. Database vs Schema Distinction
In Nova's StarRocks model:
- **Database** = StarRocks database (real, `SHOW DATABASES`)
- **Schema** = Nova concept stored in `NOVA_SYSTEM.CONFIG_STAGES` (not StarRocks native schemas)
- Tables/views are **database-scoped** (only shown under "default" schema)
- Stages are **schema-scoped** (from CONFIG_STAGES)

---

## 8. Backend API Endpoints Summary

### Objects Module (`/api/v1/objects`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/databases` | List all databases with counts |
| GET | `/databases/{db}` | Database detail + DDL |
| GET | `/databases/{db}/objects` | All objects (tables+views+MVs) |
| GET | `/databases/{db}/tables` | List tables |
| GET | `/databases/{db}/tables/{table}` | Table detail (columns, DDL, status) |
| GET | `/databases/{db}/tables/{table}/columns` | Column list |
| GET | `/databases/{db}/tables/{table}/ddl` | CREATE TABLE DDL |
| GET | `/databases/{db}/views` | List views |
| GET | `/databases/{db}/views/{view}` | View detail |
| GET | `/databases/{db}/schemas` | List schemas (from CONFIG_STAGES) |
| GET | `/databases/{db}/schemas/{schema}/tree` | Full tree: tables+views+MVs+stages |

### Query Module (`/api/v1/query`)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/execute` | Execute SQL (multi-statement) |
| POST | `/explain` | EXPLAIN plan |
| GET | `/context` | Roles + databases + schemas + defaults |
| GET | `/completions` | Autocomplete items (by kind) |
| GET | `/history` | Query execution history |
| GET | `/history/stats` | History statistics |

### Workspaces Module (`/api/v1/workspaces`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/tree` | File tree + tab state + defaults |
| POST | `/files` | Create new file |
| GET | `/files/{id}` | Get file content |
| PUT | `/files/{id}` | Save file content |
| DELETE | `/files/{id}` | Delete file |
| POST | `/folders` | Create folder |
| POST | `/rename` | Rename entry |
| PUT | `/state` | Save UI state (tabs, sidebar) |

---

## 9. Component Hierarchy (Workspaces Page)

```
WorkspacesPage
├── Header (fixed, title + description)
├── Sidebar (collapsible, 320px)
│   ├── Tabs (Workspaces | Databases)
│   ├── Search + Refresh
│   ├── [Workspaces tab]:
│   │   └── WorkspaceTree (file tree from NOVA_SYSTEM)
│   │       └── WorkspaceEntry (file/folder nodes)
│   └── [Databases tab]:
│       └── DatabaseExplorer
│           └── DatabaseNode (useQuery: db-schemas, enabled: expanded)
│               └── SchemaNode (useQuery: schema-tree, enabled: expanded)
│                   └── ObjectGroup (Tables/Views/MVs/Stages)
│                       └── TableItemWithPopover (useQuery: table-columns, enabled: hover)
├── Tab Bar (open file tabs with close/rename/download)
├── Toolbar
│   ├── Run / Format / Explain buttons
│   ├── InlineSelect (Role picker)
│   └── DatabaseSchemaSelector (DB + Schema popover)
├── MonacoSqlEditor (with autocomplete, formatting, themes)
└── Results Panel (resizable)
    ├── Results tab (paginated table with sorting + context menu)
    ├── History tab (query history from AUDIT_LOG)
    ├── Explain tab (query plan text)
    └── Chart tab (Recharts visualization)
```

---

## 10. Important Implementation Notes

1. **No separate API hooks file** — All `useQuery` calls are inline in the component. There is no `useWorkspaces()` or `useDatabases()` custom hook abstraction. Everything is co-located.

2. **Mega-component pattern** — The workspaces page is a single 4127-line file containing the main `WorkspacesPage` export plus ~10 internal components (`DatabaseExplorer`, `DatabaseNode`, `SchemaNode`, `ObjectGroup`, `TableItemWithPopover`, `WorkspaceTree`, `MonacoSqlEditor`, `QueryResults`, etc.)

3. **Mixed state management** — Server state uses React Query, but local UI state (expanded nodes, search text, tab content, resize height) uses `useState`. There is NO Redux or Zustand for workspace state.

4. **The `DatabaseSchemaSelector` is a controlled component** — It receives databases/schemas as props and calls back via `onSelectDatabase`/`onSelectSchema`. The parent handles the actual schema fetching.

5. **`api.get` uses plain `fetch`, not axios** — Despite axios being listed as a dependency (and used in the QueryClient error handler for `AxiosError`), the actual API client uses native `fetch`. The AxiosError handling in `main.tsx` is likely a legacy pattern.

6. **Schemas are a Nova concept** — StarRocks doesn't have schemas in the traditional sense. Nova stores "schemas" in `NOVA_SYSTEM.CONFIG_STAGES` as a namespace for stages. Tables/views are always shown under the "default" schema.
