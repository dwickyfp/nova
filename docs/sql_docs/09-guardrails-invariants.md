# 09 — Guardrails & Invariants

> The rules that constrain every statement, query, and document in Nova: what the SQL guard blocks, what redaction removes, and the invariants enforced by tests.

Sources: `backend/app/common/sql_guard.py`, `backend/app/common/responses.py`, `backend/app/modules/query/sql_pipeline.py`, `backend/app/core/exceptions.py`.

---

## Part 1 — The SQL guard

### What it protects

| Protected object | Rule |
|------------------|------|
| `ACCOUNTADMIN` role | Cannot be dropped, revoked from, altered, or renamed *to*. |
| `root` user | Cannot be dropped. |
| Nova built-in UDFs | `DROP GLOBAL FUNCTION` of `AI_COMPLETE`, `AI_SENTIMENT`, `AI_CLASSIFY`, `AI_SUMMARIZE`, `AI_EXTRACT`, `AI_TRANSLATE`, `AI_FILTER`, `ML_PREDICT` is blocked. |

`ACCOUNTADMIN` is the only role with `GRANT OPTION`; if dropped, no user can grant privileges and the system locks. Hence the hard block.

### Blocked patterns

`BLOCKED_PATTERNS` (`sql_guard.py:70`) — all matched with `re.IGNORECASE | re.DOTALL`:

```
DROP ROLE [IF EXISTS] ACCOUNTADMIN
REVOKE … FROM ROLE [IF EXISTS] ACCOUNTADMIN
REVOKE … FROM [IF EXISTS] ACCOUNTADMIN
ALTER ROLE [IF EXISTS] ACCOUNTADMIN
ALTER ROLE <src> RENAME TO ACCOUNTADMIN
DROP USER … root
DROP GLOBAL FUNCTION [IF EXISTS] <AI_*|ML_PREDICT> (   — with signature
DROP GLOBAL FUNCTION [IF EXISTS] <AI_*|ML_PREDICT> ;   — without signature
```

Each blocked pattern raises `ForbiddenSQLError` with a specific message (e.g. `"ACCOUNTADMIN role cannot be dropped"`).

### Normalization before matching

The guard cannot be shifted by presentation-level noise. `normalize_sql` (`sql_guard.py:274`) applies, in order:

1. **Comment removal** — block comments dropped (first `*/` closes, as the engine does; comments do **not** nest), line comments stripped to end of line.
2. **Identifier unquoting** — backtick, double-quote, single-quote, and bracket forms of an *identifier-shaped* body are collapsed. `'ACCOUNTADMIN'`, `` `ACCOUNTADMIN` ``, `"ACCOUNTADMIN"`, `[ACCOUNTADMIN]` all reduce to `ACCOUNTADMIN`. Real string literals keep their quotes.
3. **Whitespace squeeze** — runs collapse to a single space.

So `DROP /*x*/ ROLE\n'ACCOUNTADMIN'` and `DROP ROLE ACCOUNTADMIN` are the same string to every pattern.

### Statement-by-statement

`guard_sql` splits the input with `split_sql_statements` and checks each statement (`sql_guard.py:296`). A guard anchored on the whole blob would be blind to everything after the first `;`. The splitter itself respects single-quoted literals and comments, and treats the first `*/` as closing a block comment — matching the engine — so it never invents or loses a statement boundary.

### Destructive confirmation

Separate from the guard, `is_destructive_sql` and `is_unscoped_mutation` gate destructive statements:

```
DESTRUCTIVE:      ^\s*(DROP|TRUNCATE|ALTER TABLE … DROP|DELETE FROM|UPDATE )
UNSCOPED MUTATION:^\s*(DELETE FROM|UPDATE )   (no WHERE anywhere)
```

`guard_user_statement(sql, confirm_destructive=False)` raises `ForbiddenSQLError("Destructive SQL requires confirmation before execution.")` for a destructive statement without confirmation. The API surfaces this as `needs_confirmation=true`.

---

## Part 2 — Credential redaction

