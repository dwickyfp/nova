# SQL Documentation — Nova Dialect & StarRocks Queries

> Complete reference for every SQL surface Nova exposes: the `@stage` dialect, the ML/AI statements, the `NOVA_SYSTEM` tables, the execution API, and the guardrails that constrain all of it.

Engine: **StarRocks 4.1.4** (`525f624`, grammar pinned to tag `4.1.4`, commit `4a9848edf03f5c936dac664b2d52527f48e72eb0`).
Backend: FastAPI + Python 3.11 (`backend/`).

---

## Scope

This folder documents the SQL that Nova accepts and the SQL it emits. It is written from the implementation — the pipeline, parser, translator, guards, and services — not from feature summaries. Every SQL example that appears here is either:

- an input the pipeline accepts and rewrites (with the rewrite shown), or
- a statement Nova refuses, with the exact error path named.

No example contains a real storage or user credential. Placeholders (`'K'`, `'S'`, `'***'`) are used wherever the implementation would inject one.

---

## Reading order

| # | Document | What it covers |
|---|----------|----------------|
| 00 | `00-index.md` | This file — scope, reading order, the four-step pipeline at a glance. |
| 01 | `01-dialect-pipeline.md` | The shared pipeline: guard → parse → translate → inject → redact. Ordering, `PreparedSQL`, and why every entry point uses it. |
| 02 | `02-stage-queries.md` | The `@stage` abstraction: syntax, context classification, `FILES()` translation, CSV auto-detection, `@stage` vs user variables. |
| 03 | `03-ml-model-ddl.md` | `CREATE ML_MODEL ... AS SELECT ...` — grammar, defaults, validation, the metadata rows it writes. |
| 04 | `04-ml-predict-evaluate.md` | `ML_PREDICT`, the prediction API, batch prediction, and the evaluation metrics training produces. |
| 05 | `05-ai-functions.md` | `AI_COMPLETE` / `AI_SENTIMENT` / `AI_CLASSIFY` / `AI_SUMMARIZE` / `AI_EXTRACT` / `AI_TRANSLATE` / `AI_FILTER`, their UDF bodies, and the `ai_query()` backing. |
| 06 | `06-nova-system-tables.md` | `NOVA_SYSTEM` catalog: every table, its key model, and which are queryable from a worksheet. |
| 07 | `07-query-execution-api.md` | `POST /api/v1/query/execute`, `/explain`, `/history`, the request/response contract, and the multi-statement path. |
| 08 | `08-native-starrocks-sql.md` | Plain StarRocks SQL that travels through the pipeline untouched. Parts that depend on upstream semantics are marked unverified pending the research comparison. |
| 09 | `09-guardrails-invariants.md` | The guard patterns, destructive confirmation, credential redaction, and the invariants every document must respect. |
| 10 | `10-starrocks-reference-comparison.md` | *Owned by the research workstream* — maps each Nova query to its official StarRocks 4.1.4 equivalent. |
| 11 | `11-query-catalog.md` | *Owned by the research workstream* — the full catalog of queries with sources and confidence. |

Documents `10` and `11` are produced by the StarRocks research workstream and referenced here rather than duplicated. Where a claim in `08` depends on them and they are not yet present, the claim is explicitly marked **unverified**.

---

## The pipeline in one picture

Every statement a user hands Nova — through the SQL Workspace, the MySQL proxy, an ML training call, or a batch prediction — passes through the same four steps in the same order. This is the single most important fact about Nova SQL.

```
user SQL
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. GUARD      `sql_pipeline.guard_user_statement`            │
│    split on ';' → reject blocked patterns → require           │
│    confirmation for destructive statements                    │
├──────────────────────────────────────────────────────────────┤
│ 2. PARSE      `dialect.parser.parse_sql`                      │
│    find `@stage` references, classify stage vs variable       │
│    by position (not by a dot), skipping literals/comments      │
├──────────────────────────────────────────────────────────────┤
│ 3. TRANSLATE  `dialect.translator.translate_stage_query`      │
│    `@stage.path.file.csv` → `FILES('path'=…, 'format'=…)`     │
├──────────────────────────────────────────────────────────────┤
│ 4. INJECT     `dialect.injector.get_credential_params`        │
│    add CSV params, then storage credentials to every FILES()  │
├──────────────────────────────────────────────────────────────┤
│ 5. REDACT     `sql_guard.redact_sql_credentials`              │
│    value-only `***` form — the only form that leaves process  │
└──────────────────────────────────────────────────────────────┘
   │
   ▼
StarRocks 4.1.4 (FE MySQL protocol), on the *user's* connection
```

