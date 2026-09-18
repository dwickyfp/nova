# 01 — The Dialect Pipeline

> The shared four-step preparation every user-supplied statement goes through before it reaches StarRocks: guard → parse → translate → inject → redact.

Source of truth: `backend/app/modules/query/sql_pipeline.py`.

---

## Why the pipeline exists

Nova has several entry points that accept SQL from a user:

| Entry point | Code path |
|-------------|-----------|
| SQL Workspace / `POST /api/v1/query/execute` | `QueryService.execute` |
| MySQL proxy (port 4406) | `QueryService.execute` via `app/proxy` |
| `EXPLAIN` from the Workspace | `QueryService.explain` |
| `CREATE ML_MODEL … AS SELECT <training_sql>` | `MLEngineService.train_model` |
| `POST /api/v1/ml/predict/batch` | `MLEngineService.batch_predict` |

All of them execute on a connection that eventually needs storage credentials for `FILES()`. If each entry point applied its own subset of the steps, the subsets would drift — and drift on a credential-bearing pipeline is a leak. That is not hypothetical: `ml_engine` originally executed `training_sql` with **none** of the four steps — no guard, no translation, no injection, no redaction (`NOVA-28`). The shared module exists so the guarantee is stated once (`sql_pipeline.py:1`).

---

## The four steps

### Step 1 — Guard

`guard_user_statement(sql, *, confirm_destructive=False)` (`sql_pipeline.py:68`).

- Splits the input into statements with `split_sql_statements` **first**, because the API accepts multi-statement scripts. A guard anchored on the whole blob would be blind to everything after the first `;`.
- Runs `guard_sql` on each statement — the ACCOUNTADMIN/root/built-in-UDF protection described in `09-guardrails-invariants.md`.
- Rejects any destructive statement (`DROP`, `TRUNCATE`, `ALTER TABLE … DROP`, `DELETE FROM`, `UPDATE`) unless `confirm_destructive=True`.

A rejection raises `ForbiddenSQLError`. The workspace path audits the refusal before re-raising; `ml_engine` simply propagates it.

### Step 2 — Parse

`parse_sql(sql)` from `dialect/parser.py`. It finds every `@stage` reference and classifies each as a stage or a user variable **by position**, not by the presence of a dot. Literals and comments are scanned first so an `@name` inside `'FROM @x'` is treated as data. Full treatment in `02-stage-queries.md`.

### Step 3 — Translate

`translate_stage_query(parsed, stage_configs)` from `dialect/translator.py`. Each `@stage.path` becomes a `FILES('path'=…, 'format'=…)` call. A stage whose name is not in `stage_configs` raises `ValueError("Stage '<name>' not found")`. Full treatment in `02-stage-queries.md`.

### Step 4 — Inject

Two injections run in `prepare_stage_sql` (`sql_pipeline.py:131`–`136`):

1. **CSV parameters** (`csv.column_separator`, `csv.skip_header`, …) if the caller detected them.
2. **Storage credentials** from `dialect/injector.py`.

Both go through `_inject_files_params`, which is **per-key idempotent**: a key is added only if that exact `'key'` is absent from the `FILES()` body. This is deliberate. The "obvious" guard (`if 'access_key' in content: skip`) was wrong — the translator already emits credentials, so on the CSV pass it saw an `access_key` belonging to the credential group and skipped the CSV properties entirely, producing a stage query that returned the file's raw line text as a single column instead of parsed columns. Keying on the names being injected makes the two passes independent (`sql_pipeline.py:147`).

### Step 5 — Redact

`redact_for_output(sql)` → `redact_sql_credentials`. Replaces credential *values* with `***`, preserving parameter names, paths, and formats so an audit row still documents what ran. Fails closed: if a credential value would survive, it raises `CredentialsRedactionError` rather than return the raw statement.

---

## Ordering is load-bearing

The order above is not arbitrary (`sql_pipeline.py:18`):

- The guard runs on the **normalized** statement *before* translation, so a statement is judged as the user wrote it.
- Redaction runs on the **translated** statement, because that is the text that carries the injected credentials.

