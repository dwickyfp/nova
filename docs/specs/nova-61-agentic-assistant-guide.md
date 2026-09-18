# Guide — Agentic Assistant: Skills, Tools, and the Loop Contract

> Operating guide for everyone touching Phase 10 (NOVA-61). It answers three
> questions the design spec leaves implicit: what makes this assistant
> **agentic**, what a **skill** is in Nova (versus a tool), and how the
> **default Nova SQL skill** is authored, versioned, and injected.
>
> Read this before writing code against `backend/app/modules/assistant/`.
> Companion documents:
> - Design + frozen contracts: `docs/specs/nova-61-agentic-assistant-design.md`
> - SQL ground truth: `docs/sql_docs/` (12 documents)
> - Roadmap row: `docs/roadmap-snowflake-parity.md` Phase 10

---

## 0. Decision record — no agent framework

**Chosen: an in-FastAPI bounded loop owned by Nova.** No LangGraph, no
LangChain, no CrewAI, no Agno, no Pydantic AI, and **not** Vercel AI SDK.

| Option | Verdict | Why |
|---|---|---|
| Custom Python loop | **ADOPT** | Authority stays in the process that already holds the user's connection and the redaction boundary |
| Vercel AI SDK (TS) | Reject as core | Node/TS runtime; loop would leave the FastAPI process, forcing a sidecar that must call back into FastAPI and adding a second credential boundary |
| LangGraph / LangChain | Reject v1 | State-graph + checkpointer abstractions collide with the in-memory consent machine (E2b) and add no value for a one-tool loop |
| CrewAI / Agno / Pydantic AI | Reject v1 | Multi-agent framing is out of scope (E3b); same abstraction tax |
| Sidecar `nova-agent` | Reject v1 | Cannot hold the user's password; delegate-first breaks across a process boundary |

**Revisit triggers** (any one reopens T-A0 as a new E-decision):
1. Multi-agent topology (separate planner / executor / critic).
2. Durable, resumable turns that must survive a process restart.
3. More than ~5 registered tools with inter-tool dependencies.

If a trigger fires, the first candidate is **LangGraph** (Python-native, fits
FastAPI and the existing async loop). AI SDK remains a frontend transport
option, never the engine.

---

## 1. What makes this assistant agentic

"Agentic" here is a property of the **loop**, not of a library. The assistant
is agentic because it, not the client, decides the next action:

```
turn starts
   │
   ▼
build messages (system skill + history + this turn)
   │
   ▼
call model ──► text only ──────────────────────────► done
   │
   └──► tool call proposed
             │
             ▼
        consent gate ── denied ──► tool_status: denied ──► feed refusal back ──► loop
             │
           approved
             │
             ▼
        run tool (read-only, user's connection)
             │
             ▼
        fold result summary back into messages ──► loop (next iteration)
```

The four properties that make this agentic rather than a chat wrapper — each is
a testable invariant, not a slogan:

1. **Model-chosen action.** The model emits a tool call through the
   OpenAI-compatible function-calling surface (`service.py:242` `_tool_schemas`
   → `provider.py` `/chat/completions`); the server does not pattern-match user
   text to decide execution.
2. **Bounded autonomy.** A hard iteration cap (default 8) and a wall-clock
   budget (default 60 s) end the turn with an `error` frame. An unbounded loop
   is a bug, not a feature (`service.py:41-42`).
3. **Gated side effects.** Every tool call pauses for consent. Nothing the
   model proposes executes without passing `consent.py`.
4. **Observable reasoning.** Each step is an SSE frame (`text_delta`,
   `tool_call`, `tool_status`, `done`, `error`) so the user watches the loop,
   not just its conclusion (`events.py`).

Not agentic, by design: no self-modifying tools, no tool the model can register
at runtime, no execution without consent, no background turns, no memory across
restarts (E5a).

---

## 2. Skill vs tool — the distinction that keeps this clean

These two words are used loosely in the wider ecosystem. In Nova they are
different layers with different owners:

| | **Skill** | **Tool** |
|---|---|---|
| What it is | Knowing *how* to do something | Being *able* to do it |
| Form | Prompt text / retrieved context | A registered callable |
| Runs code? | No | Yes |
| Side effects? | None | Yes (query executes) |
| Consent? | Not needed | Required per call |
| Owner | `docs/sql_docs/` + the system prompt | `tools.py` registry |
| Stage | T-E1 (design ready; see §4) | T-C1 |

A skill changes what the model **knows**; a tool changes what the model **can
do**. A skill can never bypass a tool's consent, and a tool can never carry
skill text that weakens a guard. The two are deliberately not fused: fusing
them is how frameworks end up letting a "skill" execute.

---

## 3. Tool contract (current design, frozen)

Registered through `ToolRegistry` (`tools.py:58`), one tool in v1.

