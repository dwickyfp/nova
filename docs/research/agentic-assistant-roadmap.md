# Roadmap conversion: Coco-style agentic assistant (NOVA-61)

> **Workstream 4 of NOVA-61 — owner: Nova Project Lead (Team Lead).**
> **Inputs (all read at their PR heads, 2026-09-18):**
> - `docs/research/agentic-assistant-snowflake-coco.md` (PR #71)
> - `docs/research/agentic-assistant-nova-architecture.md` (PR #71)
> - `docs/research/agentic-assistant-uiux-sidebar.md` (PR #70)
> - `docs/research/agentic-assistant-query-tool-permissions.md` (PR #69)
>
> **What this document is:** the conversion of three research outputs into (a) a
> proposed roadmap entry, (b) a staged, PR-sized task backlog with delegation-ready
> specs, and (c) the open decisions the Team Lead cannot answer alone. It changes
> **no production code**, proposes **no merge**, and does **not** mark anything
> `done`. It is the draft the next session executes against once the human
> decisions in §6 are answered.

---

## 0. Bottom line

The three research workstreams converged on a design that is **small, reuses the
existing pipeline, and adds no credential surface**. The recommendation is
therefore to schedule a **new roadmap phase — Phase 10, "Agentic assistant"** —
as a **subset scope** (SQL assistant + one permissioned tool + skills), **not**
full CoCo parity (coding + admin + dbt + notebook + browser).

That recommendation **contradicts a standing roadmap decision**: matrix row **#20
Agent platform (CoCo / Cortex)** is currently `DEFER` / `NOT_APPLICABLE`
(`docs/roadmap-snowflake-parity.md:147`, `:330`). This document does **not** flip
that silently. It is surfaced as decision **E3** in §6 and must be answered by the
human owner before any implementation task is created.

---

## 1. What the research settles (verified, load-bearing)

Every claim below is traceable to a research doc; the ones marked **[K]** are
independently re-checked against the repo by this document.

| # | Finding | Source | Confidence |
|---|---|---|---|
| F1 | "Coco" is a product name for **Cortex Code**, built on **Cortex Agents**; three surfaces (Snowsight right panel, Desktop IDE, CLI) | coco §1–2 | high |
| F2 | CoCo **executes SQL/DDL from the panel** and has a documented, GA consent model: *Allow Once / Allow all in this chat / Always Allow*, plus approval modes | coco §4.2 | high |
| F3 | CoCo auto-approves **read-only** SQL (`SELECT`/`SHOW`/`DESCRIBE`) and prompts on **write**/`USE_ROLE` | coco §4.3 | high |
| F4 | Nova's pipeline puts **guard, redaction, and audit inside `QueryService.execute`**, so any tool that calls it inherits all three | arch §8, query-tool §1 | high |
| F5 | The **same in-process entrypoint the worksheet uses** is `QueryService.execute_statements()`; the proxy is a second caller, not a bypass | query-tool §1.2 | high |
| F6 | **Delegate-first** is the only RBAC story: the tool runs on the **user's** connection, never a service identity | query-tool §3, arch §2 | high |
| F7 | **All design primitives the UI needs already exist** (`Sheet`, `ScrollArea`, `StatusBadge`, `EmptyState`, `Collapsible`, `ConfirmDialog`); only four new components — all compositions | uiux §5.1 | high |
| F8 | The **right panel is the dominant pattern** for this product class, non-modal, context-bound to the active file | uiux §1 | high |
| F9 | **No auto-reconnect** in v1: agentic auto-reconnect can duplicate tool calls | uiux §2.2 | high |
| F10 | **Invariants cannot live in the prompt.** Skill docs are advisory; `sql_guard.py` is enforcement | coco §4.3, arch §6 | high |

### 1.1 Re-checked facts from this document (repo read at `0c55f14`)

| Claim | Verification | Verdict |
|---|---|---|
| `docs/sql_docs/` does not exist | `ls docs/sql_docs` → no such directory | **confirmed absent** — NOVA-59 has not landed; the SQL skill is a design, not a source |
| `#20` is `DEFER` / `NOT_APPLICABLE` | `docs/roadmap-snowflake-parity.md:147`, `:330` | **confirmed** — needs E3 |
| Engine pin is `4.1.4` | `README.md:543` (NOVA-51, commit `4a9848e`) | **confirmed** — supersedes the `4.1.1` note in the research docs' provenance |
| `README.md` Roadmap is the canonical checkbox surface; `docs/roadmap-snowflake-parity.md` is the phase/strategy plan | `README.md:536-538`, `:540` | **confirmed** — both must be updated together |
| LLM providers already exist (`CONFIG_AI_PROVIDERS`) with masked keys | `arch §1` citing `ai_ml/service.py:46-99,112-118` | accepted from research; not re-run here |

---

## 2. Proposed roadmap entry (draft — not applied)

> **Status: PROPOSED.** This section is what the roadmap edits become **after**
> E1–E4 are answered. It is written in the exact shape the README and
> `roadmap-snowflake-parity.md` use, so the apply step is mechanical.

### 2.1 Phase 10 — Agentic assistant (proposed)

**Purpose:** give Nova a right-side, non-modal assistant that can answer questions
about the workspace, help author SQL against the Nova dialect, and — with explicit
per-call consent — execute **read-only** SQL through the existing query pipeline.
It is deliberately **not** a coding agent, an admin copilot, or an autonomous
executor.

**Naming note:** the research consistently calls the engine-side model a *loop*
(plan → tool → reflect). That is agentic behaviour. Nova's v1 is a **bounded
agent loop with one tool and a hard iteration cap**, not a framework-based agent
platform. The phase name should carry that: **"Agentic assistant (bounded)"**.

**Proposed canvas items (each is a matrix-style row; number `#25+` pending the
NOVA-49 matrix update):**

| Item | Capability | Strategy | Effort | Prio | Depends on |
|---|---|---|---|---|---|
| **A1** | Assistant backend module + bounded agent loop over existing LLM providers | `NOVA_NATIVE` | M | P1 | E3, E4 |
| **A2** | `query_execute` tool (delegate-first, read-only allowlist, per-call consent) | `NOVA_NATIVE` | M | P1 | A1, E1, E2 |
| **A3** | Assistant right panel UI (transcript, streaming, tool-call/approval card) | `NOVA_NATIVE` | M | P1 | A1 |
| **A4** | Conversation + consent state in `NOVA_SYSTEM` (or explicitly not) | `NOVA_NATIVE` | S–M | P1 | E2, and E5 below |
| **A5** | SQL skill context from `docs/sql_docs/` (retrieval-first) | `NOVA_NATIVE` | S–M | P2 | NOVA-59 (external) |
| **A6** | Assistant audit trail + cost/iteration guardrails | `NOVA_NATIVE` | S | P2 | A1, A2 |

**Deliberately not in Phase 10 v1** (parity items the research describes but Nova
should not build yet): browser tool, file/clipboard tools, dbt scaffolding,
notebook authoring, `CREATE TABLE`-by-prompt, admin/governance copilot actions,
`Bypass approvals` global mode, persistent "always allow", sidecar runtime, and
any agent framework. Each is recorded in §5 as a revisit-triggered follow-on, not
as an omission.

### 2.2 Roadmap file edits the apply step will make

| File | Edit | Why |
|---|---|---|
| `docs/roadmap-snowflake-parity.md:147` | `#20 … DEFER / NOT_APPLICABLE` → `#20 … SUPERSEDED_BY Phase 10 (subset)` | E3 answered "yes" means this decision is reversed and must say so |
| `docs/roadmap-snowflake-parity.md:330` | same as above in the `DEFER` table | keep the two tables consistent |
| `docs/roadmap-snowflake-parity.md` §2 or new §4.1 | add Phase 10 block from §2.1 | phase plan |
| `README.md` (after Phase 9) | add `### Phase 10 — Agentic assistant (bounded) 🔶` with `[ ]` items and evidence paths | canonical checkbox surface |
| `README.md` Decision Log | add a NOVA-61 decision row (E1–E4 outcomes) | durable decision record |
| `docs/agents/` or `docs/specs/` | new spec `nova-61-agentic-assistant-design.md` after E-decisions | the implementation contract; pattern follows `docs/specs/nova-23-task-orchestration-design.md` |

**Sequencing with the current roadmap:** Phase 10 is **P1 but not critical path**.
It must not displace Phase 0 (#2/#9) or Phase 1 (#3/#4/#7/#15/#16/#12-9b). The one
hard external dependency is **A5 → NOVA-59**; A1–A4/A6 do not depend on NOVA-59 and
can start once the E-decisions land.

---

## 3. Task backlog (PR-sized, delegation-ready)

Ordering is serial where the docs say modules are shared. Each task is sized to one
PR. **None of these is dispatched by this document** — they are created as
sub-issues only after §6 is answered (see §7 for why).

### Stage A — Decision freeze + spec (no code)

**T-A0 — Write the Phase 10 design spec.**
- **Goal:** produce `docs/specs/nova-61-agentic-assistant-design.md` fixing E1–E4 outcomes, the tool contract, the event contract, and the state schema.
- **Context:** all four research docs; pattern `docs/specs/nova-23-task-orchestration-design.md`.
- **Constraints:** no code; no credential examples; placeholders only.
- **Acceptance:** spec answers E1–E4 with the chosen option and states the trade-off; every "not in v1" from §2.1 is listed.
- **DoD:** doc committed; roadmap edits from §2.2 applied; `Refs NOVA-61`.
- **Sizing:** S. **Owner suggestion:** Team Lead (this role).

### Stage B — Backend assistant loop (needs E3, E4)

**T-B1 — Assistant module skeleton + provider wiring.**
- **Goal:** `backend/app/modules/assistant/` with a router `/api/v1/assistant`, thread CRUD, and a call path to the provider configured in `NOVA_SYSTEM.CONFIG_AI_PROVIDERS`.
- **Context:** `backend/app/modules/ai_ml/service.py:46-99,112-118` (provider read + masking), `backend/app/main.py:124-148` (module registration), `arch §2`.
- **Constraints:** no new LLM provider table; **never** return `get_provider_api_key()` output to a caller outside the module; provider key never logged.
- **Acceptance:** auth-required endpoints; a thread can be created/listed/deleted; a dry call to the configured provider succeeds without exposing the key; unit tests for the masking path.
- **DoD:** ruff + mypy + unit green; no key material in any response/log.
- **Sizing:** M.

**T-B2 — Bounded agent loop + SSE event stream.**
- **Goal:** plan → tool → reflect loop with a hard iteration cap, emitting a single SSE stream (`text_delta`, `tool_call`, `tool_status`, `done`, `error`) consumed by the panel.
- **Context:** `uiux §2.2` (fetch + `ReadableStream`, **no** auto-reconnect), `query-tool §6` (iteration cap, wall clock, row caps), `arch §2`.
- **Constraints:** no reconnect; serialize tool calls per conversation; cap iterations and rows (defaults in `query-tool §6`); cancellation via `AbortController`, and the server must stop the loop on disconnect.
- **Acceptance:** stream contract tests; a runaway loop terminates at the cap; abort leaves partial state marked cancelled, not deleted.
- **DoD:** unit + contract tests; event contract documented in the spec.
- **Sizing:** M.

**T-B3 — Assistant/consent state in `NOVA_SYSTEM` (conditional on E2/E5).**
- **Goal:** if persistence is chosen, add `CONFIG_ASSISTANT_THREADS` / `_MESSAGES` / `_TOOL_GRANTS` per the research schemas; if not, keep conversation state in process memory.
- **Context:** `arch §7` (threads/messages), `query-tool §2.4` (grant table keyed `(user_name, workspace_id, tool_name)` with `grant_scope` **only**).
- **Constraints:** **no column can hold a statement, result, or credential**; only redacted SQL from `QueryResult.executed_sql` may be stored; the grant table must be physically incapable of holding SQL.
- **Acceptance:** schema test asserting no credential-shaped column; round-trip test storing a redacted statement; a `grep` scan over the table after a full cycle returns no secret.
- **DoD:** `docker/init-nova.sql` updated; migration note; credential-scan test.
- **Sizing:** M (if persisted) / S (if in-memory).

### Stage C — The permissioned query tool (needs E1, E2)

**T-C1 — `query_execute` tool, delegate-first.**
- **Goal:** a tool that calls `QueryService.execute_statements()` on the requesting user's connection and never opens a socket.
- **Context:** `backend/app/modules/query/service.py:453` (entry), `:219` (guard), `:363/:381/:398` (audit), `repository.py:161` (user conn), `repository.py:68` (redaction), `sql_guard.py:471` (`redact_sql_credentials`).
- **Constraints:** single execution entrypoint — import `query_service`, never `asyncmy`/`db.user_conn`; the deny list from `query-tool §4.2` is layered **above** the unchanged guard; a permission error terminates the loop, never retries elevated.
- **Acceptance:** tool inherits guard/audit/redaction by test to the same degree as the worksheet; denied statements never reach the engine; an engine privilege error is surfaced, not retried.
- **DoD:** unit + integration tests; no new credential handling.
- **Sizing:** M.

**T-C2 — Consent layer: ask / always-allow (read-only) / deny.**
- **Goal:** the permission state machine above the pipeline, with the always-allow grant scoped exactly as E2 decides.
- **Context:** `query-tool §2` (decision states, scope table, option matrix A–D), `uiux §3.2` (card + checkbox rules).
- **Constraints:** grant covers **read-only statements only**; destructive statements keep per-call confirmation from `guard_user_statement`; do **not** reuse `CONFIG_USER_PREFERENCES` for grants (option D rejected).
- **Acceptance:** tests for allow-once, allow-session, deny; a destructive statement is never auto-approved even with a grant; the grant is lost on restart (in-memory path) or revoked by a UI action (persisted path).
- **DoD:** tests + the storage decision recorded in the spec.
- **Sizing:** M.

**T-C3 — Assistant audit trail.**
- **Goal:** link assistant-originated executions to their conversation and consent decision without new credential surface.
- **Context:** `query-tool §5` (use `session_id` correlation; optional `event_type='assistant_tool'`), `docker/init-nova.sql:311` (`AUDIT_LOG`).
- **Constraints:** reuse `redact_sql_credentials`, never write a second redactor; never store result rows.
- **Acceptance:** a conversation's tool calls are joinable to their execution rows; decision (`allow_once`/`allow_session`/`deny`) is recorded; payload scan is credential-free.
- **DoD:** tests; audit schema documented.
- **Sizing:** S.

### Stage D — Frontend panel (needs the T-B2 event contract; can start with a stub)

**T-D1 — `AssistantPanel` shell + layout integration.**
- **Goal:** right panel inside the workspace `<section>`, `Sheet` overlay under ~1024px, persisted collapsed state via the existing `PUT /workspaces/state` path.
- **Context:** `frontend/src/features/workspaces/index.tsx:1144` (`<aside>`), `:1294` (`<section>`), `:799-828` (debounced state persist), `components/ui/sheet.tsx:44-79`, `uiux §1.3` (option A).
- **Constraints:** `flex min-h-0 flex-col` + `ScrollArea min-h-0 flex-1`; no hardcoded heights; no `overflow-x-scroll`; Monaco parent gets `min-w-0`; no new dependencies.
- **Acceptance:** panel opens/closes; editor does not overflow; narrow screen uses `Sheet`; state survives reload via workspace state, not `localStorage`.
- **DoD:** component tests; no design-system violation.
- **Sizing:** M.

**T-D2 — Transcript, streaming, and stop.**
- **Goal:** `MessageList`/`MessageBubble` rendering the SSE stream, with a Stop control bound to `AbortController` and a cancelled badge on partial content.
- **Context:** `uiux §2.2`, `§3.2`; existing `runQuery` abort pattern `index.tsx:983-984,1007`.
- **Constraints:** server text rendered as plain text, never `dangerouslySetInnerHTML`; do not copy results to `localStorage`; no token-by-token live-region announcements.
- **Acceptance:** deltas append; stop works; partial state persists with a clear marker; a11y live region announces status transitions only.
- **DoD:** component tests incl. abort and partial state.
- **Sizing:** M.

**T-D3 — `ToolCallCard` + approval controls.**
- **Goal:** inline tool card showing the full SQL preview, status via `StatusBadge`, Deny/Allow, and an "Always allow" checkbox **only** for read-only statements.
- **Context:** `uiux §3.2` alternative 1 (recommended), `§5.1` (`StatusBadge` tones), `query-tool §2.3`.
- **Constraints:** destructive statements show no always-allow control; no hover-only approval path; 44×44 tap targets; badge keeps its text label (not colour-only).
- **Acceptance:** approve/deny/always-allow wiring tested; the checkbox is absent for a destructive statement; keyboard reachable.
- **DoD:** component tests; contract matches `ToolCallView` in `uiux §3.3`.
- **Sizing:** M.

**T-D4 — Session management (list/new/rename/delete/clear).**
- **Goal:** thread list in a `Sheet`/`Popover` from the panel header; delete behind `ConfirmDialog`.
- **Context:** `uiux §4.2`, `components/confirm-dialog.tsx`.
- **Constraints:** thread context bound to the active `file_id` at creation, shown as a badge, never auto-switched.
- **Acceptance:** CRUD works; delete confirms; the file badge is accurate after tab changes.
- **DoD:** component tests.
- **Sizing:** S.

### Stage E — SQL skill context (needs NOVA-59)

**T-E1 — SQL skill retrieval.**
- **Goal:** assistant retrieves relevant `docs/sql_docs/*.md` at question time and injects excerpts; a small committed summary covers stable invariants only.
- **Context:** `arch §6` (option A primary, C complement), NOVA-59 (external dependency).
- **Constraints:** **invariant enforcement stays in `sql_guard.py`** — the skill is advisory; retrieval includes the doc revision so staleness is detectable.
- **Acceptance:** an assistant answer about `@stage` cites the current doc; a prompt that asks the assistant to bypass a guard produces a refusal from the guard, not from the prompt.
- **DoD:** tests; regeneration checklist when `docs/sql_docs/` changes.
- **Sizing:** M. **Blocked until NOVA-59 lands.**

### Dependency graph

```
T-A0 (spec) ─┬─ T-B1 ─ T-B2 ─┬─ T-D2 ─ T-D3
             │               └─ T-D1 ─ T-D4
             ├─ T-B3 (E2/E5)
             ├─ T-C1 ─ T-C2
             ├─ T-C3
             └─ T-E1  ── NOVA-59 (external, not yet landed)
```

Shared modules: T-B1/B2/B3 and T-C1/C2 all touch `backend/app/modules/assistant/`
and must be **serialized** within that module. T-C1/C2 interact with
`backend/app/modules/query/` — read-mostly (call the service), but any change there
serializes against Phase 1 work on the same files.

---

## 4. Decision matrix (options, effort, risk, recommendation)

Effort key: **S** ≈ days, **M** ≈ 1–2 weeks, **L** ≈ multi-week.

### 4.1 E1 — execution surface

| Option | Description | Effort | Risk | Recommendation |
|---|---|---|---|---|
| **E1a** | Assistant executes read-only SQL directly, per-call approval | M | Medium (needs approval + audit) | **Adopt (recommended)** — matches CoCo/VS Code, and the pipeline already makes it safe |
| **E1b** | Assistant only inserts SQL into the worksheet; user runs it | S | Low | Safe fallback if E1a is rejected; loses the agentic loop's value |
| **E1c** | Hybrid: read-only executes; DDL/destructive hand off to worksheet | M | Medium | Reasonable middle ground; more UI states |

**Research alignment:** the query-tool workstream designed for E1a; the UI workstream
built its card around it. Choosing E1b invalidates T-C2's approval card (though not
T-C1/T-C3).

### 4.2 E2 — `always allow` scope

| Option | Scope | Effort | Risk | Recommendation |
|---|---|---|---|---|
| **E2a** | Ask every time | S | Lowest safety risk, high friction | v0 only |
| **E2b** | Per conversation, in-memory, read-only | S–M | Low | **Recommended default** |
| **E2c** | Per user + workspace, persisted in `NOVA_SYSTEM` | M | Medium (durable trust record; needs revocation UI) | Only if persistence is required |
| **E2d** | Global allow-all | S | **High** — widens to DDL/admin | Do not ship in v1 |

Both the UI and query-tool workstreams independently recommend **E2b**.

### 4.3 E3 — product position (the roadmap conflict)

| Option | Description | Effort | Risk | Recommendation |
|---|---|---|---|---|
| **E3a** | Keep #20 `NOT_APPLICABLE`; do not build the assistant | — | Misses the user's explicit request | Only if the request is withdrawn |
| **E3b** | Re-scope #20 to a **bounded SQL assistant subset**, new Phase 10 | M per canvas row | Scope creep if the subset is not enforced | **Recommended** |
| **E3c** | Full CoCo parity (coding + admin + dbt + notebook + browser) | L, multi-phase | High — touches almost every module | **Not recommended for v1** |

### 4.4 E4 — LLM provider identity

| Option | Description | Effort | Risk | Recommendation |
|---|---|---|---|---|
| **E4a** | Reuse `NOVA_SYSTEM.CONFIG_AI_PROVIDERS` | S | Shared quota/rate limit with AI SQL functions | **Recommended for v1** |
| **E4b** | Separate provider/identity for the assistant | M | A second credential to manage; more config surface | Defer until quota contention is real |

### 4.5 E5 — conversation state persistence (new in this document)

| Option | Description | Effort | Risk | Recommendation |
|---|---|---|---|---|
| **E5a** | In-memory only; conversations die on restart | S | Low; users lose history | **Recommended for v1** — smallest credential surface |
| **E5b** | Persist threads/messages in `NOVA_SYSTEM` | M | Medium; needs a retention policy and a credential test | Follow-on once the loop is proven |
| **E5c** | Persist **and** persist grants | M | Highest; needs revocation UI | Only with E2c |

> **Note for the human:** E5 was surfaced by the research (`arch §9` lists retention
> as an open product question) but was not one of the four original decisions. It is
> added here because T-B3 cannot be sized or dispatched without it.

### 4.6 E6 — LLM trace storage (surfaced by the coco workstream)

| Option | Description | Effort | Risk | Recommendation |
|---|---|---|---|---|
| **E6a** | Do not store prompt/response traces in `NOVA_SYSTEM` | S | Lowest credential surface; no product observability | **Recommended for v1** |
| **E6b** | Separate trace table from `AUDIT_LOG` (Snowflake's split) | M | Medium; a second place that can hold user text | Follow-on if observability is required |
| **E6c** | Store traces in `AUDIT_LOG` | S | Higher; mixes feedback with audit | Not recommended |

---

## 5. Explicitly not in Phase 10 v1 (with revisit triggers)

| Deferred | Why | Revisit when |
|---|---|---|
| Agent framework (LangGraph / Pydantic AI, both MIT) | Adds a permanent state/tool-contract shape for a one-tool loop; research recommends `TAHAN` | Multi-agent or durable-resumable graphs are a real requirement |
| Sidecar `nova-agent` runtime | Delegate-first is harder across a process boundary; extra ops | Measured agent load degrades the API request path |
| `Bypass approvals` global mode | Widens to DDL/admin; highest-risk option in every workstream | Never without a warning dialog + destructive separation, and an explicit user decision |
| Persistent "always allow" across devices | Durable trust record needs revocation + audit | E2c is chosen |
| Browser / file / clipboard tools | Arbitrary execution surface; out of scope for a SQL assistant | A concrete, permissioned use case exists |
| `CREATE TABLE`-by-prompt, dbt, notebook | Full-parity items; each is its own phase | E3c is chosen |
| `StarRocks/mcp-server-starrocks` as the tool path | Bypasses Nova's guard/audit/redaction (research §9) | Never as the execution path; only as a subject of study |
| Query timeout / cancellation in shared code | Pipeline has no statement timeout and no cancel handle (`query-tool §8`) | A conversation tool genuinely needs it — then it is its own scoped change to `QueryService` |
| `react-markdown` (MIT, named not adopted) | Zero-dependency plain text suffices for v1 | Rich rendering is a stated requirement |

---

## 6. Decisions required from the human owner

Answer these before Stage B–E are dispatched. Stage A (spec) needs only E1–E3.

1. **E3 (blocking everything):** re-scope roadmap row #20 from `NOT_APPLICABLE` to a
   new, bounded **Phase 10**, or keep it `NOT_APPLICABLE` and decline the feature?
   *This is the only decision that changes the roadmap rather than a task.*
2. **E1:** may the assistant execute read-only SQL itself (E1a), or must it only
   hand SQL to the worksheet (E1b)?
3. **E2:** is `always allow` **per conversation, in-memory, read-only** (E2b)
   sufficient, or is persistence per user+workspace (E2c) required?
4. **E5:** must conversations survive a restart (E5b/E5c), or is in-memory (E5a) fine?
5. **E4:** reuse the existing AI provider config (E4a), or a separate identity (E4b)?
6. **E6:** store LLM traces at all, and if so, separate from `AUDIT_LOG` (E6b)?
7. **Scope confirmation:** is the v1 subset — SQL Q&A + dialect-aware authoring +
   one read-only, consented tool + skills — the correct target, or is a specific
   out-of-scope item from §5 actually required in v1?

---

## 7. Why nothing is dispatched from this document

The sub-issues in Stage B–E are **ready but not created**, for two reasons:

1. **E3 gates the roadmap, not just a task.** Creating implementation sub-issues
   before the human answers E3 would commit the project to reversing a standing
   decision — exactly what the Team Lead role forbids.
2. **E1/E2 change task shape.** A "no" on E1a deletes T-C2's approval card and
   changes T-D3; a "no" on persistence deletes T-B3's larger half.

Once §6 is answered, the dispatch order is: **T-A0 first** (it freezes the answers
into a spec), then Stage B and Stage D can run in parallel (D against a stub
stream), then Stage C (after E1/E2), then Stage E when NOVA-59 lands.

---

## 8. Still unknown / not verified

Carried forward from the three research docs, deduplicated. None is load-bearing.

- CoCo's acronym expansion, product GA date, Desktop release notes (404), result-set
  render location, Snowsight session-persistence mechanism, internal tool schema.
- Whether "Always Allow" in Snowsight is per browser or per device/account.
- A CoCo-equivalent agentic surface in BigQuery was not found.
- Exact panel widths/resize behaviour in CoCo/Copilot are undocumented; Nova's
  `w-[22rem]` is a proposal, not a copied number.
- Whether Nova already exposes a streaming endpoint — **not verified by the UI
  workstream**; the safe assumption is "no", so streaming is backend work in T-B2.
- `Ctrl/Cmd + J` is not verified conflict-free against all Monaco bindings.
- The assistant button icon is blocked by design-system rule B1 (`DESIGN.md:272-277`)
  — a pending design decision, not a free choice (`uiux §8.6`).
- No statement timeout and no cancel handle exist in the pipeline today.
- `get_provider_api_key` returns plaintext to any in-process caller
  (`ai_ml/service.py:94`); the isolation boundary is procedural, not structural (E4).
- Real agent-loop load on the FastAPI connection pool is unmeasured — it is what
  would justify the sidecar.
- `docs/sql_docs/` (NOVA-59) has not landed, so the skill design is a plan.

---

## 9. Provenance

| Input | Revision / path |
|---|---|
| Cocoo research | `docs/research/agentic-assistant-snowflake-coco.md` @ PR #71 (`dae2437`) |
| Architecture options | `docs/research/agentic-assistant-nova-architecture.md` @ PR #71 (`dae2437`) |
| UI/UX sidebar | `docs/research/agentic-assistant-uiux-sidebar.md` @ PR #70 (`3e4f239`) |
| Query tool + permissions | `docs/research/agentic-assistant-query-tool-permissions.md` @ PR #69 (`98a5da7`) |
| Roadmap phase plan | `docs/roadmap-snowflake-parity.md` (`#20` at `:147`, `:330`) |
| Canonical checklist | `README.md:540-726` (Roadmap), `:728-802` (Decision Log) |
| Repo read for re-checks | `0c55f14` (main) |

All research source URLs and their access dates are in the individual research
documents; they are not duplicated here.
