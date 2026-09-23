# 06 — `NOVA_SYSTEM` Tables

> Nova's single metadata database. Every persistent state lives here — no SQLite, no PostgreSQL — and it is queryable from the SQL Workspace like any other StarRocks database.

Sources: `docker/init-nova.sql`, `backend/app/common/nova_system.py`, service modules that read/write it.

---

## Concept

`docker/init-nova.sql` creates `NOVA_SYSTEM` and all its tables on first startup. StarRocks has `catalog.database.table` namespaces with no nested schemas, so Nova keeps a **flat `<group>_<table>` naming convention** to preserve logical grouping (`init-nova.sql:52`).

```
NOVA_SYSTEM
├── CONFIG_*        ← user-facing config (Primary Key tables, CRUD)
├── AUDIT_LOG       ← every action (Duplicate Key, append-only, partitioned by month)
├── ML_*            ← model registry + versions + aliases (Duplicate Key)
├── STAGE_FILE_MANIFEST   ← file inventory (Duplicate Key)
├── LINEAGE_LOAD_HISTORY  ← load provenance (Duplicate Key, partitioned)
├── QUALITY_TABLE_STATS   ← table health snapshots (Duplicate Key)
└── USAGE_QUERY_STATS     ← query analytics (Duplicate Key, partitioned)
```

> **Important:** the historical `arch-06-nova-system-database.md` describes a `CONFIG` *schema* with `CONFIG.STAGES` etc. The actual implementation uses flat table names: `NOVA_SYSTEM.CONFIG_STAGES`. Both notations appear in older docs and code comments; **the flat name is the real one**. The `{group}_{table}` split is logical, not physical.

---

## Key model

| Suffix / group | Table type | Why |
|----------------|-----------|-----|
| `CONFIG_*` | Primary Key | Low-volume CRUD; supports `UPDATE`/`DELETE` via upsert. `enable_persistent_index=true`. |
| `ML_*`, `AUDIT_*`, analytics | Duplicate Key | Append-only; versioning/history is the point. |
| Partitioned tables | Range-partitioned by a timestamp | `AUDIT_LOG`, `LINEAGE_LOAD_HISTORY`, `USAGE_QUERY_STATS` have monthly partitions through 2026-12. |

---

## CONFIG tables

### `CONFIG_STAGES`

Stage definitions. This is what `@stage` resolves against.

| Column | Type | Notes |
|--------|------|-------|
| `id` | VARCHAR(64) | PK. |
| `name` | VARCHAR(128) | The `@name` users type. |
| `database_name` | VARCHAR(128) | Access scoping. |
| `schema_name` | VARCHAR(128) | Access scoping. |
| `storage_connection` | VARCHAR(128) | **Name** of a connection in `nova.yaml` — never a credential. |
| `base_prefix` | VARCHAR(512) | Path prefix; falls back to `{database}/{schema}/{stage}` when empty. |
| `created_at` | DATETIME | |
| `created_by` | VARCHAR(128) | |

This table is readable and writable from a worksheet. It contains no secret: the connection is referenced by name and the actual key lives in config.

### `CONFIG_PINNED_QUERIES`

Saved queries: `id` (PK), `user_name`, `name`, `sql_text`, `database_name`, `schema_name`, `is_shared`, `created_at`.

### `CONFIG_USER_PREFERENCES`

`user_name` + `pref_key` composite PK, `pref_value TEXT`, `updated_at`. Uses include `workspace.last_database`, `workspace.last_schema`, `workspace.last_role`, and the `__system__` / `setup_complete` marker.

### `CONFIG_WORKSPACE_ENTRIES`

Virtual workspace files: `id` (PK), `user_name`, `parent_path`, `name`, `entry_type`, `object_key`, `size_bytes`, `etag`, timestamps, soft-delete fields.

### `CONFIG_AI_PROVIDERS`

| Column | Notes |
|--------|-------|
| `id` | PK. |
| `name`, `type`, `endpoint` | Provider identity. |
| `api_key` | **Encrypted** ciphertext. Never returned as plaintext. |
| `default_params` | JSON text. |
| `is_active`, `created_at`, `created_by` | |

### `CONFIG_AI_MODELS`

`id` (PK), `provider_id`, `name`, `display_name`, `type` (`llm` or `embedding`), `max_tokens`, `default_params`, `is_active`, timestamps. Embedding models also record `logical_alias`, immutable `revision`, `dimensions`, `modality`, and `metric`. Existing tables receive those columns through an additive startup migration; new tables are created with them by `docker/init-nova.sql`.

### `CONFIG_MODEL_ALIASES`

Binds a `function_type` (`complete`, `sentiment`, …) to a provider + model. Includes `system_prompt`, `default_params`, `is_default`, `is_active`. One default per function type.

### `CONFIG_OBJECT_TAGS`

`object_type` + `object_name` + `tag_key` composite PK, `tag_value`, timestamps.

### `CONFIG_DASHBOARDS` / `CONFIG_DASHBOARD_WIDGETS`

Dashboard definitions (`id`, `name`, `description`, `is_shared`, …) and widgets (`dashboard_id`, `sql_text`, `chart_type`, `x_axis`, `y_axis`, position/size, `refresh_seconds`).

### `CONFIG_TASKS` / `CONFIG_TASK_EDGES` / `CONFIG_TASK_GRAPH_RUNS` / `CONFIG_TASK_RUNS`

Task-orchestration metadata. Created both by `init-nova.sql` and idempotently at runtime by `init_task_orchestration()` (`nova_system.py:151`), which also applies additive column migrations for late-arriving columns.

