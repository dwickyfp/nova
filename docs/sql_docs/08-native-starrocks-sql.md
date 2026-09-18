# 08 — Native StarRocks SQL

> Plain StarRocks SQL that Nova passes through. Nova only intercepts specific statements; everything else is the engine's business.

Sources: `backend/app/modules/query/sql_pipeline.py`, `dialect/parser.py`, `dialect/ml_model.py`, `docker/init-nova.sql`.

> **Verification note.** Nova does not re-implement StarRocks SQL semantics. This document describes what Nova *does* with a statement (guard, pass through, rewrite, or intercept) and points at upstream behaviour. Claims about what StarRocks itself accepts and how it executes belong to the official 4.1.4 documentation and are marked **unverified** here unless Nova's own code or tests establish them. The research workstream's `10-starrocks-reference-comparison.md` is the authoritative cross-reference when it is available.

---

## What Nova intercepts vs. passes through

| Statement | Nova behaviour |
|-----------|----------------|
| `SELECT` / `WITH` / `SHOW` / `DESCRIBE` / `DESC` / `EXPLAIN` | Passed through; `@stage` translated if present. |
| `INSERT` / `UPDATE` / `DELETE` | Passed through; guarded and confirmation-gated. |
| `DROP` / `TRUNCATE` / `ALTER TABLE … DROP` | Guarded; requires `confirm_destructive`. |
| `CREATE TABLE` / `CREATE VIEW` / `CREATE DATABASE` / `CREATE FUNCTION` | Passed through. |
| `COPY INTO …` | Passed through; `@stage` translated. |
| `CREATE ML_MODEL … AS SELECT …` | **Intercepted** — handled by the Python ML engine; never sent to StarRocks. |
| `CREATE TASK …` | **Intercepted** — lowered to `CONFIG_TASK*` metadata; never sent to StarRocks. |
| `AI_*`, `ML_PREDICT` | StarRocks global UDFs; execute inside the engine. |
| `@@variables` | Passed through; never read as stages. |
| `LIST …` | No implementation; passed through and **rejected by the engine** (StarRocks has no `LIST` statement). |

Everything else is passed to the engine as the user wrote it, after the guard and redaction-no-op.

---

## Passing through is byte-identical

When a statement has no `@stage` reference and is not intercepted, `prepare_stage_sql` returns it unchanged (`sql_pipeline.py:119`), and `redact_sql_credentials` is a no-op on credential-free SQL. So a native statement is byte-for-byte what the user wrote. This is why Nova's own system tables can be queried with ordinary `SELECT` (see `06-nova-system-tables.md`).

Example — a native query that never touches Nova features:

```sql
SELECT c.city, COUNT(*) AS orders, SUM(o.total_amount) AS revenue
FROM NOVA_DEMO.orders o
JOIN NOVA_DEMO.customers c ON c.customer_id = o.customer_id
GROUP BY c.city
ORDER BY revenue DESC;
```

Nova applies the guard (no match), parses (no stage refs), and executes.

---

## Create and DDL statements

```sql
CREATE DATABASE IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.events (
  event_id BIGINT NOT NULL,
  event_time DATETIME NOT NULL,
  payload VARCHAR(512)
) PRIMARY KEY(event_id)
DISTRIBUTED BY HASH(event_id) BUCKETS 4
PROPERTIES("replication_num" = "1");
```

Nova passes these through. The `PRIMARY KEY` / `DISTRIBUTED BY` / `PROPERTIES` clauses are StarRocks 4.x semantics — **unverified** in this document; see upstream.

`DROP`, `TRUNCATE`, and `ALTER TABLE … DROP` are **destructive**: the API returns `needs_confirmation=true` unless `confirm_destructive=true` is sent (`sql_guard.py::DESTRUCTIVE_SQL_PATTERN`).

```json
{ "sql": "DROP TABLE analytics.events", "confirm_destructive": true }
```

A destructive statement also carries `destructive=true` in the response.

---

## `COPY INTO` — load and export

Nova rewrites `@stage` (both directions) and passes the rest through.

Load:

```sql
COPY INTO NOVA_DEMO.orders FROM @stage1.orders.csv
```

Export:

```sql
COPY INTO @stage1.exports FROM NOVA_DEMO.orders
```

`detect_command_type` classifies these as `STAGE_LOAD`/`STAGE_EXPORT` and the translation step rewrites the stage. `COPY INTO` returns no columns, which is the reason `success` is derived from the explicit `error` marker rather than result shape (`04`/`07`). Exact `COPY INTO` option syntax (properties, column mapping) is **unverified** here — upstream.

---

## `CREATE TASK` — Nova DDL, not engine SQL

`CREATE TASK` is **not** sent to StarRocks. It is parsed, validated, lowered to `NOVA_SYSTEM.CONFIG_TASK*`, and returns a metadata row (`service.py:613`). The worker builds the engine's `SUBMIT TASK` per node at run time.

```sql
CREATE TASK refresh_orders
  SCHEDULE = 'CRON 0 3 * * *'
  AS INSERT INTO analytics.order_facts SELECT * FROM NOVA_DEMO.orders;
```

