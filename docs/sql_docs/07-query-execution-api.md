# 07 — Query Execution API

> How Nova SQL reaches the engine: `POST /api/v1/query/execute`, the multi-statement contract, `EXPLAIN`, history, and the two supported client surfaces.

Sources: `backend/app/modules/query/router.py`, `service.py`, `repository.py`, `backend/app/main.py`, `backend/app/common/responses.py`.

---

## Surfaces

Nova SQL is executed through exactly two user-facing paths:

1. **SQL Workspace** in the UI → `POST /api/v1/query/execute`.
2. **MySQL proxy** on port **4406** → the same `QueryService.execute` pipeline.

The StarRocks FE native MySQL port (9030) is **not** a Nova surface. It bypasses the `@stage` dialect, credential injection, and the guard, so statements that rely on Nova features must reach the engine through one of the two paths above.

Router mounting (`main.py:124`):

```
/api/v1/query/*      → query_router
/api/v1/ml/*         → ml_router
/api/v1/ai/*         → ai_router + llm_fn_router
/api/v1/internal/ml/*→ ml_internal_router   (localhost only)
```

---

## `POST /api/v1/query/execute`

Executes one or more SQL statements (split on `;`). Each statement runs the full pipeline: guard → parse → translate → inject → execute → audit. It stops on the first error and returns the results collected so far plus an error result. The response is **always a list**, even for a single statement.

### Request

```json
{
  "sql": "SELECT * FROM @stage1.data.csv LIMIT 5",
  "database": "NOVA_DEMO",
  "schema": "public",
  "role": "ACCOUNTADMIN",
  "max_rows": 500,
  "file_id": null,
  "confirm_destructive": false
}
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `sql` | string | required | Min length 1. |
| `database` | string \| null | null | |
| `schema` | string \| null | null | Sent as `schema` (alias of `schema_name`). |
| `role` | string \| null | null | Applied via `SET ROLE` on the user connection. |
| `max_rows` | int | 500 | 1 ≤ n ≤ 5000. |
| `file_id` | string \| null | null | Tags the audit row and history. |
| `confirm_destructive` | bool | false | Required for `DROP`/`TRUNCATE`/`DELETE`/`UPDATE`/`ALTER … DROP`. |

Authentication is via `get_current_user`; the session carries `username` and Fernet-encrypted `encrypted_password`. The plaintext password is decrypted only to open the user's StarRocks connection (or skipped when the proxy injects an already-authenticated connection).

### Response (list of statement results)

```json
[
  {
    "success": true,
    "columns": ["id", "name", "amount"],
    "rows": [[1, "widget", 9.99]],
    "row_count": 1,
    "affected_rows": 0,
    "elapsed_ms": 42.7,
    "original_sql": "SELECT * FROM @stage1.data.csv LIMIT 5",
    "executed_sql": "SELECT * FROM FILES('path'='s3://bucket/…', 'format'='csv', 'aws.s3.access_key'='***', 'aws.s3.secret_key'='***') LIMIT 5",
    "warnings": ["Resolved @stage1 reference for execution"],
    "destructive": false,
    "needs_confirmation": false,
    "error": null
  }
]
```

| Field | Meaning |
|-------|---------|
| `success` | `error is None`. Derived only from the explicit marker, never inferred from result shape. |
| `columns` / `rows` / `row_count` | Tabular result. CSV header names replace `$1`, `$2`, … when detected. |
| `affected_rows` | For DML. |
| `original_sql` | The user's statement. |
| `executed_sql` | The **redacted** engine-bound statement. Always safe to display. |
| `warnings` | Informational only; never decides success. A successful `@stage` query carries a warning. |
| `destructive` | Whether the statement is a destructive kind. |
| `needs_confirmation` | True for destructive statements or an unscoped `DELETE`/`UPDATE`. |
| `error` | Failure message, or `null`. |

### The `success` contract

`success` is `QueryResult.error is None` (`repository.py:71`). It is **not** computed from `warnings` or from an empty result. The earlier shape heuristic — `bool(warnings) and not columns and row_count == 0` — misreported every successful `@stage` DML statement, because translation always appends a warning while `COPY INTO` returns no columns. This is covered by `tests/unit/test_query_success_contract.py`.

### Credential safety in the response

The response class is `SanitizingJSONResponse`, which recursively redacts credential values from the serialized payload as a last line of defence (`common/responses.py`). Normally it rewrites nothing, because `QueryResult` already redacted `executed_sql` on construction. If the redactor refuses a string, the field is replaced with a fixed placeholder rather than crashing the response.

### Multi-statement behaviour

`execute_statements` splits the input with `split_sql_statements` — respecting string literals and comments — then executes each in order. On an exception it appends an error result for that statement and **stops**; later statements do not run (`service.py:453`).

```json
{
  "sql": "SELECT 1; SELECT 2; SELECT fail; SELECT 3"
}
```

→ results for `SELECT 1`, `SELECT 2`, and a failure result for `SELECT fail`; `SELECT 3` never runs.

---

## `POST /api/v1/query/explain`

Returns the plan for one statement. `@stage` is translated first, so `EXPLAIN` plans against a real path and real credentials; the response is a single `QueryResponse` (not a list).

```json
{ "sql": "SELECT * FROM @stage1.data.csv", "database": "NOVA_DEMO" }
```

`QueryService.explain` (`service.py:885`) normalizes `db.default.table`, runs the guard, translates `@stage`, and prepends `EXPLAIN`. A translation failure returns `error` with `success=false` rather than an empty plan. As with `execute`, the returned `executed_sql` is redacted.

---

## History and diagnostics

### `GET /api/v1/query/history`

Reads `NOVA_SYSTEM.AUDIT_LOG` for `event_type='query'` and `user_name=<current user>`. Query parameters: `file_id`, `status`, `limit` (default 50), `offset`, `search` (matches `sql_text`), `database_name`, `date_from`, `date_to`, `min_duration_ms`, `user_name` (admins only).

Response items carry `log_id`, `event_time`, `user_name`, `object_name`, `action`, `sql_text`, `status`, `duration_ms`, `rows_affected`, `error_message`, `file_id`, `database_name`, `schema_name`, `session_id`.

### `GET /api/v1/query/history/stats`

Aggregates: total, average duration, error count, success count, error rate — with the same filters.

### `GET /api/v1/query/context`

Returns the caller's roles, databases, schemas, and last-used defaults (read from `CONFIG_USER_PREFERENCES`).

### `GET /api/v1/query/completions`

Completion items for `role`, `database`, `schema`, `column`, `stage`, `stage_file`, or general objects.

---

## The audit trail

Every execution writes to `NOVA_SYSTEM.AUDIT_LOG` with `event_type='query'` and `action='execute'`:

| Outcome | `status` | `rewritten_sql` |
|---------|----------|-----------------|
| Executed successfully | `SUCCESS` | Redacted engine statement |
| Engine raised | `ERROR` | Redacted engine statement |
| Guard refused (pre-engine) | `ERROR` | `NULL` |
| `@stage` translation refused (pre-engine) | `ERROR` | `NULL` |

Pre-engine refusals did not always produce a row historically; `_audit_engine_result` (`service.py:398`) exists to record them so "no ERROR rows" genuinely means "no failed attempts". See `tests/unit/test_query_pre_engine_audit.py`.

---

## MySQL proxy (port 4406)

A MySQL client can connect on 4406 and use `@stage` transparently:

```
mysql --host=127.0.0.1 --port=4406 --user=nova_admin --password=… \
  --execute="SELECT * FROM @products.products_new.csv LIMIT 5"
