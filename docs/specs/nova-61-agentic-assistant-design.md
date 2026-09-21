# Phase 10 — Agentic Assistant (bounded) — NOVA-61 design

> Implementation contract for the bounded Coco-style SQL assistant.
> Status: **decisions taken; E3b/E1a/E2b/E5a confirmed 2026-09-18.**
> Pinned to the locked v1 subset. Every code reference was read at the
> revision named in §10, not recalled.
>
> This spec is the **Stage A (T-A0)** gate for NOVA-61. Stage B–E are dispatched
> from here; nothing is implemented yet.

---

## 0. What was decided, and against which criteria

The user asked for a Coco-style agentic assistant: capable skills (SQL), tools
(query execute with permission and "always allow" per session), and mature
UI/UX. The research (four docs, §10) converted that into six decisions. The
human owner answered them on 2026-09-18:

| # | Decision | Chosen option | Optimised for | Cost accepted |
|---|---|---|---|---|
| **E3** | Product position | **E3b** — re-scope roadmap row #20 from `DEFER`/`NOT_APPLICABLE` to a bounded **Phase 10**; **not** full CoCo parity | useful (the user explicitly asked), clean architecture | Roadmap decision reversal, recorded in `README.md` Decision Log; scope must be held to the subset below or it creeps to a multi-phase programme |
| **E1** | Execution surface | **E1a** — assistant executes **read-only** SQL itself, per-call approval | useful, performance | Needs the approval card + consent state; research designed for this (CoCo/VS Code do the same) |
| **E2** | `always allow` scope | **E2b** — **per conversation, in-memory, read-only** | resource, clean architecture | Grants die on restart; users re-approve. Smallest credential surface |
| **E5** | Conversation state | **E5a** — **in-memory only**; conversations die on restart | resource, clean architecture | No history across restarts; no `NOVA_SYSTEM` thread tables in v1 |
| **E4** | LLM provider identity | **E4a** — reuse `NOVA_SYSTEM.CONFIG_AI_PROVIDERS` | resource, clean code | Assistant shares quota/rate limit with AI SQL functions |
| **E6** | LLM trace storage | **E6a** — do **not** store prompt/response traces | clean architecture | No product observability of the model loop in v1; debug via server logs only |
| **B1** | Icon language (design system) | `Bot` = LLM/assistant, `Sparkles` = ML, `Wand2` = AI-function authoring | clean code | The 14 legacy icon sites are **not** modified; only the new assistant surface uses `Bot` |

E4 and E6 were not in the routing-fix mention line (which pinned E1a/E2b/E5a);
they are listed here because the roadmap's own recommendation is E4a/E6a and
T-B1/T-B2 cannot be built without them. If the human owner overrides E4 or E6,
the affected sections are §3.1 (provider wiring) and §8 (observability) only.

### 0.1 Locked v1 subset (the scope fence)

**In v1:**

- SQL Q&A — the assistant can read the attached schema context and answer.
- Dialect-aware authoring — the assistant proposes SQL that respects Nova's
  surface (`@stage`, `AI_*`, `ML_PREDICT`; no invented StarRocks syntax).
- **One** read-only tool, `query_execute`, with per-call consent and an
  in-memory, conversation-scoped, read-only "always allow".
- Skills — `docs/sql_docs/` retrieved as context (Stage E; blocked on NOVA-59).

**Out of v1** (every item from roadmap §5, restated as a checklist so a reviewer
can assert it):

- [ ] Not in v1: agent framework (LangGraph / Pydantic AI) — no framework.
- [ ] Not in v1: sidecar `nova-agent` runtime.
- [ ] Not in v1: `Bypass approvals` global mode.
- [ ] Not in v1: persistent "always allow" across restarts/devices.
- [ ] Not in v1: browser / file / clipboard tools.
- [ ] Not in v1: `CREATE TABLE`-by-prompt, dbt, notebook.
- [ ] Not in v1: `StarRocks/mcp-server-starrocks` as the tool path.
- [ ] Not in v1: query timeout / cancellation in shared `QueryService`.
- [ ] Not in v1: `react-markdown` (plain text + code blocks suffice).
- [ ] Not in v1: LLM trace storage (E6a).
- [ ] Not in v1: write/DDL execution (the deny list in §5.2).