### What is a credential

`CREDENTIAL_PARAM_SUFFIXES` (`sql_guard.py:368`):

```
access_key, secret_key, session_token, account_key, sas_token, service_account_key
```

Matching is on the **suffix** of a dotted parameter name with a provider prefix, and accepts `_` or `.` between words (`account_key` and `account.key` both match). Built from the same tuple, so the two sides cannot drift.

### How redaction works

`redact_sql_credentials(sql)` replaces the **value** with `***` while preserving the parameter name, its quoting, and the operator (`sql_guard.py:471`):

```
'aws.s3.access_key'='<value>'   →   'aws.s3.access_key'='***'
aws.s3.secret_key="<value>"   →   aws.s3.secret_key='***'
```

Two patterns cover quoted and bare keys; a shared value grammar handles single/double/backtick quoting and `=` or `=>`.

### Fails closed

After substitution, `_redaction_is_complete` re-scans for **any** populated credential assignment of any shape. If one survives, `redact_sql_credentials` raises `CredentialsRedactionError` rather than return the raw statement (`sql_guard.py:495`). The only alternative to redacting is leaking, so a statement that cannot be redacted must not be returned.

### Where redaction is applied

| Layer | Mechanism |
|-------|-----------|
| `QueryResult.__post_init__` | Redacts `executed_sql` on construction — the single entry point for that field (`repository.py:68`). |
| `QueryService.execute` | Redacts the engine statement before the audit write and before returning (`service.py:343`). |
| `MLEngineService` | Stores the redacted training SQL; logs the redacted form. |
| `SanitizingJSONResponse` | Recursively redacts the serialized HTTP body as a last line of defence. |

The name `engine_sql` vs `redacted_sql` on `PreparedSQL` makes the credential-bearing form visible at every use site instead of relying on a comment.

---

## Part 3 — The invariants

These are the rules from `AGENTS.md`, restated with their enforcement.

### 3.1 Credentials never appear in user-visible output

Credentials live in `nova.yaml` + `.env` and in memory (encrypted for the StarRocks user password and AI provider keys). They never appear in:

- `NOVA_SYSTEM` tables,
- API JSON responses,
- frontend state,
- logs,
- error messages.

**Enforcement:** redaction layers above; `tests/unit/test_credential_leaks.py`, `test_audit_credential_redaction.py`, `test_exception_handler_credential_leak.py`, `test_explain_credential_leak.py`, `test_defense_in_depth_hardening.py`.

### 3.2 No credential-injection mechanism that can extract a secret

The pipeline uses provider-agnostic FILES() parameters supplied by the storage provider. Nova's own FILES() parameter *suffixes* match whatever the configured provider needs; it does not key on "is this Azure vs AWS" in a way that writes `azure.account_key` to a non-Azure store. `_storage_access_key()` / `_storage_secret_key()` return the configured values and never mutate them per request.

**Enforcement:** `tests/unit/test_defense_in_depth_hardening.py`, `tests/unit/test_credential_leaks.py`.

### 3.3 `@stage` is a first-class abstraction

`@stage_name.path.file.ext` is the user-facing file access form; it is always rewritten to `FILES()` with credentials injected. This rewrite is invariant-protected in `tests/unit/test_dialect.py::TestBareAndDirectoryStagesReachTranslation` and `test_sql_pipeline_files_params.py`.

### 3.4 Every action reaches the audit log

`QueryService.execute` writes a `SUCCESS` **or** `ERROR` row to `NOVA_SYSTEM.AUDIT_LOG` for every attempt, including pre-engine refusals. `_audit_engine_result` covers the paths that return before the engine call (`service.py:398`).

**Enforcement:** `tests/unit/test_query_pre_engine_audit.py`, `tests/unit/test_audit_credential_redaction.py`.

### 3.5 StarRocks is the RBAC source of truth

Nova authenticates a user against StarRocks and executes the user's statement on the user's connection. Nova's metadata reads (stage configs, `NOVA_SYSTEM` lookups) are configuration, not a privilege boundary — the control is enforced where StarRocks enforces every other read: on the statement.