The response includes a warning stating that no statement was sent to StarRocks. Details belong to the task-orchestration docs, not here.

---

## SQL guard interactions

Native SQL that touches protected objects is blocked before the engine sees it:

| Statement | Outcome |
|-----------|---------|
| `DROP ROLE ACCOUNTADMIN` | `ForbiddenSQLError("ACCOUNTADMIN role cannot be dropped")` |
| `REVOKE … FROM ROLE ACCOUNTADMIN` | Blocked |
| `REVOKE … FROM ACCOUNTADMIN` (bare form) | Blocked |
| `ALTER ROLE ACCOUNTADMIN` | Blocked |
| `ALTER ROLE x RENAME TO ACCOUNTADMIN` | Blocked |
| `DROP USER … root` | Blocked |
| `DROP GLOBAL FUNCTION AI_*|ML_PREDICT` | Blocked |

The guard normalizes comments and identifier quoting first, so `DROP /*x*/ ROLE \`ACCOUNTADMIN\`` is caught. Full detail in `09-guardrails-invariants.md`.

---

## `EXPLAIN`

`EXPLAIN <statement>` is supported through `POST /api/v1/query/explain`. `@stage` is translated first so the plan is against a real path; the returned `executed_sql` is redacted. Standard `EXPLAIN` output columns are the engine's — **unverified** here.

---

## `SHOW` / `DESCRIBE`

Passed through. Examples:

```sql
SHOW DATABASES;
SHOW TABLES FROM NOVA_SYSTEM;
SHOW GLOBAL FUNCTIONS;

DESC NOVA_SYSTEM.CONFIG_STAGES;
```

`SHOW GLOBAL FUNCTIONS` includes `AI_*` and `ML_PREDICT`. `DESC`/`DESCRIBE` is also what the completions endpoint uses for columns (`service.py::_list_columns`).

---

## Multi-statement scripts

The API splits on `;` and runs statements in order, stopping at the first failure (`07`). The guard splits first too, so a blocked statement after a `;` cannot hide:

```sql
SELECT 1; DROP ROLE ACCOUNTADMIN
```

→ blocked; the second statement is never reached. Asserted by `test_ml_engine_training_sql.py::test_multistatement_script_cannot_hide_a_blocked_tail`.

---

## What is explicitly NOT Nova SQL

- Raw `s3://` / `minio://` / Azure / GCS URIs as table sources. Use `@stage` so credentials are injected and never typed.
- Storage credentials as literals in `FILES()` calls.
- Direct connections to the engine on port 9030 for feature work.

---

## Engine-semantics cross-reference (`10`)

The research comparison `10-starrocks-reference-comparison.md` answers several of the upstream questions this document deliberately did not guess. The answered points are now cross-references rather than open items:

1. **`FILES()` property set.** `10:44` gives the official signature (`data_location`, `data_format`, `schema_detect`, `StorageCredentialParams`, `columns_from_path`, `list_files_only`, `list_recursively`) and the S3-compatible credential keys Nova injects. `csv.trim_space` is **not** in the official parameter list and remains `[BELUM TERVERIFIKASI]` (`10:109`).
2. **`ai_query()` config keys.** Answered by `10:71`: `model` (required), `api_key` (required), `endpoint`, `temperature`, `max_tokens`, `top_p`, `timeout_ms`.
3. **Grammar `CREATE ML_MODEL`.** Answered by `10:61-65`: the vendored/upstream grammar contains no `ML_MODEL`/`ML_PREDICT` token or rule; `CREATE ML_MODEL` is Nova-only and never reaches the engine. Matrix rows `10:29`, `10:31`, `10:33` cover `CREATE ML_MODEL`, `ML_PREDICT`, and the grammar provenance.

The remaining three items are **not** covered by `10` and stay open — they were moved into `10:105-114` as `[BELUM TERVERIFIKASI]` so they are not lost:

1. `COPY INTO` load/export option syntax and behaviour.
2. `PRIMARY KEY` / `DUPLICATE KEY` / partitioning / `PROPERTIES` semantics in 4.1.4.
3. `EXPLAIN` output shape.

---

## Verification

| Claim | Test / source |
|-------|---------------|
| No-stage statement passes through unchanged | `test_dialect.py::TestParser::test_simple_select_no_stage`; `test_sql_pipeline_files_params.py::test_no_files_call_is_untouched` |
| Multi-statement split respects literals/comments | `tests/unit/test_sql_statement_boundary.py` |
| Blocked tail cannot hide behind `;` | `test_ml_engine_training_sql.py::test_multistatement_script_cannot_hide_a_blocked_tail` |
| `CREATE ML_MODEL` is not engine SQL | `test_ml_model_ddl.py::test_query_service_routes_create_ml_model_to_ml_engine` |
| `CREATE TASK` is not engine SQL | `tests/unit/test_query_create_task_interception.py` |
| Guard blocks protected-object DDL | `tests/unit/test_sql_guard*.py` |