Any change to this list is a new E-decision and reopens T-A0, not a Stage B–E
implementation detail.

---

## 1. Architecture (E3b + E4a)

**Chosen: in-FastAPI module with its own bounded loop** (research §2 option a;
architecture matrix "ADOPT v1").

```
React assistant panel (Stage D)
  │  POST /api/v1/assistant/threads/{id}/messages   → SSE stream
  │  fetch + ReadableStream, AbortController for stop
  ▼
backend/app/modules/assistant/          ← NEW module (Stage B)
  ├─ router.py        auth-required endpoints (get_current_user)
  ├─ service.py       bounded loop: plan → tool → reflect, iteration cap
  ├─ provider.py      reads CONFIG_AI_PROVIDERS via ai_ml_service
  ├─ events.py        the SSE event contract (§4)
  ├─ consent.py       permission state machine (Stage C)
  ├─ registry.py      tool registry (v1: query_execute only)
  └─ tools/query_execute.py   ← calls query_service, never a socket
  ▼
existing Nova (unchanged)
  ├─ QueryService.execute_statements()   guard → dialect → execute → redact → audit
  └─ AIService                            masked provider read (key stays backend)
```

**Why not the sidecar:** delegate-first is harder across a process boundary
(the sidecar cannot hold the user's password, so it would have to call back
into FastAPI anyway), and the real load on the FastAPI pool is unmeasured. It is
a v2 follow-on with a revisit trigger (measured agent load degrades the API
request path).

**Why not a framework:** a one-tool loop does not justify a permanent state and
tool-contract shape. Recorded as a revisit trigger (multi-agent / durable
resumable graphs).

### 1.1 Module registration

`backend/app/main.py:124-148` mounts modules under `/api/v1`. Stage B adds:

```python
app.include_router(assistant_router, prefix=f"{prefix}/assistant", tags=["assistant"])
```

### 1.2 Streaming and the request path

The loop streams SSE from a normal FastAPI route. Research flagged the risk that
a long LLM loop holds a worker; v1 accepts it (E3b subset), and the mitigations
are the iteration cap and wall-clock budget (§9). The sidecar decision is
revisited only on measured evidence.

---

## 2. State model (E5a + E2b)

**v1 stores conversation state in process memory only.** There are no
`CONFIG_ASSISTANT_*` tables in v1. The consequences, stated explicitly:

- A restart loses all conversations and all grants. Accepted (E5a/E2b).
- Nothing to redact at rest, because nothing is at rest. The credential surface
  is therefore the **in-memory prompt/context and the outbound provider request**,
  not a table.
- Multi-worker deployment (more than one uvicorn worker) would break thread
  affinity — a thread created on worker A is invisible to worker B. **v1 assumes
  a single web worker for the assistant**, or sticky routing. This is a real
  constraint and is listed in §9. (The alternative — E5b persistence — is the
  follow-on that removes it.)

### 2.1 In-memory shapes

These are data classes, not tables. Exact field names are the contract for
Stage B/C.

```text
AssistantThread
  thread_id: str            # uuid4
  user_name: str            # from get_current_user
  workspace_file_id: str|None   # active worksheet at creation, for context
  title: str                # auto from first user message, editable
  created_at, updated_at
  messages: list[AssistantMessage]
  consent: ConsentPolicy    # §6 — lives and dies with the thread (E2b)

AssistantMessage
  message_id: str
  role: Literal["user","assistant","tool"]
  content: str              # assistant/user text; plain text, no HTML
  tool_call: ToolCallView|None
  created_at

ToolCallView                # client-facing; mirrors the UI workstream contract
  tool_name: str
  sql_preview: str          # REDACTED SQL only
  classification: Literal["read_only","destructive","denied"]
  status: Literal["pending","approved","denied","running","done","failed","cancelled"]
  result_summary: str|None  # row count / affected / error text; never rows
  error: str|None
```

### 2.2 What may enter context or be stored (hard rule)

Only the **redacted** form of any statement may enter a thread, a tool card, an
event, or a provider request. `QueryResult.executed_sql` is redacted at
construction (`backend/app/modules/query/repository.py:68-69`), so the tool
passes the result object and never the engine statement. The rule is enforced
by a test in Stage C, not by convention.

