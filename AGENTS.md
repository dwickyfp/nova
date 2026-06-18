# AGENTS.md — Nova Project Guide

> This file is the single source of truth for any AI agent working on Nova.
> Read this before making any changes. Follow it strictly.

---

## What is Nova?

Nova is a **Snowflake-grade management console for StarRocks**. It provides:
- Web UI (React + Monaco Editor) on port 8000
- MySQL Protocol Proxy on port 4406 (any MySQL client can connect)
- SQL Dialect Engine with `@stage` syntax
- Storage-agnostic file management via Stages
- ML functions (forecast, classify, anomaly detection)
- LLM integration (AI_COMPLETE, AI_SENTIMENT, etc.)

**Engine:** StarRocks 4.1.x
**Backend:** FastAPI + Python 3.11
**Frontend:** React / Next.js / shadcn/ui / Monaco Editor

---

## Project Structure

```
~/public/Research/nova/
├── docs/                          # Feature specs + architecture docs (35 files)
│   ├── 01-overview.md             # Master overview
│   ├── 02 ~ 27-*.md              # Feature modules
│   ├── arch-01 ~ arch-07-*.md    # Architecture docs
│   └── gap-analysis.md           # Missing features analysis
│
├── backend/                       # FastAPI application
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── core/                  # config.py, security.py
│   │   ├── db/                    # starrocks.py (MySQL connector)
│   │   ├── models/                # stage_repo.py, etc.
│   │   ├── schemas/               # Pydantic schemas
│   │   ├── services/              # Business logic
│   │   ├── sql_dialect/           # Parser, translator, injector
│   │   ├── storage/               # Provider abstraction
│   │   ├── proxy/                 # MySQL protocol proxy
│   │   └── api/v1/endpoints/      # HTTP API routes
│   └── tests/
│
├── frontend/                      # React/Next.js app
│   ├── app/                       # Next.js pages
│   ├── components/                # UI components
│   ├── lib/                       # API client, utils
│   └── stores/                    # Zustand stores
│
├── nova.yaml                      # Storage connection config (git-versioned)
└── .env                           # Secrets (git-ignored)
```

---

## Architecture Rules (NEVER VIOLATE)

### 1. Storage-Agnostic UI

**Rule:** No S3, MinIO, Azure, GCS references in any user-facing UI element.

```
❌ "Upload to s3://bucket/path"
❌ "MinIO endpoint: http://minio:9000"
❌ "AWS S3 credentials"

✅ "Upload to @stage1"
✅ "Browse stage1/data/"
✅ "Storage: Production Storage" (connection name from nova.yaml)
```

### 2. Credential-Invisible

**Rule:** Credentials NEVER appear in UI, API responses, or database.

```
Credentials location:
  nova.yaml + .env → storage credentials (static, git-versioned)
  Memory (encrypted) → StarRocks user password (per-session only)

Credentials NEVER in:
  ❌ NOVA_SYSTEM tables
  ❌ API JSON responses
  ❌ Frontend state
  ❌ Logs
  ❌ Error messages
```

### 3. Single Database

**Rule:** All persistent state in StarRocks `NOVA_SYSTEM`. No SQLite, no PostgreSQL.

```
nova.yaml     → storage credentials, StarRocks connection (static)
NOVA_SYSTEM   → everything else (config + analytics)
```

### 4. @stage Syntax is Sacred

**Rule:** `@stage_name.file.csv` is the primary user-facing abstraction for file access.

```
User writes:     SELECT * FROM @stage1.data.csv
StarRocks gets:  SELECT * FROM FILES('path'='s3://...', 'format'='csv', creds...)
Nova rewrites:   @stage → FILES() + auto-detected format + injected credentials
```

**Access control:** Stages are schema-bound. User must have SELECT/INSERT on the parent database.schema to access stage files. See `04-stage-manager.md` for full access matrix.

### 5. StarRocks = Auth Source of Truth

**Rule:** Nova authenticates against StarRocks directly. No separate user table. All login users ARE StarRocks users.

```
root       → empty password, Docker internal only (FE↔BE), NOT exposed
nova_admin → default 'nova', first login forces password change
Others     → created by nova_admin via Nova UI or SQL

Auth: pymysql.connect(user=username, password=password) → success = authenticated
RBAC: SHOW GRANTS → UI adjusts based on privileges
```

### 6. ACCOUNTADMIN Role is Immutable SUPER USER

**Rule:** The `ACCOUNTADMIN` role is the **SUPER USER** role in Nova. It has the **highest privilege level** in StarRocks and **MUST NEVER be dropped, renamed, or revoked**.