```

The proxy authenticates against StarRocks (relaying its own challenge, so it never holds a password), runs statements through the same `QueryService` pipeline, and returns MySQL protocol results. Statements that rely on `@stage`/`AI_*`/`ML_*` must go through 4406, not 9030.

---

## Worked request/response

Request:

```json
{ "sql": "SELECT customer_id, AI_SENTIMENT('Great service') AS s FROM NOVA_DEMO.customers LIMIT 2" }
```

Response (abbreviated):

```json
[
  {
    "success": true,
    "columns": ["customer_id", "s"],
    "rows": [[1, "{\"sentiment\":\"positive\",\"confidence\":0.95}"], [2, "{\"sentiment\":\"positive\",\"confidence\":0.9}"]],
    "row_count": 2,
    "executed_sql": "SELECT customer_id, AI_SENTIMENT('Great service') AS s FROM NOVA_DEMO.customers LIMIT 2",
    "warnings": [],
    "error": null
  }
]
```

No `@stage` here, so the statement reaches the engine unchanged.

---

## Limitations

- A statement is split on `;` before execution; `execute_statements` does not continue past the first failure.
- `max_rows` is capped at 5000.
- `success` requires the explicit `error` marker; a client cannot treat "empty result + warning" as failure.
- The internal ML endpoints (`/api/v1/internal/ml/*`) are unauthenticated and must not be publicly routed.

---

## Verification

| Claim | Test |
|-------|------|
| `success` derived from `error`, not shape | `tests/unit/test_query_success_contract.py` |
| Multi-statement stops on first error | `test_ml_model_ddl.py::test_query_service_execute_statements_continues_until_error` |
| Pre-engine refusals are audited | `tests/unit/test_query_pre_engine_audit.py` |
| Guard placement relative to execution | `tests/unit/test_query_guard_placement.py` |
| `executed_sql` redaction / no leaks in JSON | `tests/unit/test_exception_handler_credential_leak.py`, `test_explain_credential_leak.py` |
| `EXPLAIN` translation path | `tests/unit/test_query_explain_success_contract.py` |