**Never** in a thread, event, provider request, or log:

- `encrypted_password`, the decrypted StarRocks password, or a session token.
- `PreparedSQL.engine_sql` (the post-injection statement that carries FILES()
  credentials).
- LLM provider API keys. `AIService.get_provider_api_key`
  (`backend/app/modules/ai_ml/service.py:94`) returns plaintext to in-process
  callers; the assistant module must hold it only for the duration of one
  outbound request and never log or persist it.
- Result rows containing credential-shaped columns. v1 sends a row-count
  summary and at most a bounded preview through a value redactor (§5.4).

---

## 3. Provider wiring (E4a)

- Provider and model are read from the existing `NOVA_SYSTEM.CONFIG_AI_PROVIDERS`
  / `CONFIG_AI_MODELS` (`docker/init-nova.sql:112,126`).
- The assistant module calls `AIService` (`backend/app/modules/ai_ml/service.py:46`)
  for the masked read and `get_provider_api_key` (`:94`) for the outbound call.
- No new provider table. No second credential to manage (that was E4b).
- The key is never returned on any assistant endpoint and never logged. The
  masking path already exists (`_mask_api_key`, `ai_ml/service.py:112`); Stage B
  adds tests asserting the assistant endpoints reuse it.

**Shared quota is accepted.** The assistant and AI SQL functions pull from the
same provider; a `429` is surfaced to the user as a transient error, not retried
in a hot loop.

---

## 4. Event contract (SSE)

One stream per assistant turn, `Content-Type: text/event-stream`, emitted by
`POST /api/v1/assistant/threads/{thread_id}/messages`. The client consumes it
with `fetch` + `ReadableStream` (the UI workstream chose this over `EventSource`
because Nova's bearer token lives in Zustand, not a cookie, so `EventSource`
cannot send `Authorization`). **No auto-reconnect** — a replayed stream could
duplicate a tool call.

Event names and payloads are frozen here; Stage B implements and Stage D
consumes them.

| `event:` | Payload | When |
|---|---|---|
| `text_delta` | `{ "text": "..." }` | assistant text increment |
| `tool_call` | `ToolCallView` with `status:"pending"`, `sql_preview` **redacted**, `classification` | the model proposed a tool call; the stream pauses for consent |
| `tool_progress` | `{ "tool_call_id", "stage", "text", "sql_preview"? }` | observable tool lifecycle; never hidden chain-of-thought |
| `tool_status` | `{ "tool_call_id", "status" }` — one of `approved/denied/running/done/failed/cancelled` | consent resolved or execution progressed |
| `content_block_done` | `{ "content_index", "content_id" }` | closes an ordered response block |
| `done` | `{ "message_id", "finish_reason" }` | the turn completed normally |
| `error` | `{ "code", "message" }` — message already redacted upstream | the turn failed |
| `ping` | `{}` | keep-alive during a long provider call |

Rules:

- Every frame in a turn carries one `run_id` and a monotonic `sequence`.
- Response content carries `content_index` and `content_id`. A higher index
  waits until every lower index exists and is closed. Arrival time never
  overrides authored order.
- Generated SQL is emitted only after credential redaction, before execution
  completes, through `tool_progress`.

- The stream pauses at `tool_call` pending consent. Consent arrives on a
  separate HTTP call (§6, `POST /api/v1/assistant/tool-calls/{tool_call_id}/decision`);
  the server then emits `tool_status` on the still-open stream. This avoids a
  half-duplex approval over the SSE channel.
- On client disconnect, the server **stops the loop** and marks the partial
  message `cancelled`, not deleted (UI requirement).
- Server-provided text is rendered as plain text by the client; the client must
  never `dangerouslySetInnerHTML` it.
- `error.message` is the already-redacted text; the loop must never format an
  error with the raw engine statement.

---

## 5. `query_execute` tool contract (E1a)

### 5.1 Call path (non-negotiable)

