# Agentic Assistant — `query_execute` tool + permission/consent design

> Workstream 3 of NOVA-61 (Python Dev Expert). Design research only — **no production code**.
> Answers scope item 3 of the issue: the `query_execute` tool and its permission model,
> mapped onto Nova's existing invariants with precise `path:line` integration points.

Status: **research draft, for cross-review**. Every code reference below was read at the
revision stated, not recalled. Confidence labels: **high** = verified in this repo's code or
config; **medium** = inferred from the code with a stated assumption; **low** = open question.

---

## 0. Executive answer

The assistant's `query_execute` tool must be a **thin adapter that calls the *same* in-process
entrypoint the SQL worksheet uses** — `QueryService.execute_statements()` — and must be invoked
on a **connection opened as the requesting user** (delegate-first, the NOVA-23 pattern). It must
never open a StarRocks socket itself and must never reach port 9030 directly.

The permission layer (ask / always-allow / deny) belongs **above** the pipeline, in the assistant
runtime. It must not weaken anything inside `guard_user_statement`, the dialect pipeline, or the
audit write. "Always allow for this session" is a **per-conversation, in-memory, non-durable**
grant by default (see §2 option B) — persistence to `NOVA_SYSTEM` is a separate, user-decided
option (E2).

Two hard constraints drive the whole design:

1. **RBAC StarRocks is the source of truth.** The tool runs as the user, so the engine — not the
   assistant — decides what is allowed. Nova adds no authorization of its own beyond the existing
   guard (`backend/app/common/sql_guard.py`), which stays exactly as strong as it is today.
2. **Credential-invisible.** No password, storage key, session token, or credential-shaped value
   may enter the conversation history, the tool-call record, the model context, or the assistant's
   own `NOVA_SYSTEM` tables. Nova already enforces this for SQL output via
   `redact_sql_credentials`; the assistant must reuse that, not re-implement it.

---

## 1. Existing execution path, traced with `path:line`

The request path today, end to end:

```
HTTP:  POST /api/v1/query/execute          backend/app/modules/query/router.py:65
  → QueryService.execute_statements(...)   backend/app/modules/query/service.py:453
      → split_sql_statements(sql)          (loop over statements)
      → QueryService.execute(...)          backend/app/modules/query/service.py:176
          → _normalize_default_schema_qualification         service.py:204
          → guard_user_statement(...)                      service.py:219
          → Nova DDL interception (ML_MODEL / CREATE TASK) service.py:234, 251
          → parse_sql / prepare_stage_sql                  service.py:263, 281
          → decrypt_password(encrypted)                    service.py:331
          → QueryRepository.execute_as_user(...)           repository.py:125
              → db.user_conn(username, password)           repository.py:161
              → SET ROLE + execute + fetchmany(max_rows)   repository.py:186-193
          → QueryResult.__post_init__ → redact_sql_credentials  repository.py:68
          → write_audit_log(SUCCESS ...)                   service.py:363
      (on exception) write_audit_log(ERROR ...)             service.py:381
  (on guard refusal) _audit_engine_result(status=ERROR)      service.py:398
```

### 1.1 Where a shortcut could exist, and why it does not

| Shortcut | Where it is closed |
|---|---|
| Direct engine socket on 9030 | Nothing in the request path opens one; the only app-level port reference is the pool config `backend/app/core/config.py:22` (`STARROCKS_FE_MYSQL_PORT: int = 9030`, used by `db.user_conn` / `db.system_conn`). The assistant must not add a second one. |
| Skipping the guard | The guard is called *inside* `QueryService.execute` at `service.py:219`, not by the router. A new caller cannot forget it. |
| Bypassing `@stage` translation | `prepare_stage_sql` is called unconditionally inside `execute` (`service.py:281` and `:319`). |
| Leaking injected credentials | `redact_sql_credentials` runs in `QueryResult.__post_init__` (`repository.py:68-69`) — the constructor is the single choke point, so any object built from the repository is already redacted. The service reads the redacted form at `service.py:343`. |
| Skipping audit | `write_audit_log` is called on the success path (`service.py:363`), the engine-error path (`service.py:381`), and the pre-engine refusal path (`service.py:398`). |
| Weak RBAC | `execute_as_user` opens `db.user_conn(username, password)` (`repository.py:161-165`); there is no service-identity fallback. |

