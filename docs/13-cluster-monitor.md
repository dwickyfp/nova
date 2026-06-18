# Module 13: Cluster Monitor

> Cluster health, node status, metrics, query history, and system views.

---

## Node Management

### BE / CN Nodes

| Action | SQL |
|--------|-----|
| List backends | `SHOW BACKENDS` |
| List compute nodes | `SHOW COMPUTE NODES` |
| Add backend | `ALTER SYSTEM ADD BACKEND ...` |
| Drop backend | `ALTER SYSTEM DROP BACKEND ...` |
| Decommission | `ALTER SYSTEM DECOMMISSION BACKEND ...` |

### FE Nodes

| Action | SQL |
|--------|-----|
| List frontends | `SHOW FRONTENDS` |
| Add follower | `ALTER SYSTEM ADD FOLLOWER ...` |
| Add observer | `ALTER SYSTEM ADD OBSERVER ...` |
| Drop follower/observer | `ALTER SYSTEM DROP FOLLOWER/OBSERVER ...` |

---

## Metrics & Health

### Health Check

```
GET /api/health
GET /metrics?type=json
GET /api/v2/cluster_summary
```

### Key Metrics

| Metric | Description |
|--------|-------------|
| `tablet_num` | Number of tablets (shared-data, v4.1) |
| `MemtableIOSpeed` | Memtable I/O speed (v4.1) |
| `staros_shard_count` | StarOS shard count (v4.1) |
| `resource_group_running_queries` | Running queries per resource group |
| `resource_group_total_queries` | Total queries per resource group |

---

## Query History

### System Views

| View | Content |
|------|---------|
| `information_schema.loads` | Load job history |
| `information_schema.routine_load_jobs` | Routine Load jobs |
| `information_schema.stream_loads` | Stream Load history |
| `information_schema.pipe_files` | Pipe file ingestion status |
| `information_schema.pipes` | Pipe status |
| `information_schema.tasks` | Async tasks |
| `information_schema.task_runs` | Task run history |
| `information_schema.analyze_status` | ANALYZE job status |
| `information_schema.warehouse_queries` | Queries per warehouse |

### HTTP APIs

| Endpoint | Description |
|----------|-------------|
| `GET /api/profile?query_id={}` | Query execution profile |
| `GET /api/query_detail` | Query details |
| `GET /api/v2/query_detail` | Query details v2 |
| `GET /api/v2/backend` | Backend list |
| `GET /api/v2/computeNode` | Compute node list |
| `GET /api/v2/cluster_summary` | Cluster summary |
| `GET /api/show_data?db={}` | Database size |

---

## System Views (information_schema)

### Metadata Views

| View | Content |
|------|---------|
| `schemata` | All databases |
| `tables` | All tables (row count, size, engine, etc.) |
| `columns` | All columns (type, nullable, default, comment) |
| `views` | User-defined views |
| `materialized_views` | MV definitions and status |
| `partitions_meta` | Partition metadata |
| `tables_config` | Table configuration |

### Configuration Views

| View | Content |
|------|---------|
| `global_variables` | Global variables |
| `session_variables` | Current session variables |
| `verbose_session_variables` | Variables with defaults and changes |
| `be_configs` | BE configuration parameters |

### Runtime Views

| View | Content |
|------|---------|
| `fe_metrics` | FE metrics |
| `fe_threads` | FE thread state (v4.1) |
| `be_metrics` | BE metrics |
| `be_tablets` | Tablet distribution |
| `be_compactions` | Compaction status |
| `be_cloud_native_compactions` | Cloud-native compaction (shared-data) |
| `be_txns` | Transaction status |
| `be_threads` | BE thread state |
| `be_logs` | BE logs |
| `be_bvars` | bRPC statistics |
| `fe_tablet_schedules` | Tablet scheduling tasks |
| `warehouse_metrics` | Warehouse metrics |
| `character_sets` | Available character sets |
| `collations` | Available collations |

---

## Cluster Monitor UI

### Dashboard

```
┌─ Cluster Dashboard ─────────────────────────────────────┐
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ FE Nodes │  │ BE Nodes │  │ Queries  │              │
│  │ 3/3 🟢  │  │ 5/5 🟢  │  │ 142/min  │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Storage  │  │ Tablets  │  │ Memory   │              │
│  │ 2.3 TB   │  │ 12,847   │  │ 67%      │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│                                                          │
│  ── Node Status ──                                       │
│  FE-0  🟢 Leader    192.168.1.10:9010                   │
│  FE-1  🟢 Follower  192.168.1.11:9010                   │
│  FE-2  🟢 Observer  192.168.1.12:9010                   │
│  BE-0  🟢 Alive     192.168.1.20:9050  245 GB          │
│  BE-1  🟢 Alive     192.168.1.21:9050  238 GB          │
│  ...                                                     │
└──────────────────────────────────────────────────────────┘
```

### Query History

```
┌─ Query History ─────────────────────────────────────────┐
│                                                          │
│  [Filter: All ▼] [Status: All ▼] [Time: Last 24h ▼]   │
│                                                          │
│  Query ID    User     Database   Status  Time    Rows   │
│  abc-123     admin    DATALAKE   ✅      2.3s    1,420  │
│  def-456     analyst  ANALYTICS  ✅      0.8s    520    │
│  ghi-789     etl      DATALAKE   ❌      —       —      │
│    Error: table not found                                │
│  jkl-012     admin    DATALAKE   ✅      45.2s   1.2M   │
└──────────────────────────────────────────────────────────┘
```