```python
# backend/app/modules/assistant/tools/query_execute.py   (Stage C — illustrative)
results = await query_service.execute_statements(
    sql=sql,
    username=user["username"],
    encrypted_password=user["encrypted_password"],
    database=context.database,
    schema=context.schema,
    role=context.active_role,
    max_rows=ASSISTANT_MAX_ROWS,          # §9, small
    session_id=user["session_id"],
    confirm_destructive=False,            # never auto-confirm
    file_id=context.workspace_file_id,
)
```

- The tool imports `query_service` from
  `backend/app/modules/query/service.py` (`QueryService.execute_statements`,
  line 497 at `44252d4` — the line is a hint; the symbol is the contract). It
  must **never** import `asyncmy` or `db.user_conn`, and never open a socket.
- It runs on the **requesting user's** connection, so StarRocks RBAC is the
  authorization source of truth (delegate-first, the NOVA-23 D9.4 pattern).
  There is no service-identity fallback.
- It calls the in-process service, not `POST /api/v1/query/execute` over the
  network — one less auth boundary, and the same pipeline either way.
- Guard, `@stage` translation, credential injection/redaction, and audit are all
  inside `QueryService.execute` (`service.py:219,263,281,343,363,381,398`), so
  the tool inherits them and cannot forget them.

### 5.2 Statement allow/deny (layered **above** the unchanged guard)

The classification runs on **each statement** from `split_sql_statements`
(`backend/app/common/sql_guard.py:538`) so a multi-statement payload cannot
smuggle a denied statement behind an allowed one.

**Allowed** (auto-runnable when a read-only grant covers it):

`SELECT`, `WITH … SELECT`, `SHOW`, `DESCRIBE`/`DESC`, `EXPLAIN`.

**Denied** (never auto-run; not covered by any always-allow grant):

`DROP`, `TRUNCATE`, `DELETE`, `UPDATE`, `ALTER … DROP`, `GRANT`, `REVOKE`,
`SET`, `CREATE`, `INSERT`, `COPY INTO`, and the Nova DDL interceptions
(`CREATE ML_MODEL`, `CREATE TASK`).

The existing guard is **not modified**. It remains the enforcement for the
ACCOUNTADMIN/root class (`sql_guard.py:70-115`). A denied statement is refused
by this policy before the engine is reached; a request that tries to bypass the
policy is refused by the guard. Both are audited.

### 5.3 Permission error behaviour

An engine privilege error (e.g. `5203`) is surfaced as a tool failure. The loop
**terminates** for that statement and does not retry with elevated credentials.
This is the same rule as delegate-first in `execution.py:6-7`.

### 5.4 Result returned to the model

- Row cap: `ASSISTANT_MAX_ROWS` (§9), passed as `max_rows` so the cap applies at
  fetch time (`repository.py:193`), not after the rows are in memory.
- The model receives columns + a bounded preview + row count — **not** an
  unbounded result set.
- Values that are credential-shaped are redacted before they enter context.
  This is a value-level redaction, distinct from `redact_sql_credentials`
  (which operates on SQL); Stage C owns it and tests it.
- No result rows are ever written to any persistent table (E5a/E6a: nowhere to
  write them).

---

## 6. Consent state machine (E2b)

States per tool call: `pending → approved | denied`, then
`approved → running → done | failed`, or `→ cancelled`.

The "always allow" grant:

- Scope: **this conversation only** (`AssistantThread.consent`), in process
  memory. Lost on restart (E2b).
- Coverage: **read-only statements only**. A destructive statement shows no
  always-allow control and always requires per-call approval; even a grant
  cannot auto-approve it.
- Storage: an in-memory policy object, e.g. `ConsentPolicy{always_allow: {"query_execute": "read_only"}}`.
  **No `NOVA_SYSTEM` table in v1**, and in particular **not**
  `CONFIG_USER_PREFERENCES` (research option D rejected: a free-text store
  invites someone to put SQL in it, which is the credential-leak path).
- Revocation: closing the conversation, or a "Reset permissions" control in the
  panel (Stage D). No cross-device persistence to revoke.

Consent decisions are recorded per call in memory for the UI; the durable audit
trail is the execution audit row plus, optionally, a Stage C `assistant_tool`
event (§7). No consent state is persisted in v1.

### 6.1 Wire contract (frozen)