### 1.2 The MySQL proxy is the second caller, not a bypass

`backend/app/proxy/executor.py:204` also calls `query_service.execute_statements(...)`, passing an
**already-authenticated connection** (`connection=connection`, `encrypted_password=""`) rather than
a password — the proxy relays StarRocks' own auth challenge and never holds plaintext
(`repository.py:154-159`). This is the "connected" branch of `execute_as_user`.

**Design consequence:** `QueryService.execute_statements` already accepts both an
`encrypted_password` (web path) and a live `connection` (proxy path). The assistant builds on the
**web path**: it has the request's `require/Depends(get_current_user)` context available
(`backend/app/core/deps.py:15-53`) and therefore `encrypted_password` from the Redis session. It
should pass `connection=None` and let `execute_as_user` open the user connection. That keeps one
authorization story (the user's StarRocks credentials) and no new credential handling.

### 1.3 Where the tool must be mounted

The tool is not itself an HTTP endpoint in the worksheet sense; it is an internal call. Two
mounting choices exist and both must funnel through the same service:

- **Option 1 (recommended):** the assistant runtime lives in the existing FastAPI process and calls
  `query_service.execute_statements(...)` directly, in-process. No new network hop, no new auth
  boundary, and the user's session dict from `get_current_user` supplies username + encrypted
  password.
- **Option 2:** the assistant is a sidecar/worker (see architecture workstream) and calls
  `POST /api/v1/query/execute` over loopback with the user's bearer token. This is the same path the
  worksheet uses but adds a token-forwarding hop; the token must be the user's, never a service
  token, or RBAC is lost.

Either way the invariant is the same: **there is exactly one SQL execution entrypoint, and the tool
calls it.** The tool module should import `query_service` from
`backend/app/modules/query/service.py` and never `asyncmy`/`db.user_conn` directly.

> Integration point summary (file:line):
> - Entry: `backend/app/modules/query/service.py:453` (`execute_statements`) — call this.
> - Guard: `backend/app/modules/query/sql_pipeline.py:68` (`guard_user_statement`).
> - Guard rules: `backend/app/common/sql_guard.py:70` (`BLOCKED_PATTERNS`), `:296` (`guard_sql`).
> - Destructive gate: `backend/app/common/sql_guard.py:333` (`DESTRUCTIVE_SQL_PATTERN`), `:339` (`UNSCOPED_MUTATION_PATTERN`).
> - Translation: `backend/app/modules/query/sql_pipeline.py:98` (`prepare_stage_sql`).
> - Injection: `backend/app/modules/query/dialect/injector.py:26` (`get_credential_params`).
> - Redaction: `backend/app/common/sql_guard.py:471` (`redact_sql_credentials`), invoked at `backend/app/modules/query/repository.py:68`.
> - User-scoped execution: `backend/app/modules/query/repository.py:125` (`execute_as_user`).
> - Audit: `backend/app/common/audit.py:10` (`write_audit_log`) → `NOVA_SYSTEM.AUDIT_LOG` (`docker/init-nova.sql:311`).

---

## 2. Permission model

### 2.1 Decision states

A tool call that wants to run SQL presents a **pending action**: the SQL text, the routed
database/schema/role, and the number of statements. The user resolves it with one of:

- **allow once** — run now, do not remember.
- **allow for this session** ("always allow") — run now and auto-approve structurally equivalent
  future calls for the rest of this conversation.
- **deny** — do not run; the assistant receives a structured refusal and must not retry the same
  statement in a loop.

### 2.2 What "this session" means (precision matters)

There are three candidate scopes:

| Scope | Meaning | Durability | Fit |
|---|---|---|---|
| **Conversation** | the chat thread the user is in | in-memory for the thread | Recommended default |
| **Login session** | the JWT/Redis session (`user["session_id"]`, `deps.py:49`) | until logout/TTL | Broader than the user likely expects |
| **User + workspace (persistent)** | a row in `NOVA_SYSTEM` | across restarts | Requires explicit user decision (E2) |

**Recommendation:** default to **conversation scope**. It is the smallest grant that satisfies "I
don't want to click allow ten times while you explore my schema", and it dies with the thread.
Persisting to `NOVA_SYSTEM` should be opt-in and is exactly open decision **E2**.

### 2.3 The grant is a policy, not a blanket "yes"

"Always allow for this session" must **not** mean "run anything". The UI copy and the stored
policy should be explicit that it covers a class, e.g. *read-only statements* (`SELECT`, `SHOW`,
`DESCRIBE`, `EXPLAIN`). Destructive statements (`DROP`, `TRUNCATE`, `DELETE`, `ALTER TABLE … DROP`,
unscoped `UPDATE`/`DELETE`) must **never** be covered by an always-allow grant — they keep the
per-call confirmation that `guard_user_statement` already enforces
(`sql_pipeline.py:82-85`). This is both safer and simpler to reason about: an always-allow grant
cannot silently erase the confirmation the engine's own guard demands.

### 2.4 Storage without violating "Single Database" and credential-invisibility

The `always allow` state is a preference, not data. Two viable stores:

- **In-memory (recommended default).** Keep the granted class on the assistant conversation object
  in process memory. Nothing is persisted, nothing can credential-leak, and "this session" is
  literally true. Cost: a restart clears grants (acceptable — the user re-approves).
- **`NOVA_SYSTEM` (opt-in, E2).** If persistence across restarts is required, store a **policy
  row keyed by user + workspace + tool**, with a value that is a *class label* (`read_only`) and
  never a SQL string, result, or credential. A policy table that stores SQL would become a new
  credential sink (SQL can contain `FILES('...secret_key'='…')` if the user typed it) and is
  therefore rejected by design.

Proposed persistent table (only if E2 says persistent — **not** part of the default):

```sql
-- Proposal only. Do not create until E2 is decided.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ASSISTANT_TOOL_GRANTS (
  user_name    VARCHAR(128) NOT NULL,   -- StarRocks username, same key space as CONFIG_USER_PREFERENCES
  workspace_id VARCHAR(64)  NOT NULL,   -- scope to one Nova workspace, not global
  tool_name    VARCHAR(64)  NOT NULL,   -- e.g. 'query_execute'
  grant_scope  VARCHAR(32)  NOT NULL,   -- 'read_only' only; never a SQL/statement payload
  granted_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY (user_name, workspace_id, tool_name)
DISTRIBUTED BY HASH(user_name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

Why each decision:

- **Key is `(user_name, workspace_id, tool_name)`** — a grant made in one workspace must not leak
  to another; the tool name keeps future tools independent.
- **`grant_scope`, not `allowed_sql`** — the schema physically cannot hold a statement, a result,
  or a credential. This is the same posture as `CONFIG_TASK*`, whose header comment
  (`docker/init-nova.sql:181-183`) states "no password/token/secret/credential column exists".
- **Placement under `CONFIG_`** matches Nova's existing preference/CRUD family
  (`CONFIG_USER_PREFERENCES`, `docker/init-nova.sql:86`).
- **No `conversation_id`** — conversations themselves are a separate storage question
  (architecture workstream §9), and a grant keyed to a conversation id would defeat the purpose of
  persistence.

### 2.5 Option matrix — permission mechanism

| # | Option | Effort | Risk | Dependencies | Recommendation |
|---|---|---|---|---|---|
| **A** | Per-call ask only (no memory) | **S** | Low safety risk; high UX friction, users start auto-clicking | assistant conversation UI (frontend workstream) | Fine as v0; not shippable as final UX |
| **B** | **In-memory `read_only` always-allow, conversation scope** | **S–M** | Low. No persistence, no credential surface. Lost on restart. | conversation object in assistant runtime | **Recommended default** |
| **C** | Persistent always-allow in `NOVA_SYSTEM` (table above) | **M** | Medium — a durable "trust this tool" record; needs a revocation UI and audit | E2 decision + grant management UI + audit event | Only if the user explicitly wants persistence |
| **D** | Reuse `CONFIG_USER_PREFERENCES` with a JSON value | **S** | Medium — a free-text store invites someone to put SQL in it, which is the credential-leak path | none | **Do not use** |

**Effort key:** S ≈ days, M ≈ 1–2 weeks, L ≈ multi-week. (Same key as the architecture workstream.)

---

## 3. Mandatory constraint: RBAC stays the source of truth (delegate-first)

Nova's existing delegate-first pattern is documented in `backend/app/modules/task_orchestration/credentials.py`
and `execution.py`: work runs on the **owner's** connection so the engine enforces privileges and
records the real submitter. The same rule applies to the assistant:

### 3.1 How the design guarantees it

- The tool signature takes the **authenticated user context** (`username`, `encrypted_password`,
  `session_id`, `active_role`) from `get_current_user` (`backend/app/core/deps.py:15`) and passes
  it straight to `QueryService.execute_statements`. There is no code path that substitutes a
  service/root identity.
- `execute_as_user` opens `db.user_conn(username, password)` (`repository.py:161-165`). The role,
  if present, is applied with `SET ROLE` on that same user connection (`repository.py:185-186`).
- The system pool (`db.system_conn`) is used only for `NOVA_SYSTEM` metadata, never for
  user-facing SQL. The assistant must not call `execute_as_system` for anything the user asked to
  run.

### 3.2 What happens for DDL outside the user's authority

The engine refuses it. `SUBMIT TASK`/privilege checks in Nova already demonstrate the expected
behaviour: "if the owner's grants do not cover the body, the engine rejects the submit and the node
fails" (`execution.py:6-7`). For the assistant:

- The refusal surfaces as a `StarRocksError` from `repository.py:210-211` and is turned into an
  `error` on the `QueryResult`, then into `QueryResponse.error` (`router.py:117`).
- The assistant must present it as an engine permission error, and — critically — must **not**
  retry with elevated credentials. Design rule: a permission error terminates the tool-use loop for
  that statement.
- Rate-limit/repeat protection in §6 prevents an agent loop from hammering a refused statement.

---

## 4. Statement allow/deny list

This is a **policy layered on top of** the existing guard, not a replacement. The guard
(`sql_guard.py`) must not be weakened; the assistant adds a narrower allowlist so the model cannot
propose statements Nova will never auto-run.

### 4.1 Allowed (auto-runnable when a grant covers `read_only`)

| Statement | Notes |
|---|---|
| `SELECT` / `WITH … SELECT` | the primary use case; `max_rows` bounds what reaches the model |
| `SHOW` (`SHOW TABLES`, `SHOW GRANTS`, `SHOW CREATE TABLE`, …) | metadata discovery; `SHOW DATABASES` is filtered by the proxy only, the web path returns what the user may see |
| `DESCRIBE` / `DESC` | schema discovery (`service.py:1283` uses `DESC` internally) |
| `EXPLAIN` | plan inspection; already a first-class service method (`service.py:885`) |

### 4.2 Denied outright for the assistant (never auto-run, never covered by always-allow)

| Statement | Why |
|---|---|
| `DROP …` | destructive; also the only family the guard specially protects (`BLOCKED_PATTERNS`) |
| `TRUNCATE …` | destructive |
| `DELETE …` | destructive; unscoped form also triggers `UNSCOPED_MUTATION_PATTERN` |
| `UPDATE …` | destructive; unscoped form same as above |
| `ALTER TABLE … DROP …` | destructive (`DESTRUCTIVE_SQL_PATTERN`) |
| `ALTER ROLE ACCOUNTADMIN` / `DROP ROLE ACCOUNTADMIN` / `REVOKE … ACCOUNTADMIN` | hard-blocked by the guard; the assistant must surface the refusal, never attempt a workaround |
| `GRANT` / `REVOKE` | privilege mutation; admin surface, not an assistant action |
| `SET` | session mutation; the proxy owns `SET` semantics (`proxy/session.py`), an assistant `SET ROLE` could be used to change authorization mid-conversation |
| `CREATE` / `INSERT` / `COPY INTO` | write path; not in the read-only default. `CREATE ML_MODEL` and `CREATE TASK` are intercepted Nova DDL (`service.py:234,251`) and are out of scope for v1 |

**Important:** the *deny* list is a default, not a security boundary by itself. The security
boundary is that the statement runs on the user's connection, so even a `DROP` the user is
entitled to perform is permitted by the engine — which is why the assistant's own policy keeps
destructive statements behind per-call confirmation. The guard remains the last line for the
ACCOUNTADMIN/root protections it already owns.

### 4.3 Where the policy check goes

A new function in the assistant runtime (not in `sql_guard.py`, which must stay a security
primitive) that classifies the **first keyword** of each statement using `split_sql_statements`
(`sql_guard.py:538`) and refuses anything outside the allowlist. Reusing the splitter means a
multi-statement payload cannot smuggle a denied statement behind an allowed one.

---

## 5. Audit

### 5.1 Existing trail

`write_audit_log` (`backend/app/common/audit.py:10`) writes to `NOVA_SYSTEM.AUDIT_LOG`
(`docker/init-nova.sql:311`). Every `query_execute` invocation already produces a row through
`QueryService.execute`: success (`service.py:363`), engine error (`service.py:381`), and pre-engine
refusal (`service.py:398`). `sql_text` is the user's statement; `rewritten_sql` is the **redacted**
post-translation form (`service.py:343,371`).

### 5.2 What the assistant must add

The existing rows do not record that a statement came from the assistant, nor which tool call and
conversation it belonged to. Audit completeness requires linking the two without adding credential
surface. Options:

- **Recommended (no schema change):** pass a correlation identifier through the existing
  `session_id`/`file_id` columns. The assistant sets `session_id` to the assistant conversation id
  (the column is `VARCHAR(64)`, `init-nova.sql:327`), so query rows and assistant actions join on
  one key.
- **Explicit (schema change, own issue):** add an `event_type='assistant_tool'` row per tool call
  capturing: `user_name`, conversation id, tool name, decision (`allow_once` / `allow_session` /
  `deny`), the SQL **redacted via `redact_sql_credentials`**, and outcome
  (`success`/`error`/`denied`). This gives a consent audit trail separate from the execution trail.

### 5.3 Payload constraints (credential-invisible)

- The tool-call record stores the **redacted** SQL, using the existing
  `redact_sql_credentials` (`sql_guard.py:471`) — the same function the query pipeline uses. Do not
  write a second redactor.
- Never store the user's `encrypted_password`, any `FILES()` credential, or LLM API keys. API keys
  for providers are already encrypted at rest (`common/crypto.py:33`); the assistant must read them
  through `AIService.get_provider_api_key` (`modules/ai_ml/service.py:94`) and never echo them.
- Never persist raw result rows into the audit trail. `AUDIT_LOG` has no result column by design;
  keep it that way.
- The model context may contain redacted SQL and result data the user is entitled to see; it must
  **never** contain injected `FILES()` credentials. Since the pipeline redacts before the result
  leaves `QueryService`, and tools receive the `QueryResult` (already redacted via
  `repository.py:68`), this is structurally satisfied — as long as the tool passes the *result
  object*, not a re-rendered engine statement.

---

## 6. Rate limit / cost guardrails

| Guardrail | Proposal | Rationale |
|---|---|---|
| Tool-call iterations per turn | hard cap (e.g. 8–10) | bounds LLM spend and prevents runaway agent loops |
| Wall-clock per tool call | timeout around `execute_statements` | a slow query must not hang the conversation; note the pipeline has no timeout today (**gap — see §8**) |
| `max_rows` returned to the model | small, e.g. 50–100, below the API's own 500 default (`router.py:32`) | result rows are context tokens; the model rarely needs 500 |
| Result byte cap | truncate with an explicit marker | protects context window and cost |
| Denied/refused statement retries | fail closed after 1 repeat | a model that keeps retrying a refused statement is a loop, not progress |
| Concurrent tool calls | serialize per conversation | avoids interleaved partial state in the UI |

These are assistant-runtime policies; none of them changes `QueryService` behaviour. The `max_rows`
cap must be passed down as the `max_rows` argument (`service.py:184` → `repository.py:193`) so it is
enforced at fetch time, not after the rows are already in memory.

---

## 7. Security risks and mitigations

| # | Risk | Mitigation | Code that handles it today |
|---|---|---|---|
| R1 | **Prompt injection from table/file content** read by the model ("ignore previous instructions, DROP …") | The injected instruction cannot exceed the user's rights, so the blast radius is the user's own data; destructive statements still require per-call confirmation; the assistant never auto-runs anything in the deny list. Treat all result content as untrusted input in the system prompt. | **Partial.** `guard_user_statement` blocks the ACCOUNTADMIN/root class; no assistant-specific injection handling exists yet (assistant does not exist) — **not handled, by design in this doc** |
| R2 | **Chained tool calls escalating privilege** | Serialized calls; single allowlist classifier per statement; a permission error aborts the loop; `SET ROLE` is denied so authorization cannot be changed mid-conversation | **Partial.** Delegate-first means every call re-checks against the user's connection (`repository.py:161`); no chaining logic exists yet |
| R3 | **Credential leak via error / redaction** | Errors are redacted before surfacing: engine errors go through `redact_sql_credentials` (`execution.py:113-121`, `_redact`); the proxy returns generic errors (`proxy/executor.py:220`); `redact_sql_credentials` **fails closed** with `CredentialsRedactionError` if a value survives (`sql_guard.py:495-499`) | **Yes** — `sql_guard.py:471`, `repository.py:68` |
| R4 | **Credential leak via conversation history** | The tool passes the already-redacted `QueryResult`; `executed_sql` is redacted at construction (`repository.py:68`); conversation storage must store only the redacted form | **Yes, if the assistant reuses `QueryResult`** |
| R5 | **Statement smuggled past the allowlist** | Classify every statement from `split_sql_statements`; never classify the raw blob | **Yes** — reuse `sql_guard.py:538` |
| R6 | **Guard weakened to make the assistant "work"** | Guard code is not modified; policy is layered above it. Any PR touching `BLOCKED_PATTERNS` requires review against `AGENTS.md` §6 | **Yes** — `sql_guard.py:70` |

---

## 8. Open questions / not yet known

- **No query timeout in the pipeline.** `execute_as_user` sets connection/timeouts only for connect
  (`repository.py:160-172`); there is no statement timeout. A conversation tool needs one, but
  adding it is a change to shared code — **not decided here**.
- **No cancellation path.** The proxy and web path do not expose a query-cancel handle; a streaming
  assistant "stop" button cannot today cancel an in-flight StarRocks query.
- **No `SET ROLE` policy confirmation.** The deny list above denies `SET` for the assistant, but
  whether the assistant should instead *pin* the user's active role for the conversation (and how
  that interacts with the worksheet) is unresolved.
- **Conversation storage** (where tool calls, redacted SQL, and messages live) is the architecture
  workstream's question; this document assumes a conversation object exists and only describes what
  may be stored in it.
- **`docs/sql_docs/`** (NOVA-59) does not exist at this revision — `ls nova/docs/sql_docs` returned
  no such directory. The skill-context design is therefore out of scope here; it is workstream 1.
- **`ACCOUNTADMIN` immutability is enforced by regex guard**, not by the engine for the assistant's
  custom statements; the guard is the current protection (`sql_guard.py:70-115`) and is assumed
  sufficient.
- **LLM provider credential isolation** (E4) is unresolved; `get_provider_api_key`
  (`modules/ai_ml/service.py:94`) returns the decrypted key to any in-process caller. The assistant
  must not log or persist it, but the boundary is procedural, not structural, today.

---

## Appendix — Code reference index

| Concern | Location |
|---|---|
| HTTP entrypoint | `backend/app/modules/query/router.py:65` |
| Multi-statement orchestration | `backend/app/modules/query/service.py:453` |
| Single-statement pipeline | `backend/app/modules/query/service.py:176` |
| Guard invocation | `backend/app/modules/query/service.py:219`; `backend/app/modules/query/sql_pipeline.py:68` |
| Guard rules | `backend/app/common/sql_guard.py:70`, `:296` |
| Destructive / unscoped patterns | `backend/app/common/sql_guard.py:333`, `:339` |
| @stage translation | `backend/app/modules/query/sql_pipeline.py:98` |
| Credential injection | `backend/app/modules/query/dialect/injector.py:26` |
| Credential redaction | `backend/app/common/sql_guard.py:471`, called at `backend/app/modules/query/repository.py:68` |
| User-scoped execution | `backend/app/modules/query/repository.py:125` |
| System-scoped execution (metadata only) | `backend/app/modules/query/repository.py:89` |
| Proxy delegation | `backend/app/proxy/executor.py:204` |
| Audit write | `backend/app/common/audit.py:10` |
| Audit table DDL | `docker/init-nova.sql:311` |
| Auth dependency | `backend/app/core/deps.py:15` |
| Delegate-first credentials | `backend/app/modules/task_orchestration/credentials.py:1`, `execution.py:176` |
| AI provider keys | `backend/app/modules/ai_ml/service.py:94`; `backend/app/common/crypto.py:33` |
| Config table family | `docker/init-nova.sql:86` (`CONFIG_USER_PREFERENCES`), `:112` (`CONFIG_AI_PROVIDERS`) |