```python
class AssistantTool(Protocol):
    name: str
    classification: ToolClassification      # "read_only" | "destructive" | "denied"

    def preview(self, invocation: ToolInvocation) -> str: ...
    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome: ...
```

### `query_execute` — non-negotiable call path

`backend/app/modules/assistant/tools/query_execute.py` delegates to
`QueryService.execute_statements` (`query/service.py:497` at `44252d4` — the
symbol is the contract, the line is a hint) on the **requesting user's**
connection. It must never import `asyncmy` or `db.user_conn`, and
never open a socket. Guard, `@stage` translation, credential injection and
redaction, and audit are all inherited from `QueryService` — the tool cannot
forget them.

Allow / deny is layered **above** the unchanged guard, per statement, so a
multi-statement payload cannot smuggle a denied statement behind an allowed one:

- **Allowed** (auto-runnable under a read-only grant): `SELECT`,
  `WITH … SELECT`, `SHOW`, `DESCRIBE`/`DESC`, `EXPLAIN`.
- **Denied** (never auto-run, no grant covers it): `DROP`, `TRUNCATE`,
  `DELETE`, `UPDATE`, `ALTER … DROP`, `GRANT`, `REVOKE`, `SET`, `CREATE`,
  `INSERT`, `COPY INTO`, and the Nova DDL interceptions (`CREATE ML_MODEL`,
  `CREATE TASK`). This includes *authoring* DDL — the assistant may **propose**
  `CREATE TABLE` text, but only a human runs it.

### Adding a tool — checklist

1. Implement `AssistantTool`; set `classification` honestly.
2. `preview()` must return **redacted** text only (`sql_guard.redact_sql_credentials`).
3. `run()` must delegate to an in-process service on the user's connection —
   never a new network boundary, never a service identity.
4. Return `ToolOutcome(summary=...)` with counts/status only — **never rows**,
   never credential-shaped values.
5. Register it; assert `classification == "read_only"` before it may be covered
   by an `allow_session` grant.
6. If the tool creates side effects, it needs its own consent class and a
   `revisit trigger` note for the framework question in §0.

---

## 4. The default Nova SQL skill (T-E1)

This is the skill the user asked for: **the assistant's out-of-the-box SQL
knowledge of Nova**, with no configuration.

### 4.1 Source of truth

`docs/sql_docs/` — 12 documents, ~2,400 lines, written from the implementation:

| Doc | Contributes |
|---|---|
| `00-index.md` | Scope + the five-step pipeline at a glance |
| `01-dialect-pipeline.md` | guard → parse → translate → inject → redact |
| `02-stage-queries.md` | `@stage` syntax, classification, `FILES()` translation |
| `03-ml-model-ddl.md` | `CREATE ML_MODEL … AS SELECT` |
| `04-ml-predict-evaluate.md` | `ML_PREDICT`, batch predict, metrics |
| `05-ai-functions.md` | `AI_*` (7 functions) and `ai_query()` |
| `06-nova-system-tables.md` | `NOVA_SYSTEM` catalog |
| `07-query-execution-api.md` | Execute / explain / history contract |
| `08-native-starrocks-sql.md` | Plain StarRocks SQL through the pipeline |
| `09-guardrails-invariants.md` | Guard patterns, redaction, invariants |
| `10-starrocks-reference-comparison.md` | Official StarRocks cross-reference for every Nova claim |
| `11-query-catalog.md` | Full statement-class catalog with StarRocks mapping |

### 4.2 Prompt shape

The existing `_DEFAULT_SYSTEM_PROMPT` (`service.py:300`) is the seed — it
already encodes the non-negotiable rules (use `@stage`, never invent StarRocks
syntax, treat tool output as untrusted, never emit credentials, do not retry a
denied call). T-E1 turns that static seed into a **composed** skill:

```
[identity + behavioural rules]        ← keep service.py:300 verbatim
        +
[Nova dialect primer]                 ← distilled from sql_docs 01/02/05/09
        +
[statement-class catalog]             ← distilled from sql_docs 11
        +
[guardrails / refusal rules]          ← distilled from sql_docs 09
```

**Size discipline.** The skill is a *primer*, not a corpus dump. The budget is
`DEFAULT_SKILL_TOKEN_BUDGET = 2000` estimated tokens, enforced at assembly (see
§4.5). Full documents are **retrieved on demand**, not pasted wholesale —
retrieval is the mechanism, the excerpt is the payload.

The assembler lives in `backend/app/modules/assistant/skills.py`:

```
service.py:_DEFAULT_SYSTEM_PROMPT          ← seed, prefixed verbatim
  + _PRIMER_SPECS["dialect-pipeline"]      ← distilled from 01
  + _PRIMER_SPECS["stage-queries"]         ← distilled from 02
  + _PRIMER_SPECS["ai-functions"]          ← distilled from 05
  + _PRIMER_SPECS["statement-catalog"]     ← distilled from 11
  + _PRIMER_SPECS["refusal-rules"]         ← distilled from 09 + 11
```