The endpoint is scoped by `tool_call_id` alone. There is deliberately **no
`/threads/{thread_id}` segment**: the broker already binds a call to its owning
conversation, so a thread id in the path would be redundant or, worse, let a
caller name a thread the call does not belong to.

```
POST /api/v1/assistant/tool-calls/{tool_call_id}/decision
Content-Type: application/json

{ "decision": "allow_once" | "allow_session" | "deny" }
```

| `decision` | UI intent | Effect |
|---|---|---|
| `allow_once` | **Allow**, always-allow unchecked | approve this call only |
| `allow_session` | **Allow**, always-allow checked | approve + set the read-only conversation grant (E2b) |
| `deny` | **Deny** | reject this call |

Rules:

- The body is exactly one enum field. There is **no `always_allow` boolean**;
  "always allow" is expressed by selecting `allow_session`, which the backend
  only honours for a read-only statement.
- The UI keeps its own two-part intent (`approve`/`deny` plus an `alwaysAllow`
  flag) and maps it to this enum at the HTTP boundary, so the read-only gate
  stays a client concern and the wire stays a single frozen enum.
- The response is `{ "tool_call_id", "status", "grant_active" }`.
- Unknown, already-resolved, or another user's `tool_call_id` answers **404**
  (never 403), so existence does not leak (NOVA-70).

---

## 7. Audit

- Every execution already writes `NOVA_SYSTEM.AUDIT_LOG`
  (`backend/app/common/audit.py:10` → `docker/init-nova.sql:311`) via
  `QueryService` on all three paths (success `service.py:363`, engine error
  `:381`, pre-engine refusal `:398`).
- The assistant correlates its tool calls to those rows by passing the
  conversation id as `session_id` (column `VARCHAR(64)`,
  `init-nova.sql:327`). No schema change required for the correlation.
- Stage C may add an `event_type='assistant_tool'` row per call (tool name,
  decision, **redacted** SQL, outcome). This is a small, additive change; the
  decision to add it is a Stage C implementation detail, and if added it must
  reuse `redact_sql_credentials` (`sql_guard.py:471`) and never store rows.
- E6a means **no** prompt/response trace table. The model loop is not persisted.

---

## 8. Observability (E6a)

- No trace table. Debugging uses server logs.
- Logs must not contain credentials or raw engine statements. The existing
  redaction helpers are reused; the assistant must not add a second redactor.
- Provider errors are logged with the provider name and HTTP status, never the
  request body (which can contain user SQL and schema context).

---

## 9. Guardrails

