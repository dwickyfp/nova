# Neva: the console assistant, redesigned

Status: proposal, 2026-11-09, branch `feat/dataviz`. Research and sources:
[neva-assistant-harness-research.md](../research/neva-assistant-harness-research.md).
Current architecture: [arch-09](../arch-09-nove-application-copilot.md).

## Goal

Rename the console assistant Nove to **Neva** and make it the assistant that
helps people understand and operate Nova end to end: it explains, finds, fixes,
builds with consent, and answers data questions from governed meaning. It keeps
every capability Nove has today.

Success means, measured against today's Nove on the same task set:

- Every current tool, skill, surface and UI affordance still works (parity suite
  green, section 6).
- Task success on a golden set of real console tasks rises, with pass^3
  reported, and no task class gets worse.
- Answers carry where they came from (semantic view, ontology, verified query,
  ad-hoc SQL, docs), and wrong-but-confident answers fall.
- Latency and tokens stay within an agreed budget for simple questions.

## Non-goals

- No second inference loop, planner, parser or memory store. Neva composes the
  shared `AssistantLoop` (AGENTS.md, `assistant/AGENTS.md`).
- No multi-agent delegation (Smart/Auto stays removed).
- No new enable/disable flag. Neva replaces Nove as normal product behaviour.
- Neva never reviews, applies or publishes ontology changes, never acts with a
  credential other than the caller's, never bypasses consent.

## Design

### 1. One engine, configured as a system agent

Studio already builds a capable loop from an agent config
(`agents/router.py:2130-2510`), and Dataviz proves a never-persisted system
agent works (`dataviz_assistant_config()`). Neva follows that path:

- **Extract the loop builder** from the agents router into one function that
  turns a config into registry, prompt, memory and `LoopContext`, and builds the
  `AssistantLoop`. Studio, Dataviz and Neva all call it. This removes code from
  the router rather than adding a parallel path.
- **`neva_system_config()`**: fixed tools (all of Nove's plus the new ones
  below), console skills (the `audience` filter inverted: console-only skills
  in, Studio-only out), standing rules, budget, `intelligent_ui=True`. Never
  persisted, like Dataviz.
- **Budget that scales with the question.** The planner already classifies
  intent. Simple lookups and navigation keep a fast profile (about 8 steps);
  investigation, multi-step builds and data analysis use the analyst profile
  (16 steps, iterative re-planning, summarization). Effort rules go in the
  prompt (simple questions: 3–10 tool calls). The step and time limits are
  measured in phase 2 before being fixed.
- **Re-plan instead of fail.** Use the loop's iterative mode and Studio's
  fallback plan, so a failed plan no longer ends a Neva turn.
- **Guards from policy.** `GuardChain.from_policy` with Neva's standing rules,
  on top of the platform injection scan and credential redaction.
- **Attachments reach the loop** (the schema already accepts them).

### 2. Grounded meaning first

Data and metric questions route through governed meaning before raw SQL:

1. Ontology resolves the business term (asks when ambiguous, offering the
   choices the resolver returns).
2. A usable binding leads to the semantic view (`semantic_query`), run as the
   caller.
3. Verified queries where they exist.
4. Ad-hoc SQL only as a fallback, labelled as such.

**New ontology tools**, all read-only unless marked, each backed by the existing
service as the caller, `scope="current"` hard-coded, release pinned for the
turn:

| Tool | Backed by |
|---|---|
| `ontology_resolve` (reuse) | `concept_resolver.resolve(..., scope_views)` |
| `ontology_explain` (reuse) | `find` + `get_concept` (history trimmed) + `representations` + `related` |
| `ontology_search` | `list_concepts(scope="current", limit<=50)` |
| `ontology_trace_metric` | usable `representations` → semantic view metadata the caller can see |
| `ontology_related` | `related(depth<=2)` |
| `ontology_release_info` | non-admin release summary |
| `ontology_my_proposals` | `stewardship_service.list(user, status)` |
| `knowledge_propose` (reuse, write, consent every time) | `stewardship_service.create(source="AGENT")` |
| `ontology_withdraw_proposal` (write, consent) | `stewardship_service.withdraw` (submitter only) |

Neva states "proposed", never "changed", for a proposal.

**Ontology fixes found during research** (security-relevant, Ranger acceptance
applies): make `scope=working` steward-only as documented; trim unreleased
revision history from `get_concept` for non-stewards; decide how
`classification` limits what reaches a model prompt.

### 3. Verify before claiming

- Drafted SQL is checked beyond grammar: resolve objects and `EXPLAIN` as the
  caller before showing "this will work"; repair in the loop (Snowflake pattern).
- Created objects are read back after a write.
- Page actions are confirmed by the page's own events (existing mechanism).
- Every answer carries a source label in the answer contract: Semantic view,
  Ontology, Verified query, Ad-hoc SQL, Docs. Only governed sources get the
  "Trusted" badge (Databricks pattern).

### 4. Context

- Preload: active role and security-context summary, current surface, selection
  and editor state, a short capability map of Nova. Fetch the rest just in time
  (docs, catalog, ontology, semantic views).