Reversing either would judge or sanitize the wrong text.

---

## `PreparedSQL` — the two forms

```
PreparedSQL
├── engine_sql    ← what StarRocks receives. CARRIES REAL CREDENTIALS.
│                   Never persist, return, or log.
├── redacted_sql  ← the only form that may leave the process.
├── warnings      ← informational; never decides success.
├── csv_columns   ← header names detected from a CSV, if any.
└── parsed        ← the ParsedSQL (stage refs, command type).
```

Both forms live on one object so the wrong choice is visible at every use site (`sql_pipeline.py:40`). Downstream, `QueryResult.__post_init__` independently redacts `executed_sql` (`repository.py:68`), and `SanitizingJSONResponse` redacts the serialized HTTP payload as a last line of defence (`common/responses.py`).

`prepare_stage_sql` is pure with respect to everything except its arguments: stage configs, `csv_params`, and credentials are passed in or read from config, so every branch is testable without a database. The engine call itself is the caller's business.

---

## Worked example

Input (as the user writes it):

```sql
SELECT * FROM @stage1.data.csv LIMIT 5
```

After step 3 (translation) — stage config `base_prefix='db/schema/stage1'`, `bucket='bucket'`:

```sql
SELECT * FROM FILES('path'='s3://bucket/db/schema/stage1/data.csv', 'format'='csv') LIMIT 5
```

After step 4 (injection) — engine form:

```sql
SELECT * FROM FILES('path'='s3://bucket/db/schema/stage1/data.csv', 'format'='csv',
  'csv.column_separator'=',', 'csv.skip_header'='1',
  'aws.s3.access_key'='K', 'aws.s3.secret_key'='S') LIMIT 5
```

After step 5 (redaction) — the form returned and audited:

```sql
SELECT * FROM FILES('path'='s3://bucket/db/schema/stage1/data.csv', 'format'='csv',
  'csv.column_separator'=',', 'csv.skip_header'='1',
  'aws.s3.access_key'='***', 'aws.s3.secret_key'='***') LIMIT 5
```

The path, format, and CSV tuning survive; only the values change.

---

## Error paths

| Failure | Raised by | Result |
|---------|-----------|--------|
| Blocked statement (`DROP ROLE ACCOUNTADMIN`) | `guard_sql` | `ForbiddenSQLError`; workspace audits `status=ERROR` and re-raises |
| Destructive statement without confirmation | `guard_user_statement` | `ForbiddenSQLError`; API returns `needs_confirmation=true` |
| Unknown stage in `@stage` | `translate_stage_query` | workspace catches `ValueError`, audits, returns `error="Stage 'x' not found"` |
| Credential value survives redaction | `redact_sql_credentials` | `CredentialsRedactionError`; fails closed |

---

## No-stage statements

A statement with no `@stage` reference short-circuits: `prepare_stage_sql` returns it unchanged with an empty warning list and no credential injection. The guard has still run, and `redact_sql_credentials` is still applied (it is a no-op for credential-free SQL). So plain `SELECT`, `INSERT`, DDL, and `SHOW` all travel the guarded path with byte-identical text.

---

## Verification

| Claim | Test |
|-------|------|
| CSV params survive credential presence and are idempotent | `tests/unit/test_sql_pipeline_files_params.py::TestCsvParamsSurviveCredentialPresence` |
| `prepare_stage_sql` produces both forms; redacted has `***` | `tests/unit/test_sql_pipeline_files_params.py::TestPrepareStageSqlOrdering::test_stage_query_carries_both_csv_params_and_credentials` |
| Guard runs before the engine on the training path | `tests/unit/test_ml_engine_training_sql.py::TestTrainingSqlIsGuarded` |
| Redacted form carries no credential | `tests/unit/test_ml_engine_training_sql.py::TestNoCredentialMaterialEscapes` |
| Unknown stage raises | `tests/unit/test_dialect.py::TestTranslator::test_translate_missing_stage_raises` |
