# Module 06: View Manager

> Manage standard views and materialized views.

---

## Standard Views

### Operations

| Action | SQL |
|--------|-----|
| Create | `CREATE VIEW <name> AS <query>` |
| Show | `SHOW VIEWS` / `SHOW FULL VIEWS` |
| Show DDL | `SHOW CREATE VIEW <name>` |
| Drop | `DROP VIEW [IF EXISTS] <name>` |

### View Detail Page

- Definition (SQL)
- Columns
- Dependent tables
- Dependent MVs

---

## Materialized Views

### MV Types

| Type | Refresh | Use Case |
|------|---------|----------|
| **Sync MV** | Synchronous (auto with base table) | Simple aggregates, low-latency |
| **Async MV** | Asynchronous (scheduled/manual) | Complex queries, large datasets |
| **Incremental MV** (v4.1) | Delta-only refresh | Iceberg append-only tables |

### Async MV Refresh Modes (v4.1)

| Mode | Description |
|------|-------------|
| `PCT` (default) | Partition Change Tracking — refreshes changed partitions |
| `INCREMENTAL` | Delta-only — processes only new data (Iceberg append-only) |
| `AUTO` | Tries INCREMENTAL, falls back to PCT if not applicable |

### Async MV Refresh Schedules

| Schedule | Syntax |
|----------|--------|
| On change | `REFRESH ASYNC` |
| Periodic | `REFRESH SCHEDULE START('...') EVERY(INTERVAL 1 HOUR)` |
| Manual | `REFRESH MANUAL` |
| Deferred | `REFRESH DEFERRED MANUAL` |

### MV Operations

| Action | UI | SQL |
|--------|-----|-----|
| Create MV | Form + SQL editor | `CREATE MATERIALIZED VIEW ...` |
| Show MVs | MV list tab | `SHOW MATERIALIZED VIEWS` |
| MV Status | Status badges (active/inactive) | `SELECT * FROM information_schema.materialized_views` |
| Refresh | [Refresh] button | `REFRESH MATERIALIZED VIEW <name>` |
| Alter MV | Properties, add/drop column (v4.1) | `ALTER MATERIALIZED VIEW ...` |
| Drop MV | [Delete] button | `DROP MATERIALIZED VIEW <name>` |
| Show DDL | DDL tab | `SHOW CREATE MATERIALIZED VIEW <name>` |

### MV Detail Page

| Tab | Content |
|-----|---------|
| **Overview** | Name, status (active/inactive), refresh mode, schedule, last refresh time |
| **Definition** | CREATE MATERIALIZED VIEW SQL |
| **Columns** | Column list with types |
| **Base Tables** | Source tables this MV depends on |
| **Refresh History** | `information_schema.task_runs` for MV refresh tasks |
| **Task Runs** | Partition-level refresh status, errors |

### MV Query Rewrite

StarRocks optimizer can automatically rewrite queries to use MVs when beneficial.

| Feature | Description |
|---------|-------------|
| Auto-rewrite | Optimizer detects when MV can answer a query |
| Rewrite control | `enable_query_rewrite` property per MV |
| Validation | `EXPLAIN <query>` shows if MV is used |

### Key MV Properties

| Property | Description |
|----------|-------------|
| `refresh_mode` | PCT / INCREMENTAL / AUTO (v4.1) |
| `mv_rewrite_staleness_second` | Allow stale MV for rewrite |
| `excluded_trigger_tables` | Tables whose changes don't trigger refresh |
| `partition_refresh_number` | Max partitions per refresh batch |
| `resource_group` | Resource group for refresh tasks |
| `enable_query_rewrite` | Enable/disable query rewrite |
| `auto_refresh_partitions_limit` | Max partitions to auto-refresh |
| `partition_ttl_number` | TTL for partitions |

### MV Limitations

- INCREMENTAL mode: Iceberg append-only tables only
- Query rewrite disabled for INCREMENTAL/AUTO MVs (v4.1.1)
- FORCE refresh and partition refresh rejected for INCREMENTAL/AUTO (v4.1.1)