**Privilege Level: MAXIMUM**
```
-- Object-level (ALL databases, tables, views, functions)
GRANT ALL ON *.* TO ROLE ACCOUNTADMIN WITH GRANT OPTION;

-- System-level (explicit — NOT covered by ALL ON *.*)
GRANT OPERATE ON SYSTEM TO ROLE ACCOUNTADMIN;           -- cluster/node management
GRANT CREATE RESOURCE GROUP ON SYSTEM TO ROLE ACCOUNTADMIN;  -- resource groups
GRANT CREATE RESOURCE ON SYSTEM TO ROLE ACCOUNTADMIN;   -- external resources
GRANT CREATE EXTERNAL CATALOG ON SYSTEM TO ROLE ACCOUNTADMIN; -- Hive, Iceberg, etc.
GRANT REPOSITORY ON SYSTEM TO ROLE ACCOUNTADMIN;        -- backup/restore
GRANT CREATE STORAGE VOLUME ON SYSTEM TO ROLE ACCOUNTADMIN;   -- shared-data storage
GRANT BLACKLIST ON SYSTEM TO ROLE ACCOUNTADMIN;          -- SQL blacklists
GRANT FILE ON SYSTEM TO ROLE ACCOUNTADMIN;               -- UDF jars, files
GRANT SECURITY ON SYSTEM TO ROLE ACCOUNTADMIN;           -- security policies
```

**Operations blocked:**
```
❌ DROP ROLE ACCOUNTADMIN;
❌ REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN;
❌ REVOKE USAGE ON *.* FROM ROLE ACCOUNTADMIN;
❌ REVOKE SELECT ON *.* FROM ROLE ACCOUNTADMIN;
❌ ALTER ROLE ACCOUNTADMIN ...;

✅ CREATE ROLE analyst;          ← create new roles freely
✅ DROP ROLE analyst;            ← drop non-system roles freely
```

**Guardrails in code:**
- Backend MUST intercept any `DROP ROLE` or `REVOKE` statement targeting `ACCOUNTADMIN` and reject it
- Admin UI MUST NOT show a "Delete" button for `ACCOUNTADMIN`
- `starrocks-init` creates `ACCOUNTADMIN WITH GRANT OPTION` — this cannot be recovered if dropped

**Why:** `ACCOUNTADMIN` is the only role with `GRANT OPTION`. If dropped, no user can grant privileges to others, effectively locking the entire system.

### 7. StarRocks Primary Key Tables for CRUD

**Rule:** Low-volume config data (stages, pins, prefs) uses Primary Key tables in NOVA_SYSTEM.CONFIG.

```sql
-- Primary Key = supports UPDATE/DELETE
CREATE TABLE NOVA_SYSTEM.CONFIG.STAGES (...) PRIMARY KEY(id)
    PROPERTIES("enable_persistent_index"="true");

-- Duplicate Key = append-only analytics
CREATE TABLE NOVA_SYSTEM.AUDIT.LOG (...) PRIMARY KEY(log_id);
```

---

## Coding Conventions

### Python (Backend)

```python
# Type hints always
def find_stage(db: str, schema: str, name: str) -> Stage | None:
    ...

# Dataclasses for data models
@dataclass
class StorageConnectionConfig:
    name: str
    type: str
    endpoint: str
    ...

# Pydantic for API schemas
class SQLRequest(BaseModel):
    sql: str
    database: str = "default_catalog"

# Context managers for DB connections
with sr_connection(database) as conn:
    with conn.cursor() as cur:
        cur.execute(sql)

# NEVER hardcode credentials
# ALWAYS use config.storage_connections[name]
```

### TypeScript (Frontend)

```tsx
// Functional components with hooks
export function FileBrowser({ stageId }: { stageId: string }) {
  const { files, loading } = useStageFiles(stageId);
  ...
}

// API calls via centralized client
import { api } from "@/lib/api";
const result = await api.executeSQL(sql, database, schema);

// shadcn/ui components always
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";

# NEVER use hardcoded heights → flex-1 + min-h-0
# NEVER use overflow-x-scroll → use overflow-x-auto
```

### SQL (Dialect)

```sql
-- @stage syntax: dots for path, auto-detect format
SELECT * FROM @stage1.folder.file.csv;

-- Full qualified when crossing schemas
SELECT * FROM @silver.stage1.data.parquet;

-- ML functions: Nova-style DDL
CREATE ML_MODEL model_name TYPE = FORECAST
    INPUT = (SELECT ...) TIMESTAMP = 'col' TARGET = 'col';

-- AI functions: convenience wrappers
SELECT AI_SENTIMENT(text) FROM table;
SELECT AI_SUMMARIZE(text, 100) FROM table;
```

---

## Doc Conventions

Every module doc follows this structure:

```markdown
# Module N: Feature Name

> One-line description.

---

## Concept/Overview
## Operations (with SQL examples)
## Nova UI (ASCII mockups)
## Implementation Notes (if architecture doc)
## Limitations
```

Every architecture doc follows this structure:

```markdown
# Architecture N: Component Name

> One-line description.

---

## Architecture (diagram)
## Implementation (Python/TypeScript code)
## Integration Points
## Configuration
```

