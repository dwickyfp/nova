# Module 12: Resource Groups

> CPU isolation, memory limits, query queues, and warehouse management.

---

## Resource Group Concepts

| Concept | Description |
|---------|-------------|
| **Resource Group** | Named group with CPU/memory quotas |
| **Classifier** | Rule to match queries to resource groups |
| **Warehouse** | Compute unit (shared-data mode) |
| **Query Queue** | Queue for queries exceeding concurrency limits |

---

## Resource Group Attributes

| Attribute | Description | Default |
|-----------|-------------|---------|
| `cpu_core_limit` | CPU cores (absolute) | 0 (no limit) |
| `mem_limit` | Memory percentage (0-1) | 0 (no limit) |
| `concurrency_limit` | Max concurrent queries | 0 (no limit) |
| `big_query_cpu_second_limit` | CPU time limit per query | 0 |
| `big_query_scan_rows_limit` | Scan rows limit per query | 0 |
| `big_query_mem_limit` | Memory limit per query | 0 |
| `spill_mem_limit_threshold` | Spill to disk threshold | 0 |
| `warehouses` (v4.1) | Associated warehouses | — |
| `cpu_weight_percent` (v4.1) | CPU weight percentage | 0 |
| `exclusive_cpu_weight` (v4.1) | Exclusive CPU weight | 0 |
| `exclusive_cpu_cores` | Hard CPU isolation (cores) | 0 |

---

## Classifier Rules

| Attribute | Description |
|-----------|-------------|
| `user` | Username match |
| `role` | Role match |
| `query_type` | Query type: `select`, `insert` |
| `source_ip` | Client IP match |

---

## Operations

| Action | SQL |
|--------|-----|
| Create resource group | `CREATE RESOURCE GROUP <name> WITH (...)` |
| Alter resource group | `ALTER RESOURCE GROUP <name> SET (...)` |
| Drop resource group | `DROP RESOURCE GROUP <name>` |
| Show resource groups | `SHOW RESOURCE GROUPS` |
| Add classifier | `ALTER RESOURCE GROUP <name> ADD (...)` |
| Remove classifier | `ALTER RESOURCE GROUP <name> DROP (...)` |
| Show usage | `SHOW USAGE RESOURCE GROUPS` |

---

## Query Queue (v4.1)

Enabled by default (`query_queue_v2`). When a query exceeds concurrency limit:

1. Query enters queue
2. Queue monitors resource availability
3. Query executes when resources available
4. Timeout if wait exceeds `query_queue_timeout`

---

## Resource Group UI

```
┌─ Resource Groups ───────────────────────────────────────┐
│                                                          │
│  [+ Create Resource Group]                               │
│                                                          │
│  Name         CPU Limit   Mem Limit   Concurrency        │
│  default_wg   auto        auto        unlimited          │
│  default_mv   auto        auto        unlimited          │
│  etl_group    4 cores     50%         10                 │
│  adhoc_group  2 cores     30%         5                  │
│                                                          │
│  ── Usage ──                                             │
│  etl_group:   3/10 running, 0 queued                     │
│  adhoc_group: 1/5 running, 2 queued                      │
└──────────────────────────────────────────────────────────┘
```
