# Module 17: Query Profile

> Visualize and analyze query execution profiles.

---

## Features

### EXPLAIN

```sql
-- Standard EXPLAIN
EXPLAIN SELECT * FROM orders WHERE dt > '2026-01-01';

-- Verbose EXPLAIN
EXPLAIN VERBOSE SELECT ...;

-- Logical EXPLAIN
EXPLAIN LOGICAL SELECT ...;

-- EXPLAIN ANALYZE (after execution)
EXPLAIN ANALYZE SELECT ...;

-- EXPLAIN for INSERT (v4.1)
EXPLAIN INSERT INTO t SELECT * FROM s;

-- EXPLAIN for query queue (v4.1)
EXPLAIN ANALYZE SELECT ...;  -- shows queue time
```

### Query Profile

```sql
-- Get profile by query ID
-- Via HTTP: GET /api/profile?query_id={query_id}
-- Via SQL: ANALYZE PROFILE FOR <query_id>

-- last_query_id in ANALYZE PROFILE (v4.1)
ANALYZE PROFILE FOR <query_id>;
ANALYZE PROFILE FOR LAST;  -- last query in session
```

---

## Profile Visualization

### Execution Graph

```
┌─ Query Profile: abc-123 ────────────────────────────────┐
│                                                          │
│  Execution Time: 2.3s                                    │
│  Total: 1,420 rows scanned → 520 rows returned           │
│                                                          │
│  ┌─────────────────────────────────────┐                │
│  │         AGGREGATE (Final)           │                │
│  │         Time: 0.01s                 │                │
│  │         Rows: 1                     │                │
│  └──────────────┬──────────────────────┘                │
│                 │                                         │
│  ┌──────────────▼──────────────────────┐                │
│  │         EXCHANGE (Gather)           │                │
│  │         Time: 0.02s                 │                │
│  │         Rows: 2                     │                │
│  └──────────────┬──────────────────────┘                │
│                 │                                         │
│  ┌──────────────▼──────────────────────┐                │
│  │         AGGREGATE (Update)          │                │
│  │         Time: 0.05s                 │                │
│  │         Rows: 2                     │                │
│  └──────────────┬──────────────────────┘                │
│                 │                                         │
│  ┌──────────────▼──────────────────────┐                │
│  │         SCAN (orders)               │                │
│  │         Time: 2.1s                  │                │
│  │         Rows: 1,420                 │                │
│  │         Predicates: dt > '2026-01-01' │             │
│  │         Partitions: 3/12            │                │
│  │         Tablets: 15/48              │                │
│  └─────────────────────────────────────┘                │
│                                                          │
│  ── Operators ──                                         │
│  [AGG] 0.01s  1 row     █████                          │
│  [EXC] 0.02s  2 rows    ██████                         │
│  [AGG] 0.05s  2 rows    ███████                        │
│  [SCN] 2.1s   1,420 rows █████████████████████████████  │
└──────────────────────────────────────────────────────────┘
```

### Built-in UI Functions (v4.1)

```sql
-- Format query
SELECT query_id(), format_sql(query);

-- Analyze profile
ANALYZE PROFILE FOR <query_id>;

-- Built-in UI functions for profile analysis
SELECT * FROM information_schema.be_metrics;
```

---

## Query History with Profiles

```
┌─ Query History ─────────────────────────────────────────┐
│                                                          │
│  Query ID    User    Status  Time   Rows   Profile      │
│  abc-123     admin   ✅     2.3s   520    [View]       │
│  def-456     analyst ✅     0.8s   1,200  [View]       │
│  ghi-789     etl     ❌     —      —      [View]       │
│  jkl-012     admin   ✅     45.2s  1.2M   [View]       │
└──────────────────────────────────────────────────────────┘

Click [View] → Opens profile visualization for that query.
```