---

## Key Data Flows

### SQL Execution (Web UI)

```
User types SQL in Monaco Editor
  → POST /api/v1/sql/execute
  → SQLPipeline.execute(sql, context)
    → DialectParser.parse(sql) → CommandType
    → DialectTranslator.translate(parsed) → StarRocks SQL
    → SQLCredentialInjector.inject(sql) → final SQL
  → StarRocks.execute(final_sql) → {columns, rows}
  → NOVA_SYSTEM.AUDIT.LOG → insert audit record
  → Return SQLResponse to frontend
```

### SQL Execution (MySQL Proxy)

```
MySQL client connects to port 4406
  → NovaMySQLProxy authenticates against StarRocks
  → COM_QUERY received
  → SQLPipeline.rewrite(sql, context) → final SQL
  → StarRocks.execute(final_sql) → result set
  → MySQL protocol response to client
  → NOVA_SYSTEM.AUDIT.LOG → insert audit record
```

### Stage File Access

```
User: SELECT * FROM @stage1.data.csv
  → Parser: STAGE_QUERY detected
  → Translator: @stage1.data.csv → FILES(...)
    → Find stage in NOVA_SYSTEM.CONFIG.STAGES
    → Get storage_connection name
    → Load config from nova.yaml
    → Generate FILES() params via provider.get_files_params()
  → Inject remaining creds
  → Execute on StarRocks
```

---

## NOVA_SYSTEM Schema Map

```
NOVA_SYSTEM
├── CONFIG                      ← CRUD (Primary Key tables)
│   ├── STAGES                  ← Stage definitions
│   ├── PINNED_QUERIES          ← Saved queries
│   ├── USER_PREFERENCES        ← UI settings
│   ├── AI_PROVIDERS            ← LLM provider connections (OpenAI, Anthropic, etc.)
│   ├── AI_MODELS               ← LLM/Embedding models per provider
│   ├── OBJECT_TAGS             ← Tag metadata
│   ├── DASHBOARDS              ← Dashboard definitions
│   └── DASHBOARD_WIDGETS       ← Dashboard widgets
│
├── ML                          ← Model metadata
│   ├── MODELS                  ← ML model registry
│   ├── MODEL_VERSIONS          ← Version history
│   └── MODEL_ALIASES           ← Named aliases
│
├── AUDIT                       ← Append-only analytics
│   └── LOG                     ← Every action logged
│
├── STAGE                       ← Stage analytics
│   └── FILE_MANIFEST           ← File inventory
│
├── LINEAGE                     ← Data provenance
│   └── LOAD_HISTORY            ← Load job history
│
├── QUALITY                     ← Data health
│   └── TABLE_STATS             ← Table snapshots
│
└── USAGE                       ← Query analytics
    └── QUERY_STATS             ← Per-user daily aggregation
```

---

## What NOT to Do

| ❌ Don't | ✅ Do Instead |
|----------|--------------|
| Hardcode S3/MinIO in UI | Use "stage name" or "storage connection name" |
| Store credentials in DB | Store in nova.yaml + .env |
| Create SQLite/PG database | Use NOVA_SYSTEM for all state |
| Create separate user table | Authenticate against StarRocks |
| Use `echo/cat` for file I/O | Use `write_file`/`read_file` tools |
| Use `grep/rg/find` in terminal | Use `search_files` tool |
| Use `sed/awk` for edits | Use `patch` tool |
| Invent StarRocks SQL syntax | Check docs.starrocks.io first |
| Break @stage → FILES() rewrite | Always preserve the dialect pipeline |
| Ship without audit logging | Every action → NOVA_SYSTEM.AUDIT.LOG |
| Hardcode heights in CSS | Use flex-1 + min-h-0 |
| Use overflow-x-scroll | Use overflow-x-auto |
| Write Java/Spring Boot | Python only (FastAPI) |

---

## Dependencies

### Backend

```
fastapi>=0.115          # Web framework
uvicorn[standard]>=0.34 # ASGI server
sqlalchemy>=2.0         # ORM (for type hints, not as primary DB)
aiomysql>=0.2           # Async MySQL
pymysql>=1.1            # Sync MySQL (StarRocks connector)
boto3>=1.38             # S3/MinIO client
cryptography>=44        # Credential encryption
pydantic>=2.11          # Validation
pydantic-settings>=2.9  # Config from env
python-multipart>=0.0.20 # File uploads
httpx>=0.28             # HTTP client
```

### Frontend

```
next                    # React framework
@monaco-editor/react    # SQL editor
shadcn/ui + radix       # UI components
tailwindcss             # Styling
zustand                 # State management
tanstack-table          # Data tables
recharts                # Charts
lucide-react            # Icons
```

---

## Version History

| Date | Change |
|------|--------|
| 2026-06-18 | Initial docs: 35 files, 7,254 lines |
