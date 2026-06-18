# Architecture 05: Frontend Architecture

> React + Next.js + shadcn/ui + Monaco Editor.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 15 (App Router) |
| UI Library | shadcn/ui + Radix primitives |
| Styling | Tailwind CSS |
| Editor | Monaco Editor (VS Code engine) |
| State | Zustand (lightweight) |
| HTTP | fetch / SWR |
| Tables | TanStack Table |
| Charts | Recharts (for monitoring) |
| Icons | Lucide React |

---

## Project Structure

```
frontend/
├── package.json
├── next.config.js
├── tailwind.config.ts
├── tsconfig.json
├── components.json              # shadcn config
│
├── app/
│   ├── layout.tsx               # Root layout (sidebar + content)
│   ├── page.tsx                 # Dashboard
│   ├── globals.css
│   │
│   ├── worksheet/
│   │   └── page.tsx             # SQL Worksheet
│   │
│   ├── catalog/
│   │   └── page.tsx             # Catalog Explorer
│   │
│   ├── stages/
│   │   ├── page.tsx             # Stage list
│   │   └── [id]/
│   │       └── page.tsx         # Stage detail (file browser)
│   │
│   ├── tables/
│   │   ├── page.tsx             # Table list
│   │   └── [name]/
│   │       └── page.tsx         # Table detail
│   │
│   ├── views/
│   │   └── page.tsx             # Views & MVs
│   │
│   ├── functions/
│   │   └── page.tsx             # Functions
│   │
│   ├── tasks/
│   │   └── page.tsx             # Task Manager
│   │
│   ├── pipes/
│   │   └── page.tsx             # Pipe Manager
│   │
│   ├── catalogs/
│   │   └── page.tsx             # External Catalogs
│   │
│   ├── users/
│   │   └── page.tsx             # User Management
│   │
│   ├── resources/
│   │   └── page.tsx             # Resource Groups
│   │
│   ├── monitor/
│   │   └── page.tsx             # Cluster Monitor
│   │
│   ├── admin/
│   │   └── connections/
│   │       └── page.tsx         # Storage Connections (admin)
│   │
│   └── settings/
│       └── page.tsx             # Settings
│
├── components/
│   ├── ui/                      # shadcn components
│   │   ├── button.tsx
│   │   ├── dialog.tsx
│   │   ├── table.tsx
│   │   ├── tabs.tsx
│   │   ├── dropdown-menu.tsx
│   │   ├── input.tsx
│   │   ├── toast.tsx
│   │   └── ...
│   │
│   ├── layout/
│   │   ├── sidebar.tsx          # Main sidebar navigation
│   │   ├── header.tsx           # Top bar (context switcher)
│   │   └── context-switcher.tsx # Database + Schema selector
│   │
│   ├── sql/
│   │   ├── sql-editor.tsx       # Monaco-based SQL editor
│   │   ├── sql-results.tsx      # Result table
│   │   ├── sql-toolbar.tsx      # Run, Format, etc.
│   │   ├── sql-history.tsx      # Query history panel
│   │   └── stage-autocomplete.tsx  # @stage autocomplete provider
│   │
│   ├── catalog/
│   │   ├── catalog-tree.tsx     # Tree view (catalogs → tables)
│   │   ├── table-detail.tsx     # Table detail tabs
│   │   └── column-list.tsx      # Column list component
│   │
│   ├── stage/
│   │   ├── stage-list.tsx       # Stage list view
│   │   ├── file-browser.tsx     # File browser (like Finder)
│   │   ├── file-upload.tsx      # Drag & drop upload
│   │   └── file-preview.tsx     # File content preview
│   │
│   └── monitor/
│       ├── node-status.tsx      # FE/BE node cards
│       ├── metric-chart.tsx     # Metric line chart
│       └── query-table.tsx      # Query history table
│
├── lib/
│   ├── api.ts                   # API client
│   ├── utils.ts                 # Utility functions
│   └── types.ts                 # TypeScript types
│
├── stores/
│   ├── context-store.ts         # Current database/schema
│   ├── sql-store.ts             # SQL editor state
│   └── catalog-store.ts         # Catalog tree state
│
└── hooks/
    ├── use-sql-execute.ts       # SQL execution hook
    ├── use-stages.ts            # Stage data hook
    └── use-catalogs.ts          # Catalog data hook
```