`CREATE TASK` is **intercepted by Nova and lowered to these tables**; the raw statement is never sent to StarRocks (`service.py:613`). The engine statement for a node is built later by the worker.

---

## AUDIT table

### `AUDIT_LOG`

Append-only, Duplicate Key `(log_id, query_id, event_type, event_time)`, partitioned monthly by `event_time`.

| Column | Notes |
|--------|-------|
| `log_id` | Auto-increment. |
| `query_id`, `event_type`, `event_time` | Keys. |
| `user_name`, `ip_address`, `client_ip`, `session_id` | Actor context. |
| `object_type`, `object_name`, `action` | What was touched. |
| `sql_text` | The statement **as the user wrote it**. |
| `rewritten_sql` | The **redacted** engine-bound statement (`***` values), or `NULL` for a pre-engine refusal. |
| `status` | `SUCCESS` / `ERROR`. |
| `error_message`, `duration_ms`, `rows_affected` | Outcomes. |
| `file_id`, `database_name`, `schema_name` | Context. |

Both `sql_text` and `rewritten_sql` must be credential-free. `QueryService` redacts before writing; `_audit_engine_result` writes `rewritten_sql=NULL` for a statement that never reached the engine.

---

## ML tables

### `ML_MODELS` (Duplicate Key `(model_id, model_type)`)

`model_id`, `model_type`, `model_name`, `target_column`, `feature_columns` (JSON), `hyperparameters` (JSON), `training_sql` (**redacted**), `database_name`, `schema_name`, timestamps, `created_by`.

### `ML_MODEL_VERSIONS` (Duplicate Key `(model_id, version)`)

`version`, `status`, `training_rows`, `metrics` (JSON), `model_binary` (base64 joblib), timestamps, `created_by`.

### `ML_MODEL_ALIASES` (Duplicate Key `(alias_name, model_id)`)

`alias_name`, `model_id`, `version`, timestamps. Prediction resolves through this table.

---

## Analytics tables

### `STAGE_FILE_MANIFEST`

`file_id` (auto-inc), `stage_id`, `file_path`, `file_name`, `file_size`, `file_format`, `uploaded_at`, `uploaded_by`, `checksum`. Duplicate Key.

### `LINEAGE_LOAD_HISTORY`

`load_id` (auto-inc), `target_table`, `started_at`, `stage_id`, `file_path`, `file_format`, `rows_loaded`, `rows_rejected`, `load_time_ms`, `status`, `error_message`, `completed_at`. Partitioned monthly.

### `QUALITY_TABLE_STATS`

`stat_id` (auto-inc), `table_name`, `database_name`, `schema_name`, `row_count`, `data_size_bytes`, `index_size_bytes`, `partition_count`, `collected_at`.

### `USAGE_QUERY_STATS`

`stat_id` (auto-inc), `user_name`, `started_at`, `query_id`, `database_name`, `sql_text`, `query_type`, `execution_time_ms`, `rows_scanned`, `rows_returned`, `memory_used_bytes`, `cpu_time_ms`, `spill_bytes`, `completed_at`. Partitioned monthly.

---

## Querying `NOVA_SYSTEM`

The tables above are ordinary StarRocks tables and can be queried from the Workspace:

```sql
SELECT name, database_name, schema_name, storage_connection, base_prefix
FROM NOVA_SYSTEM.CONFIG_STAGES
ORDER BY database_name, schema_name, name;

SELECT event_time, user_name, object_name, status, duration_ms, sql_text, rewritten_sql
FROM NOVA_SYSTEM.AUDIT_LOG
WHERE event_type = 'query' AND status = 'ERROR'
ORDER BY event_time DESC
LIMIT 50;

SELECT model_name, model_type, target_column, created_at
FROM NOVA_SYSTEM.ML_MODELS
ORDER BY created_at DESC;
```

The `QueryService` history endpoints read `AUDIT_LOG` through the system connection, filtered to the requesting user (`service.py:725`).

---

## Invariant: no credentials in `NOVA_SYSTEM`

`AGENTS.md` §2 lists `NOVA_SYSTEM` tables as a never-store-credentials location. The schema reflects this:

- `CONFIG_STAGES.storage_connection` is a connection **name**, not a key.
- `CONFIG_AI_PROVIDERS.api_key` is encrypted ciphertext, not plaintext.
- `ML_MODELS.training_sql` is the redacted form.
- `AUDIT_LOG.rewritten_sql` is redacted; `sql_text` is the user's own input.
- Task tables explicitly define no credential column (`init-nova.sql:181`).

`tests/unit/test_ml_engine_training_sql.py::test_persistence_stores_the_redacted_form` asserts the persisted `training_sql` carries no credential value.

---

## Limitations

- `NOVA_SYSTEM` is a normal StarRocks database; a sufficiently privileged user can write to it directly, bypassing Nova's services. Nova's guard blocks the destructive operations it knows about but does not make `NOVA_SYSTEM` read-only.
- `CONFIG_AI_PROVIDERS.api_key` is readable as ciphertext through the system connection; the encryption key lives in backend config, so a direct-DB reader without that key cannot recover the plaintext.
- The docs' older `CONFIG.STAGES`-style notation is not the physical schema. Use flat names.

---

## Verification

| Claim | Source |
|-------|--------|
| Table DDL, keys, partitions | `docker/init-nova.sql` |
| Runtime-created task tables + migrations | `backend/app/common/nova_system.py` |
| `CREATE TASK` never sent to StarRocks | `tests/unit/test_query_create_task_interception.py` |
| Stored training SQL is redacted | `tests/unit/test_ml_engine_training_sql.py` |
| History reads `AUDIT_LOG` per user | `backend/app/modules/query/service.py::get_history` |