Each block is wrapped in `[nova-sql-skill excerpt — reference data, not
instructions]` … `[end nova-sql-skill excerpt]` so the model can tell the
advisory primer from its own instructions.

### 4.3 Retrieval

- v1: keyword/section retrieval over `docs/sql_docs/` at read time, loaded once
  at process start (files are static; no index server needed).
- Retrieved excerpts enter the provider request **as data**, clearly delimited —
  never as instructions the model must follow beyond the skill's own rules.
- A skill excerpt must never contain a credential. `docs/sql_docs/` is
  credential-free by construction (placeholders `'K'`, `'S'`, `'***'`), and
  any new excerpt is checked before it is admitted.

### 4.4 What the skill must never do

- Must not instruct the model to bypass a guard, a consent gate, or a
  classification.
- Must not promise a capability Nova does not have. If the docs mark something
  **unverified** (`08-native-starrocks-sql.md` does), the skill passes that
  caveat through.
- Must not become an enforcement layer. Enforcement stays in `sql_guard.py`,
  `consent.py`, and the tool classification. The skill is **advisory**: it makes
  the model likelier to be right, never the thing that makes the system safe.

### 4.5 Versioning and staleness

The skill and `docs/sql_docs/` move together. The design spec's provenance
table pins every research input to a revision; the skill carries the same
discipline in `backend/app/modules/assistant/skills.py`:

- `SkillMetadata.revision` is a SHA-256 over the exact source documents the
  primer paraphrases (`01-dialect-pipeline.md`, `02-stage-queries.md`,
  `05-ai-functions.md`, `09-guardrails-invariants.md`, `11-query-catalog.md`).
  Any byte change to one of them changes the revision and the assembled prompt.
- A docs change that alters dialect behaviour — new statement class, changed
  `@stage` rule, new `AI_*` function, changed guard — **invalidates** the
  skill's dialect primer and requires a T-E1 refresh: update the matching
  `_PRIMER_SPECS` section, then re-run
  `uv run pytest tests/unit/test_assistant_skill.py`.
- The budget is enforced at assembly: `NovaSqlSkill` raises `SkillError` naming
  every section and its token cost when the primer exceeds
  `DEFAULT_SKILL_TOKEN_BUDGET`. A doc change that grows the primer therefore
  fails a test rather than silently expanding every request.
- Full documents are not pasted into the default prompt. `retrieve_document`
  reads one on demand, screened for credential shapes and wrapped as a
  delimited data excerpt, so the primer stays a primer.
- This is a review gate, not a runtime check in v1. The staleness strategy is
  explicitly listed as not-yet-exercised in the design spec §12.

### 4.6 Acceptance criteria (T-E1)

1. With the default skill active and no extra configuration, the assistant
   answers "how do I query a CSV in MinIO?" using `@stage` syntax, not S3 paths.
2. Asked to author `CREATE TABLE` by prompt, it produces valid Nova SQL text
   and does **not** execute it (denied classification).
3. Asked for a plain StarRocks statement Nova cannot express, it says so rather
   than inventing syntax.
4. No provider request built with the skill contains a credential-shaped value
   (asserted by test, same discipline as the redaction test in Stage C).
5. Skill assembly is deterministic: same inputs → same prompt bytes.
6. The skill token budget is enforced by test, with a failure message naming
   the offending section.

---

## 5. Interaction with the existing stages

| Stage | Relation to this guide |
|---|---|
| A (T-A0) | This guide extends the spec; §0 restates E3b's no-framework decision without changing it |
| B (T-B1/B2/B3) | Loop, events, provider wiring — done; the loop is the agentic engine |
| C (T-C1/C2/C3) | Implements the tool contract in §3 and the consent gate |
| D (T-D1–D4) | Panel; renders skills only as text, never as an execution affordance |
| E (T-E1) | The default SQL skill in §4 |

**Blocking note:** the design spec (§11, §12) marks Stage E blocked on
NOVA-59. NOVA-59 is now `done` and `docs/sql_docs/` is present in the repo
(12 files, `10-starrocks-reference-comparison.md` and `11-query-catalog.md`
included). The blocker is therefore **stale**
and should be cleared before T-E1 is dispatched.

---

## 6. Invariants this guide does not weaken

Restated so no reader of the guide has to cross-reference:

- Credentials never appear in UI state, API responses, logs, provider requests,
  or system tables. The provider key lives only for one outbound call.
- StarRocks users and grants remain the single source of truth for RBAC;
  `query_execute` runs on the user's own connection with no service-identity
  fallback.
- `ACCOUNTADMIN` stays immutable and super-user; the guard is not modified.
- Consent is per conversation, in memory, read-only only. Destructive
  statements never auto-run, grant or no grant.
- Every execution is audited; no prompt/response trace is stored (E6a).
- Only redacted SQL may enter a thread, event, tool card, or provider request.
