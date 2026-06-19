<div align="center">
  <img src="frontend/public/images/nova-mark.svg" alt="Nova Phoenix" width="112" />

  # Nova

  ### The data warehouse and AI platform powered by StarRocks

  Run analytics, manage data, work with files, and build AI-powered workflows
  through one fast, secure, and storage-agnostic platform.

  [![StarRocks](https://img.shields.io/badge/Powered_by-StarRocks_4.1-D04738?style=for-the-badge)](https://www.starrocks.io/)
  [![Backend](https://img.shields.io/badge/Backend-FastAPI-11161D?style=for-the-badge&logo=fastapi&logoColor=white)](backend/)
  [![Frontend](https://img.shields.io/badge/Frontend-React_19-11161D?style=for-the-badge&logo=react&logoColor=white)](frontend/)
  [![Python](https://img.shields.io/badge/Python-3.11+-11161D?style=for-the-badge&logo=python&logoColor=white)](backend/pyproject.toml)

  [Quick start](#quick-start) · [Architecture](#architecture) · [Documentation](#documentation)
</div>

---

## One platform. From raw files to intelligent products.

Nova brings a Snowflake-grade management experience to StarRocks without hiding
the engine that makes it fast.

Instead of stitching together separate tools for SQL, object storage, access
control, observability, ML, and AI, Nova exposes them through a consistent web
console, API, SQL dialect, and MySQL-compatible interface.

```sql
-- Query a staged file without exposing its storage provider or credentials.
SELECT *
FROM @production.sales.orders.parquet
WHERE order_date >= CURRENT_DATE - INTERVAL 7 DAY;
```

Nova resolves the stage, detects the file format, injects credentials securely,
translates the query into StarRocks SQL, and records the action in the audit log.

## Why Nova?

| Capability | What Nova provides |
|---|---|
| **Fast analytics** | StarRocks-powered real-time OLAP and interactive SQL |
| **Unified workspace** | SQL worksheet, catalog exploration, data management, and monitoring |
| **Stage-native files** | Storage-agnostic file access through the sacred `@stage` syntax |
| **Native security** | StarRocks users and grants remain the source of truth |
| **Credential isolation** | Storage and user credentials never appear in UI state, API responses, logs, or system tables |
| **MySQL compatibility** | Connect existing MySQL clients through Nova's protocol proxy |
| **Built-in intelligence** | ML workflows and SQL-native AI functions such as `AI_COMPLETE` and `AI_SENTIMENT` |
| **Operational visibility** | Audit history, query analytics, lineage, quality statistics, and cluster insights |

## The Nova experience

### Query everything

Use a Monaco-powered SQL workspace for tables, views, staged files, external
catalogs, ML models, and AI functions.

```sql
SELECT
    customer_id,
    AI_SENTIMENT(review_text) AS sentiment
FROM analytics.customer_reviews;
```

### Treat files like data

Stages provide one stable abstraction over S3-compatible storage, Azure, GCS,
and future providers. Users work with stage names—not endpoints, buckets, or
credentials.

```sql
SELECT * FROM @raw.events.2026.06.18.json;
```

### Bring your existing tools

Nova's MySQL protocol proxy lets compatible SQL clients connect on port `4406`
while preserving Nova's dialect translation, access checks, and audit trail.

### Build intelligence into SQL

Nova extends the warehouse with approachable ML and AI primitives:

```sql
CREATE ML_MODEL revenue_forecast TYPE = FORECAST
    INPUT = (SELECT event_time, revenue FROM analytics.daily_revenue)
    TIMESTAMP = 'event_time'
    TARGET = 'revenue';
```

## Architecture

```mermaid
flowchart LR
    UI["Nova Web Console<br/>React + Monaco"]
    CLIENT["MySQL Clients<br/>Port 4406"]
    API["Nova API<br/>FastAPI :8000"]
    PROXY["MySQL Protocol Proxy"]
    PIPELINE["SQL Dialect Engine<br/>parse · translate · secure"]
    SR["StarRocks 4.1<br/>compute · storage · auth"]
    STAGE["Stage Providers<br/>storage-agnostic files"]
    SYSTEM["NOVA_SYSTEM<br/>config · audit · lineage · usage"]

    UI --> API
    CLIENT --> PROXY
    API --> PIPELINE
    PROXY --> PIPELINE
    PIPELINE --> SR
    PIPELINE --> STAGE
    API --> SYSTEM
    PROXY --> SYSTEM
```

### Core design principles

1. **StarRocks is the source of truth.** Nova does not maintain a separate user database.
2. **Credentials are invisible.** Secrets stay in configuration or encrypted session memory.
3. **The UI is storage-agnostic.** Provider-specific infrastructure never leaks into the user experience.
4. **`@stage` is a first-class SQL primitive.** File access always flows through the dialect engine.
5. **Persistent state belongs in `NOVA_SYSTEM`.** Nova does not introduce SQLite or PostgreSQL.
6. **Every meaningful action is auditable.**
7. **`ACCOUNTADMIN` is immutable.** The system's highest-privilege role cannot be dropped or revoked.

## Quick start

### Prerequisites

- Docker with Docker Compose
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js with [pnpm](https://pnpm.io/)
- A MySQL client for direct engine access

### 1. Start the engine

```bash
cd docker
cp .env.example .env
docker compose -f docker-compose-engine.yml up -d
```

Verify the infrastructure:

```bash
docker compose -f docker-compose-engine.yml ps
curl http://localhost:8030/api/health
```

### 2. Start the backend

```bash
cd backend
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Backend endpoints:

- API: [http://localhost:8000](http://localhost:8000)
- OpenAPI: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health: [http://localhost:8000/health](http://localhost:8000/health)

### 3. Start the frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Open [http://localhost:5173](http://localhost:5173).

### 4. Connect directly to StarRocks

```bash
mysql -h 127.0.0.1 -P 9030 -u nova_admin -p
```

The development password is `nova`. Change it after the first login.

> Never expose the passwordless StarRocks `root` user outside the internal
> Docker network.

## Local services

| Service | Address | Purpose |
|---|---|---|
| Nova Web | `localhost:5173` | React application |
| Nova API | `localhost:8000` | FastAPI backend |
| Nova MySQL Proxy | `localhost:4406` | Nova-aware MySQL protocol |
| StarRocks FE HTTP | `localhost:8030` | Engine API and health |
| StarRocks MySQL | `localhost:9030` | Direct development connection |
| StarRocks BE HTTP | `localhost:8040` | Backend node API |
| Object Storage API | `localhost:9000` | Local stage storage |
| Object Storage Console | `localhost:9001` | Local storage administration |
| Redis | `localhost:6379` | Sessions and query cache |

## Technology

| Layer | Stack |
|---|---|
| Query engine | StarRocks 4.1 |
| Backend | Python 3.11, FastAPI, Pydantic, async MySQL |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, shadcn/ui |
| SQL workspace | Monaco Editor |
| State and data | Zustand, TanStack Query, TanStack Router |
| Infrastructure | Docker Compose, Redis, S3-compatible local storage |
| Testing | Pytest, Vitest, Playwright |

## Project structure

```text
nova/
├── backend/              # FastAPI application and domain modules
│   ├── app/
│   │   ├── core/         # Configuration, security, database, Redis
│   │   ├── modules/      # Auth, query, objects, stages, users, AI/ML
│   │   └── common/       # Shared NOVA_SYSTEM infrastructure
│   └── tests/
├── frontend/             # React application
│   ├── public/images/    # Nova brand assets
│   └── src/              # Routes, features, components, state
├── docker/               # StarRocks and local infrastructure
├── docs/                 # Product and architecture specifications
├── AGENTS.md             # Engineering rules for AI agents
└── README.md
```

## Documentation

The [`docs/`](docs/) directory contains the detailed product specification:

- **27 feature modules** covering SQL, stages, governance, ML, dashboards,
  cluster operations, backup, indexes, and data sharing.
- **7 architecture documents** for the SQL dialect, storage providers,
  backend, frontend, system database, and MySQL proxy.
- **Gap analysis** tracking parity targets and remaining platform work.

Start with:

- [Platform overview](docs/01-overview.md)
- [SQL worksheet](docs/02-sql-worksheet.md)
- [Stage manager](docs/04-stage-manager.md)
- [Machine learning](docs/19-machine-learning.md)
- [SQL dialect architecture](docs/arch-01-sql-dialect-engine.md)
- [Backend architecture](docs/arch-03-backend-architecture.md)

## Development checks

Backend:

```bash
cd backend
uv run pytest
uv run ruff check .
```

Frontend:

```bash
cd frontend
pnpm lint
pnpm test
pnpm build
```

## Status

Nova is under active development. The architecture and product specifications
describe the target platform; individual modules may be implemented
incrementally.

## Roadmap

### Phase 1 — Foundation & Auth ✅
- [x] Docker infrastructure (StarRocks FE/BE, MinIO, Redis)
- [x] FastAPI modular monolith with asyncmy driver
- [x] JWT + Redis session management
- [x] StarRocks-native authentication (first-login setup wizard)
- [x] ACCOUNTADMIN role guard (immutable super user)
- [x] NOVA_SYSTEM database initialization (CONFIG, AUDIT schemas)
- [x] Frontend: Sign-in page (split-screen), auth guard, JWT cookie

### Phase 2 — Query Engine ✅
- [x] SQL execution via asyncmy per-user connections
- [x] @stage SQL dialect — parser, translator, credential injector
- [x] File format auto-detection (CSV, Parquet, JSON, ORC, Avro)
- [x] Query history with audit logging
- [x] Destructive SQL guard (DROP, TRUNCATE, DELETE confirmation)
- [x] Frontend: Monaco editor, results table, multi-tab, Ctrl+Enter

### Phase 3 — Object Browser ✅
- [x] Catalog → Database → Schema → Table/View/MV/Function tree
- [x] SHOW / DESCRIBE / INFORMATION_SCHEMA queries
- [x] Table column metadata with types
- [x] Schema browser in sidebar (DatabaseExplorer)
- [x] Frontend: Object browser panel, schema pre-loading

### Phase 4 — Stage & Storage ✅
- [x] Storage provider abstraction (S3/MinIO)
- [x] Stage CRUD registered in NOVA_SYSTEM.CONFIG.STAGES
- [x] File operations: browse, upload, download, delete
- [x] Schema-bound access control (RBAC on stage files)
- [x] CTAS from stage, export to stage
- [x] NOVA_DEMO sample database (customers, products, orders)

### Phase 5 — Administration 🔶
- [x] User management (create, alter, drop, password reset)
- [x] Role management (create, drop, grant, set default)
- [x] RBAC enforcement via StarRocks SHOW GRANTS
- [x] Workspace file persistence (save/load/query files)
- [ ] Resource groups (CPU/memory quotas, classifiers)
- [ ] Cluster monitor (FE/BE/CN nodes, health checks)
- [ ] Function manager (UDF: SQL, Java, Python)
- [ ] Task manager (SUBMIT TASK, scheduling)
- [ ] Pipe manager (continuous ingestion, AUTO_INGEST)

### Phase 6 — Advanced Features 🔶
- [x] AI Provider management (OpenAI, Anthropic, openai-compatible)
- [x] ML model registry (forecast, classify, anomaly detection)
- [x] AI SQL functions (AI_SENTIMENT, AI_COMPLETE, AI_TRANSLATE, etc.)
- [ ] External catalogs (Hive, Iceberg, Paimon, JDBC, Delta Lake)
- [ ] Dashboards (charts, widgets, auto-refresh)
- [ ] Backup & restore (snapshots, point-in-time, recycle bin)
- [ ] Data governance (masking policies, row access, tagging, lineage)
- [ ] Variables & settings (session/global browser, password policies)
- [ ] Compaction manager (manual trigger, score monitoring)
- [ ] Storage volumes (shared-data mode)
- [ ] Data sharing (shared views, shared stages, API endpoints)
- [ ] Data loading (Stream Load, Broker Load, Routine Load)
- [ ] Data export (INSERT INTO FILES, partitioned unload)

### Phase 7 — Frontend Pages 🔶
- [x] Auth: Sign-in, setup wizard, role switcher
- [x] SQL Workspace: Monaco editor, results panel, tabs
- [x] Sidebar: Object browser, workspace tree
- [x] Appearance: Light/Dark/System themes
- [ ] Stage Manager page
- [ ] User & Role Management page
- [ ] AI Providers page
- [ ] Cluster Monitor page
- [ ] Dashboards page
- [ ] Admin Settings page

### Phase 8 — MySQL Protocol Proxy
- [ ] TCP listener on port 4406
- [ ] MySQL wire protocol parser
- [ ] @stage dialect translation in proxy layer
- [ ] Credential injection for @stage queries
- [ ] Audit logging for proxy queries
- [ ] Connection pooling and session tracking

## License

Internal project — not yet licensed for distribution.

---

<div align="center">
  <img src="frontend/public/images/nova-mark.svg" alt="" width="42" />

  **Nova** — turn fast data into intelligent action.
</div>