---

## Layout Design

```
┌─────────────────────────────────────────────────────────────┐
│  Nova                              [DATALAKE ▼] [bronze ▼]  │ ← Header
├──────────┬──────────────────────────────────────────────────┤
│          │                                                   │
│ 📊 SQL   │  ┌─ SQL Worksheet ──────────────────────────────┐│
│    Worksheet│ │                                              ││
│          │ │ SELECT * FROM @stage1.data.csv LIMIT 10;      ││
│ 📁 Catalog│ │                                              ││
│    Explorer│ │ ┌─ Results ──────────────────────────────┐   ││
│          │ │ │ id  name   amount                        │   ││
│ 🗄 Stages │ │ │ 1   Andi   150000                       │   ││
│          │ │ │ 2   Budi   230000                        │   ││
│ 📋 Tables │ │ │ 3   Citra  89000                        │   ││
│          │ │ └──────────────────────────────────────────┘   ││
│ 👁 Views  │ └──────────────────────────────────────────────┘│
│          │                                                   │
│ ⚡ Functions│                                                │
│          │                                                   │
│ 📝 Tasks  │                                                  │
│          │                                                   │
│ 🔗 Pipes  │                                                  │
│          │                                                   │
│ 🌐 Catalogs│                                                 │
│          │                                                   │
│ 👥 Users  │                                                  │
│          │                                                   │
│ 📈 Monitor│                                                  │
│          │                                                   │
│ 💾 Storage│                                                  │
│          │                                                   │
│ ⚙ Settings│                                                  │
│          │                                                   │
└──────────┴──────────────────────────────────────────────────┘
         │
         └── Sidebar (collapsible)
```

---

## Key Component: SQL Editor

```tsx
// components/sql/sql-editor.tsx
import Editor from "@monaco-editor/react";

interface SQLEditorProps {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
  database: string;
  schema: string;
}

export function SQLEditor({ value, onChange, onRun, database, schema }: SQLEditorProps) {
  // Monaco config
  const handleMount = (editor, monaco) => {
    // Register @stage autocomplete provider
    monaco.languages.registerCompletionItemProvider("sql", {
      triggerCharacters: ["@"],
      provideCompletionItems: async (model, position) => {
        const textUntilPosition = model.getValueInRange({...});
        
        // If user typed @, suggest stages
        if (textUntilPosition.includes("@")) {
          const stages = await api.getStages(database, schema);
          return stages.map(s => ({
            label: s.name,
            kind: monaco.languages.CompletionItemKind.Folder,
            insertText: s.name,
            detail: "Stage",
          }));
        }
        
        // If user typed @stage., suggest files
        const stageMatch = textUntilPosition.match(/@(\w+)\.$/);
        if (stageMatch) {
          const files = await api.getStageFiles(stageMatch[1]);
          return files.map(f => ({
            label: f.name,
            kind: monaco.languages.CompletionItemKind.File,
            insertText: f.name,
            detail: f.size,
          }));
        }
      }
    });
    
    // Register custom language tokens for @stage
    monaco.languages.setMonarchTokensProvider("sql", {
      tokenizer: {
        root: [
          [/@[\w.]+\.[\w]+/, "stage-reference"],
        ]
      }
    });
    
    // Custom theme for @stage highlighting
    monaco.editor.defineTheme("nova-dark", {
      base: "vs-dark",
      rules: [
        { token: "stage-reference", foreground: "F5A21E", fontStyle: "bold" }
      ]
    });
  };
  
  return (
    <Editor
      height="300px"
      language="sql"
      theme="nova-dark"
      value={value}
      onChange={onChange}
      onMount={handleMount}
      options={{
        minimap: { enabled: false },
        fontSize: 14,
        lineHeight: 22,
        padding: { top: 12 },
      }}
    />
  );
}
```

---

## Key Component: Context Switcher

```tsx
// components/layout/context-switcher.tsx

export function ContextSwitcher() {
  const { database, schema, setDatabase, setSchema } = useContextStore();
  
  return (
    <div className="flex items-center gap-2">
      <Select value={database} onValueChange={setDatabase}>
        <SelectTrigger className="w-[180px]">
          <Database className="w-4 h-4" />
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {databases.map(db => (
            <SelectItem key={db} value={db}>{db}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      
      <span className="text-muted-foreground">.</span>
      
      <Select value={schema} onValueChange={setSchema}>
        <SelectTrigger className="w-[180px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {schemas.map(s => (
            <SelectItem key={s} value={s}>{s}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
```

