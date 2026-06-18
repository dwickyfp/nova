# Nova — StarRocks Console

> **Version:** 0.1.0 (Planning)
> **Date:** June 18, 2026
> **Engine:** StarRocks 4.1.x

---

## Vision

Nova is a Snowflake-grade management console for StarRocks. It provides a unified web interface for SQL exploration, data management, storage stage abstraction, task orchestration, machine learning, and cluster administration — all backed by StarRocks as the query engine.

## Core Principles

1. **Storage-Agnostic** — No S3/MinIO/Azure/GCS references in user-facing UI. All storage operations are abstracted behind "Stages."
2. **Credential-Invisible** — Users never see or type storage credentials. Credentials are in `nova.yaml`, not in database.
3. **SQL-Native** — Everything is powered by StarRocks SQL. Nova is a UI layer on top, not a separate engine.
4. **@stage Syntax** — Custom SQL dialect (`@stage_name.file.csv`) that translates to StarRocks `FILES()` internally.
5. **Single Database** — All persistent state in StarRocks `NOVA_SYSTEM`. No SQLite, no PostgreSQL.
6. **Module-Based** — Each feature area is an independent module with clear boundaries.
7. **Snowflake Parity** — Where StarRocks supports it, Nova provides the UI. Where it doesn't, Nova builds the layer.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Nova Frontend                         │
│         (React / Next.js / Monaco Editor)                │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    Nova Backend (FastAPI)                 │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────┐ │
│  │ SQL Dialect │ │ Storage      │ │ MySQL Protocol   │ │
│  │ Engine      │ │ Providers    │ │ Proxy (:4406)    │ │
│  └─────────────┘ └──────────────┘ └──────────────────┘ │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              StarRocks Cluster (4.1.x)                   │
│  User DBs: DATALAKE, ANALYTICS, ...                      │
│  System DB: NOVA_SYSTEM (CONFIG, AUDIT, STAGE,           │
│             LINEAGE, QUALITY, USAGE, ML)                  │
└──────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  nova.yaml + .env (storage creds, StarRocks connection)  │
└─────────────────────────────────────────────────────────┘
```

---

## Module Overview (27 Feature Modules + 7 Architecture Docs)

### Feature Modules

| # | Module | Description |
|---|--------|-------------|
| 02 | SQL Worksheet | Editor, execution, @stage syntax, autocomplete |
| 03 | Catalog Explorer | Database/schema/table/column browser |
| 04 | Stage Manager | @stage syntax, file browser, upload/download |
| 05 | Table Manager | Create/alter/drop, partitions, indexes, distribution |
| 06 | View Manager | Views and materialized views (PCT/INCREMENTAL) |
| 07 | Function Manager | Built-in, UDF, SQL UDF, AI functions |
| 08 | Task Manager | SUBMIT TASK, ALTER TASK, task runs |
| 09 | Pipe Manager | Continuous ingestion pipes |
| 10 | External Catalogs | Hive, Iceberg, Paimon, JDBC, Hudi, Delta |
| 11 | User & Access Control | Users, roles, grants, privileges |
| 12 | Resource Groups | CPU isolation, query queues, warehouses |
| 13 | Cluster Monitor | Nodes, metrics, health, query history |
| 14 | Storage Connections | Read-only view of nova.yaml connections |
| 15 | Data Loading | Stream Load, Broker Load, INSERT INTO FILES |
| 16 | Data Export | INSERT INTO FILES (unload) |
| 17 | Query Profile | EXPLAIN, query profile visualization |
| 18 | Authentication | StarRocks native auth, RBAC, sessions |
| 19 | Machine Learning | LLM functions, ML models, forecast, classify |
| 20 | Data Governance | Masking, row access, tagging, lineage |
| 21 | Backup & Recovery | Snapshots, restore, recycle bin |
| 22 | Variables & Settings | Session/global vars, password policies |
| 23 | Dashboards | Charts, widgets, auto-refresh |
| 24 | Advanced Indexes | Inverted index, full-text search |
| 25 | Storage Volumes | Shared-data mode volumes |
| 26 | Compaction Manager | Compaction monitoring, manual trigger |
| 27 | Data Sharing | Shared views, shared stages |

### Architecture Docs

| # | Doc | Description |
|---|-----|-------------|
| A1 | SQL Dialect Engine | @stage parser, translator, credential injector |
| A2 | Storage Provider Layer | S3/Azure/GCS abstraction, config-based |
| A3 | Backend Architecture | FastAPI structure, API endpoints |
| A4 | Database Model | NOVA_SYSTEM schema (all tables) |
| A5 | Frontend Architecture | React/Next.js, Monaco, components |
| A6 | NOVA_SYSTEM Database | Full DDL, query examples, lifecycle |
| A7 | MySQL Protocol Proxy | MySQL endpoint, @stage rewrite for clients |

### Gap Analysis

| Doc | Description |
|-----|-------------|
| [Gap Analysis](./gap-analysis.md) | 32 missing features, prioritized |