| Guardrail | v1 value | Rationale |
|---|---|---|
| Iterations per turn | hard cap (default 8) | bounds LLM spend and prevents runaway loops |
| Wall-clock per turn | budget (default 60 s), then `error` + `done` | the pipeline has no statement timeout (§9.1) |
| `ASSISTANT_MAX_ROWS` | 100 (below the API's 500 default, `router.py:32`) | rows are context tokens |
| Result preview size | bounded chars, truncated with a marker | context window + cost |
| Denied-statement retries | fail after 1 repeat | a model retrying a refusal is a loop |
| Concurrent tool calls | serialize per thread | avoids interleaved partial state |

### 9.1 Known gaps carried from research (not fixed in v1)

- **No statement timeout** exists in `QueryService`; the assistant's wall-clock
  budget is a wrapper, not a query cancel. A hung query still holds the user's
  connection until the engine returns.
- **No cancellation handle**; a client abort stops the stream but cannot cancel
  the in-flight StarRocks query.
- These are explicitly out of v1 (roadmap §5) and become their own scoped change
  to `QueryService` if a conversation tool genuinely needs them.

---

## 10. Integration points (file:line index)

All verified against the research revisions (`98a5da7`, `3e4f239`, `dae2437`)
and re-checked against `main@0c55f14`.

| Concern | Location |
|---|---|
| Module registration | `backend/app/main.py:124-148` |
| Auth dependency | `backend/app/core/deps.py:15` |
| Execution entrypoint | `QueryService.execute_statements` — `backend/app/modules/query/service.py:497` |
| Guard invocation | `backend/app/modules/query/service.py:219`; `sql_pipeline.py:68` |
| Guard rules | `backend/app/common/sql_guard.py:70`, `:296` |
| Statement splitter | `backend/app/common/sql_guard.py:538` |
| @stage translation | `backend/app/modules/query/sql_pipeline.py:98` |
| Credential redaction | `backend/app/common/sql_guard.py:471`; `repository.py:68` |
| User-scoped execution | `backend/app/modules/query/repository.py:125`, `:161` |
| Audit write | `backend/app/common/audit.py:10`; table `docker/init-nova.sql:311` |
| Provider read (masked) | `backend/app/modules/ai_ml/service.py:46`, `:94`, `:112` |
| Provider/model tables | `docker/init-nova.sql:112`, `:126` |
| Panel anchor (Stage D) | `frontend/src/features/assistant/assistant-dock.tsx`, mounted in `frontend/src/components/layout/authenticated-layout.tsx` (NOVA-139: global, formerly `frontend/src/features/workspaces/index.tsx`) |
| Design-system primitives | `frontend/src/components/ui/` (Sheet `sheet.tsx:44-79`, ScrollArea, StatusBadge, ConfirmDialog) |

---

## 11. Stage plan (dispatched from this spec)

| Stage | Task | Depends on | Notes |
|---|---|---|---|
| A | **T-A0** this spec | — | done when merged |
| B | T-B1 module skeleton + provider wiring; T-B2 bounded loop + SSE; T-B3 **in-memory state** (E5a makes this S: no tables) | T-A0 | B1→B2 serialized in the module |
| C | T-C1 `query_execute` delegate-first; T-C2 consent (E2b in-memory); T-C3 audit correlation | T-A0, E1/E2 (answered) | C1→C2 serialized |
| D | T-D1 panel shell + `Bot` icon; T-D2 transcript/stream/stop; T-D3 tool card + approval; T-D4 session management | T-B2 event contract (D can start against a stub) | no design-system violation. NOVA-139 later promoted the panel from the workspace `<section>` to a global dock in `authenticated-layout.tsx`; the SSE/thread/consent contract is unchanged |
| E | T-E1 SQL skill retrieval | **NOVA-59** | blocked until `docs/sql_docs/` lands; advisory only — invariant enforcement stays in `sql_guard.py` |

Shared module: `backend/app/modules/assistant/`. B1/B2 and C1/C2 serialize
within it. C touches `backend/app/modules/query/` read-mostly (calls the
service); any change to that directory serializes against Phase 1 work.

**Sequencing:** T-A0 lands first (this document). Then B and D in parallel (D
against a stub stream), then C (E1/E2 now answered), then E when NOVA-59 lands.

---

## 12. Still unknown / not verified

- Real agent-loop load on the FastAPI connection pool — unmeasured; this is the
  sidecar revisit trigger.
- Whether a multi-worker assistant deployment is required in practice (see §2 —
  v1 assumes single worker or sticky routing).
- Exact panel widths in CoCo/Copilot (undocumented); Nova's `w-[22rem]` is a
  proposal, not a copied number.
- Whether `Ctrl/Cmd + J` is conflict-free against all Monaco bindings.
- `docs/sql_docs/` (NOVA-59) is authored on its own branch (`932f800` /
  `089b62c`) but **is not on `main`** at this spec's revision; the skill
  retrieval integration is Stage E and its staleness strategy is not yet
  exercised.
- `get_provider_api_key` returns plaintext to any in-process caller
  (`ai_ml/service.py:94`); the isolation boundary is procedural, not structural
  (E4). Not tightened in v1.
- No statement timeout / cancel handle in the pipeline (§9.1).

---

## Provenance

| Input | Revision / path |
|---|---|
| Cocoo research | `docs/research/agentic-assistant-snowflake-coco.md` @ `dae2437` |
| Architecture options | `docs/research/agentic-assistant-nova-architecture.md` @ `dae2437` |
| UI/UX sidebar | `docs/research/agentic-assistant-uiux-sidebar.md` @ `3e4f239` |
| Query tool + permissions | `docs/research/agentic-assistant-query-tool-permissions.md` @ `98a5da7` |
| Roadmap phase plan | `docs/research/agentic-assistant-roadmap.md` @ `1918a27` |
| Spec pattern | `docs/specs/nova-23-task-orchestration-design.md` |
| Repo re-check | `0c55f14` (main) |