- Extend surfaces from 5 to the main console pages: Database Explorer,
  Workspaces (existing), Tasks, Monitoring Queries (existing) and other
  Monitoring pages, Access Control and Roles (existing), Semantic Views
  (existing), Business Ontology, Agents, Users. Each registers its context and
  safe actions through the existing `useNoveSurface` hook (renamed).
- Memory across sessions: reuse Studio's memory module, scoped to principal,
  role and security-context version. Open decision (section 8).
- Persist the "always allow read-only" grant per thread in Redis, not process
  memory.

### 5. UI: Nova Intelligence UI in the panel

- Render the same blocks as Studio: interactive `ui` programs, tables, charts,
  citations, evidence health, suggestions (follow-up chips) and the
  `ProcessRail` activity trace. Reuse `TurnContent`, `IntelligentUi`,
  `ResultTable`, `ResultChart`, `CitationList`, `AnswerFooter` by extracting
  their Dataviz and agent-API coupling into optional props.
- UI actions get a thread-scoped endpoint for console threads; validation moves
  from the agents router into the shared module.
- Greeting card: extract `Greeting` and `greeting.ts` from Studio; Neva's empty
  state shows "Good {daypart}, {name}", page-aware suggestions (from the
  surface) and recent threads.
- Keep every Nove affordance: approval card with protected password field and
  file upload, code cards with Run, inline preview and "Fix with Neva", query
  attach with rewrite diff, model and approval-mode selectors, dock and resize.
- Thread history gains search, rename and delete (the API already supports the
  last two).
- New mark for Neva in the existing brand colours, applied through `/unslop`.

### 6. Rename

- User-visible strings, persona (both copies), planner text, tool descriptions,
  skills and knowledge docs, docs (`arch-09` renamed), identifiers
  (`useNoveSurface` → `useNevaSurface` and the rest).
- Lookup values renamed together with readers: `nove-workflows`,
  `nove-code-`/`nove-card-`, `nove-new-user-password`.
- Stored data is unaffected (`assistant_*` everywhere). Old thread transcripts
  keep the word Nove in text the model wrote; that is history, not rewritten.
- A test fails the build if "Nove" appears in user-visible frontend strings or
  prompts.

## 7. Plan

Every phase starts with its acceptance criteria and ends with a measured
checkpoint in `docs/testing/neva/`. Numbers are reported with n and a confidence
interval; judge verdicts are recorded; tuning uses a dev/test split and reports
only the test half.

| Phase | Work | Acceptance |
|---|---|---|
| 0. Baseline | Capability parity suite: one scripted eval trajectory per tool (21), skill (14) and surface (6), plus UI tests for every panel affordance. Golden set of 40 real console tasks across explain, navigate, fix SQL, build with consent, data questions, governance. Measure today's Nove on it live: success, pass^3, latency p50/p95, tokens | Suite covers the full inventory and is green on Nove; baseline recorded |
| 1. Rename | Section 6 | Parity suite green; no "Nove" in user-visible strings or prompts (test); stored data untouched |
| 2. Harness | Loop builder extraction, `neva_system_config`, budget scaling, re-planning, guards, attachments, persisted read-only grant | Parity green; new trajectories for re-planning and budget scaling; Studio and Dataviz evals unchanged; simple-question latency within an agreed bound of baseline |
| 3. UI | Intelligence UI blocks, ProcessRail, greeting, thread search/rename/delete, Neva mark | Frontend checks; every Nove affordance still present (tests); screenshots light, dark, phone judged |
| 4. Ontology | Tools in section 2, grounding order, ambiguity questions, the three ontology fixes | Trajectories: grounding order, consent on propose, never "changed", hidden-domain refusal; Ranger and patched-FE acceptance for the fixes |
| 5. Smarter | SQL verification (EXPLAIN as caller), source labels and Trusted badge, more surfaces, scoped memory if approved | Golden-set success up versus baseline with CI, no class regresses; wrong-confident answers counted down |
| 6. Acceptance | Live run on the golden set with LLM judge (accuracy, citations, completeness, tool efficiency), pass^3, cost and latency | Recorded checkpoint; decision to keep or tune each change |

Validation per change follows AGENTS.md: backend unit suite with coverage, agent
eval scorecard and affected `tests/eval/` trajectories, ruff and mypy; frontend
lint, build, coverage; seeded integration and Ranger acceptance where auth,
ontology access or SQL execution change.

## 8. Decisions for the user

1. **Budget**: let Neva use the analyst profile (16 steps, iterative) for
   complex questions, accepting slower and costlier answers there, or keep 8
   steps everywhere.
2. **Memory**: should Neva remember facts and preferences across sessions,
   scoped to user and role?
3. **Ontology writes**: may Neva file ontology proposals (consent every time),
   or stay read-only?
4. **Ontology fixes**: fix the three access gaps in this work, or separately.
5. **Golden set**: confirm the 40-task set mixes English and Indonesian as real
   users ask.