Only one object ever holds both forms, and its field names state which is which: `PreparedSQL.engine_sql` carries real credentials and `PreparedSQL.redacted_sql` is the safe form (`sql_pipeline.py:40`). See `01-dialect-pipeline.md` for the full treatment.

---

## Feature map

### Nova custom SQL (intercepted before StarRocks)

| Surface | Statement / function | Where |
|---------|----------------------|-------|
| Stage file access | `@stage[.path][/][.file.ext]` → `FILES()` | `02-stage-queries.md` |
| ML model training | `CREATE ML_MODEL name TYPE=… TARGET=… AS SELECT …` | `03-ml-model-ddl.md` |
| Classical ML inference | `ML_PREDICT(alias, features_json)` | `04-ml-predict-evaluate.md` |
| LLM functions | `AI_COMPLETE`, `AI_SENTIMENT`, `AI_CLASSIFY`, `AI_SUMMARIZE`, `AI_EXTRACT`, `AI_TRANSLATE`, `AI_FILTER` | `05-ai-functions.md` |
| Nova DDL (also intercepted) | `CREATE TASK …` lowered to `CONFIG_TASK*` metadata | `06-nova-system-tables.md` |

`CREATE ML_MODEL` and `CREATE TASK` are **Nova statements**: they are parsed by Nova and never sent to StarRocks in their written form. `ML_PREDICT` and the `AI_*` functions are **StarRocks global UDFs** registered by Nova at startup, so they execute inside the engine.

### Execution surfaces

Nova SQL reaches the engine through exactly two user-facing paths:

1. **SQL Workspace** (UI) → `POST /api/v1/query/execute`.
2. **MySQL proxy** on port **4406** → the same `QueryService`.

The engine's native MySQL port (9030) is not a Nova surface and none of the `@stage` / credential-injection behaviour applies there. See `07-query-execution-api.md`.

---

## Invariants (apply to every document here)

These are hard rules from `AGENTS.md`; the tests in `backend/tests/unit/` enforce them.

1. **No credentials in user-visible output.** Storage credentials live in `nova.yaml` + `.env` and in memory. They never appear in `NOVA_SYSTEM` tables, API JSON, frontend state, logs, or error messages. This documentation obeys the same rule — see the placeholder convention below.
2. **No credential-injection mechanism that could extract a secret.** The documents show that injection happens and what shape the result has; they do not show how to make the redactor fail or how to read a key back out.
3. **`@stage` is a first-class abstraction.** It is rewritten to `FILES()` by Nova; the user never writes `s3://` paths or credentials, and the documentation never presents raw storage URIs as the user-facing form.
4. **Nova SQL goes through the Workspace or `POST /api/v1/query/execute`** (or the proxy on 4406), never directly to port 9030.
5. **StarRocks remains the RBAC source of truth.** Nova authenticates against StarRocks and executes user statements on the user's connection; Nova's own metadata reads (stage configs, system tables) do not widen what a statement can reach.

### Placeholder convention

| Placeholder | Meaning |
|-------------|---------|
| `'K'` | An injected access-key value (redacted to `'***'` in output). |
| `'S'` | An injected secret-key value (redacted to `'***'` in output). |
| `'***'` | What a redacted credential value looks like in `executed_sql` and audit rows. |
| `bucket`, `nova-stages` | A bucket name; never a credential. |
| `db/schema/stage1` | A `base_prefix`; never a credential. |

---

## Verification status

Behavioural claims in `00`–`07` and `09` are backed by the unit suite:

```
cd backend
uv run pytest tests/unit/test_dialect.py tests/unit/test_sql_pipeline_files_params.py \
              tests/unit/test_ml_model_ddl.py -q
# 139 passed

uv run pytest tests/unit/test_sql_guard.py tests/unit/test_sql_guard_bypass.py \
              tests/unit/test_sql_guard_revoke_hardening.py \
              tests/unit/test_ml_engine_training_sql.py \
              tests/unit/test_credential_leaks.py -q
# 192 passed
```

`08-native-starrocks-sql.md` describes StarRocks-native behaviour that Nova does not re-implement; sections that depend on upstream documentation are marked **unverified** until the research comparison (`10`) lands.