**Enforcement:** `tests/unit/test_explain_credential_leak.py` (system-config read does not widen user reach), `tests/unit/test_users_rbac.py`.

### 3.6 No credentials in storage or logs

- Training SQL is persisted redacted (`test_ml_engine_training_sql.py`).
- Execution logs use redacted SQL (`test_explain_credential_leak.py`).
- `CONFIG_AI_PROVIDERS.api_key` is encrypted at rest.

**Enforcement:** `test_credential_leaks.py`, `test_audit_credential_redaction.py`, `test_ml_engine_training_sql.py::test_persistence_stores_the_redacted_form`.

### 3.7 No credential in the SQL text

A user must never need to type (and must never type) a storage credential into a `FILES()` call. The injector reads the credential from config and injects it at the engine edge, after the user's text.

**Enforcement:** `tests/unit/test_defense_in_depth_hardening.py`, `tests/unit/test_credential_leaks.py`.

### 3.8 Documentation invariant

Every document in `docs/sql_docs/` obeys the same rules:

- No real access key, secret key, token, or password appears in any example, response, or UI state.
- No mechanism that could extract a secret is shown.
- `@stage` is presented as the first-class form; raw storage URIs are not the user-facing form.
- Nova SQL is described only via the Workspace / `POST /api/v1/query/execute` or the 4406 proxy, never port 9030.

This is checked by the QA workstream against the source.

---

## Part 4 — Error contract

`ForbiddenSQLError` (`app/core/exceptions.py`) is the guard's exception. Where it is raised:

| Site | Behaviour |
|------|-----------|
| `QueryService.execute` | Audits `ERROR` with `rewritten_sql=NULL`, then re-raises. |
| `QueryService.explain` | Guard runs; `@stage` translation refusal returns `error`. |
| `MLEngineService._prepare_user_sql` | Propagates to the caller (train / batch predict). |

A `CredentialsRedactionError` from the redactor is fatal for the response being built; `SanitizingJSONResponse` replaces the offending string with a placeholder rather than shipping it or raising.

---

## Part 5 — What the guard does *not* do

Honest boundaries:

- The guard is pattern-based, not a parser. It blocks the specific protected-object operations it knows; it is not a general SQL sandbox.
- `NOVA_SYSTEM` is not made read-only. A privileged user with a direct StarRocks connection can write to it.
- A destructive statement with `confirm_destructive=true` **is executed** — confirmation is not a safety check on the effect, only an acknowledgement.
- The internal ML prediction endpoints require a loopback (or trusted-proxy) peer plus the `X-Nova-Internal-Token` shared secret; they fail closed when `NOVA_INTERNAL_TOKEN` is unset. Enforcement is in code (`ml_engine/internal_auth.py`), not deployment documentation (NOVA-90).

---

## Verification

```
cd backend
uv run pytest tests/unit/test_sql_guard.py tests/unit/test_sql_guard_bypass.py \
              tests/unit/test_sql_guard_revoke_hardening.py \
              tests/unit/test_credential_leaks.py \
              tests/unit/test_audit_credential_redaction.py \
              tests/unit/test_defense_in_depth_hardening.py \
              tests/unit/test_ml_engine_training_sql.py -q
# 192 passed (subset run during this documentation task)
```

| Claim | Test |
|-------|------|
| ACCOUNTADMIN/root/UDF protection | `test_sql_guard.py`, `test_sql_guard_bypass.py` |
| Revoke-form hardening | `test_sql_guard_revoke_hardening.py` |
| No credential leaks end-to-end | `test_credential_leaks.py` |
| Audit rows are redacted | `test_audit_credential_redaction.py` |
| Exception handlers do not leak | `test_exception_handler_credential_leak.py` |
| Stored training SQL redacted | `test_ml_engine_training_sql.py::test_persistence_stores_the_redacted_form` |
| Pre-engine refusals audited | `test_query_pre_engine_audit.py` |