---

## Key Component: File Browser

```tsx
// components/stage/file-browser.tsx

export function FileBrowser({ stageId }: { stageId: string }) {
  const [path, setPath] = useState("");
  const { files, loading } = useStageFiles(stageId, path);
  
  return (
    <div className="border rounded-lg">
      {/* Breadcrumb */}
      <div className="flex items-center gap-1 p-3 border-b text-sm text-muted-foreground">
        <span className="cursor-pointer hover:text-foreground" onClick={() => setPath("")}>
          @stage1
        </span>
        {path.split("/").filter(Boolean).map((part, i) => (
          <Fragment key={i}>
            <ChevronRight className="w-3 h-3" />
            <span className="cursor-pointer hover:text-foreground"
                  onClick={() => setPath(path.split("/").slice(0, i+1).join("/"))}>
              {part}
            </span>
          </Fragment>
        ))}
      </div>
      
      {/* File list */}
      <div className="divide-y">
        {files.map(file => (
          <div key={file.name} className="flex items-center justify-between p-3 hover:bg-muted/50">
            <div className="flex items-center gap-3">
              {file.type === "folder" ? (
                <Folder className="w-5 h-5 text-blue-500" />
              ) : (
                <File className="w-5 h-5 text-gray-500" />
              )}
              <span className="cursor-pointer hover:underline"
                    onClick={() => file.type === "folder" 
                      ? setPath(`${path}/${file.name}`.replace(/^\//, ""))
                      : null
                    }>
                {file.name}
              </span>
            </div>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              {file.size && <span>{file.size}</span>}
              {file.modified && <span>{file.modified}</span>}
              <span className="text-xs bg-muted px-2 py-0.5 rounded font-mono">
                {file.query_ref}
              </span>
              <DropdownMenu>
                <DropdownMenuTrigger><MoreHorizontal className="w-4 h-4" /></DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem onSelect={() => previewFile(file)}>Preview</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => downloadFile(file)}>Download</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => copyQueryRef(file)}>Copy query ref</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => deleteFile(file)} className="text-red-600">Delete</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

---

## API Client

```ts
// lib/api.ts

const BASE = process.env.NEXT_PUBLIC_API_URL || "";

async function fetchJSON(path: string, options?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export const api = {
  // SQL
  executeSQL: (sql: string, database: string, schema: string) =>
    fetchJSON("/api/v1/sql/execute", {
      method: "POST",
      body: JSON.stringify({ sql, database, schema }),
    }),
  
  explainSQL: (sql: string, database: string, schema: string) =>
    fetchJSON("/api/v1/sql/explain", {
      method: "POST",
      body: JSON.stringify({ sql, database, schema }),
    }),
  
  // Catalogs
  getCatalogs: () => fetchJSON("/api/v1/catalogs"),
  getDatabases: (catalog?: string) => fetchJSON(`/api/v1/databases?catalog=${catalog || ""}`),
  getTables: (database: string) => fetchJSON(`/api/v1/tables?db=${database}`),
  getTableDetail: (database: string, table: string) =>
    fetchJSON(`/api/v1/tables/${table}?db=${database}`),
  
  // Stages
  getStages: (database: string, schema: string) =>
    fetchJSON(`/api/v1/stages?db=${database}&schema=${schema}`),
  getStageFiles: (stageId: string, prefix?: string) =>
    fetchJSON(`/api/v1/stages/${stageId}/files?prefix=${prefix || ""}`),
  uploadToStage: (stageId: string, file: File, path?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (path) form.append("path", path);
    return fetch(`${BASE}/api/v1/stages/${stageId}/upload`, { method: "POST", body: form });
  },
  
  // Storage connections
  getConnections: () => fetchJSON("/api/v1/connections"),
  testConnection: (id: string) => fetchJSON(`/api/v1/connections/${id}/test`, { method: "POST" }),
  
  // Cluster
  getClusterStatus: () => fetchJSON("/api/v1/cluster/status"),
};
```
