# Neva build tracker

The single place that says which part of the Neva work is done. Design:
[proposal](../../proposals/neva-assistant.md). Research:
[harness research](../../research/neva-assistant-harness-research.md).

Status keys: `[ ]` not started, `[~]` in progress, `[x]` done and verified,
`[!]` blocked (reason written next to it). A box is ticked only after its check
ran on the final code and the evidence is linked.

Each phase ends with a checkpoint file in this folder
(`phase-N-checkpoint.md`) holding the measurements, n and confidence intervals,
judge verdicts and the commands that produced them.

## Standing rules (apply to every phase)

These come from the user and do not expire.

1. **No technical leaks.** Neva's answers, tool descriptions, tool results it
   repeats, errors and suggestions never contain route paths, the word
   "endpoint", "API", HTTP status codes, internal module, class or file names,
   `NOVA_SYSTEM` table names, stack traces or provider/storage addresses. Users
   see plain words ("the SQL editor", "your saved file", "Nova could not reach
   the AI service").
2. **Tools call services, not Nova's own HTTP routes.** Every tool runs
   in-process as the caller, through the owning service. (Audit 2026-11-09: no
   assistant or Studio tool calls Nova's HTTP routes today; keep it that way
   with a test.)
3. **Security first.** Caller credentials and the single active role only;
   consent enforced by the harness; fail closed; redacted audit for every
   governed action and refusal; no secret in context, answers, logs or traces.
4. **Lay-user testing.** Every live test is run from the point of view of a
   non-technical person who maintains Nova and wants help ("my report is slow",
   "why can't Ana see sales", "what does net revenue mean here"). Judged on:
   did it solve the problem, was it understandable, was it correct, did it leak
   anything technical, did it ask before changing things.
5. **Nothing is lost.** The parity suite (phase 0) stays green after every
   change.
6. **Measured, not claimed.** Quality claims need numbers with n and a
   confidence interval on the live provider; tuning uses a dev/test split and
   reports the test half only.
7. **UI work applies `/unslop`** and is judged in light, dark and phone width.

## Phase 0: baseline

Goal: know exactly what Nove does today and how well, before changing it.

- [x] 0.1 Technical-leak audit of prompts, skills, knowledge docs, tool
      descriptions, tool error texts and provider error texts; list every leak.
      Findings (fixed in phase 1):
      - Persona prompt tells the model about "HTTP/API routes" and names tools
        (`assistant/service.py` default system prompt).
      - Provider errors: "AI provider endpoint is not allowed", "AI provider
        returned HTTP …", "API key" (`assistant/provider.py:252,330,374`).
      - Web search errors name "Tavily API key" (`tools/web_search.py:37,240`).
      - Tool catalog text mentions "HTTP routes" (`agents/tool_catalog.py:301`).
      - Skills/knowledge use "endpoint" for the SQL editor and list internal doc
        paths: `copy-into.md:48`, `sql-extensions.md:26`,
        `accountadmin-guardrail.md:34`, `stages-tasks-access.md:24`,
        `data-movement-storage.md:11` (provider/storage property names in
        `ai-functions.md` and `stage-query.md` are real SQL property names and
        stay inside SQL examples).
      - Dynamic risk: raw engine/service error texts reach tool results, and the
        model may repeat tool names. Measured live in 0.6.
- [x] 0.2 Leak check as code: `assistant/plain_language.py`
      (`find_technical_leaks`), tested by `tests/unit/test_assistant_plain_language.py`
      (19 cases). Used by the live runner now and by Neva's answer guard in
      phase 1.
- [x] 0.3 "No HTTP self-calls": `tests/unit/test_assistant_tools_call_services.py`.
      HTTP clients only in provider, measurements, ui_media, mcp_client; no Nova
      route in assistant or agent code (the Dataviz assistant's Superset route is
      the one named exception).
- [x] 0.4 Parity suite: `tests/unit/test_neva_capability_parity.py` (52 checks:
      21 tools with read/write class and approval rule, every write needs
      approval, 14 skills load, 16 knowledge documents present and found by a
      question). Behaviour stays covered by the existing `tests/eval/`
      trajectories (Nove scenarios, consent, surfaces) and the frontend panel
      tests (`assistant-panel`, `tool-call-card`, `code-card`, `query-attach`,
      `model-selector`, `thread-history`, `assistant-dock`, `panel-resize-handle`),
      which must stay green. Finding: plain questions like "how do I create a
      table" do not reach the table guide (phase 5.6).
- [x] 0.5 Lay-user golden set: `golden-set.json`, 40 tasks (explain 8, find 6,
      fix SQL 6, data 8, access 6, maintain 6), English and Indonesian, expected
      outcome and grading rubric each; stratified dev/test split 20/20 fixed
      before tuning. Runner: `backend/scripts/neva_golden_run.py`.
- [x] 0.6 Live Nove baseline: 120 turns judged. Solved 0.21 (0.15–0.28),
      understandable 0.41, correct 0.50, pass^3 0.075, 37% empty answers, p50
      20 s. Details and targets: `phase-0-checkpoint.md`.

Acceptance: 0.2–0.4 green on today's code; baseline recorded with n and CIs.

## Phase 1: rename to Neva

- [x] 1.1 Persona (both copies), planner text, tool descriptions, skills,
      knowledge docs renamed; technical wording from 0.1 rewritten in plain words;
      "Implementation references" code paths removed from guides; persona states
      the plain-language rule (within the 3,000-token prompt budget).
- [x] 1.2 Frontend strings and identifiers (`useNoveSurface` → `useNevaSurface`,
      `askNove`, `NoveMark`, files) renamed.
- [x] 1.3 Lookup values renamed with their readers: `nove-workflows`,
      `nove-code-`/`nove-card-`, `nove-new-user-password`.
- [x] 1.4 Docs: `arch-09` renamed to `arch-09-neva-application-assistant.md`;
      docs mentioning the assistant updated.
- [x] 1.5 Tests: `test_neva_plain_prompts.py` (persona, knowledge, skill
      bodies and tool descriptions carry no "Nove" and no internals).
- [x] 1.6 Plain-language filter on every Neva and Studio stream
      (`plain_frames`): answer text, errors, steps, tool summaries and
      follow-ups; code blocks untouched; streaming not delayed. Provider and web
      search errors rewritten for a lay reader.

Acceptance: parity suite and leak check green; stored data untouched
(`assistant_*` names unchanged).

## Phase 2: harness

- [!] 2.1 Extract the loop builder from the agents router. Not needed for
      what Neva uses today: the console router builds the shared
      `AssistantLoop` with Studio's budget profile and options directly
      (`assistant/router.py` `_neva_loop`). Revisit only if a third caller
      appears.
- [x] 2.2 Neva's own tool set: `build_neva_registry()` adds the governed
      `semantic_query` and `list_semantic_views` to the console tools;
      readable semantic views are resolved per turn as the caller. Guards: the
      loop runs the system guard chain (answer screening for credentials and
      injected instructions); owner-written rules are a Studio agent concept,
      and Neva's standing rules are its persona and the plain-language rule.
- [x] 2.3 Budget scales automatically: the loop starts with the analyst
      ceiling (16 steps, 180 s, iterative re-planning, summarization, spend
      limit); once the plan shows a quick turn (explain, help, catalog, a
      question back) it lowers to Studio's fast limits (8 steps, 60 s). It can
      only lower limits. Trajectories in `test_neva_lookups_and_views.py`.
- [x] 2.4 A bad plan no longer ends the turn: a planning error falls back to
      a read-only plan (look up guides, catalog, governed metrics, read-only
      SQL) with nothing required. When a required check still did not run,
      the governed metric tool is tried, and otherwise Neva says in plain words
      that it will not guess (`loop.incomplete`, English and Indonesian) instead
      of an empty answer. Trajectories: `tests/eval/test_studio_planner_fallback.py`,
      `test_turn_planning.py`, `test_semantic_hardening.py`, `scenarios.py`.
- [x] 2.5 File attachments reach the loop: up to 3 files (text, PDF, image)
      from the panel composer, validated like Studio, given to the model as
      data (never instructions), kept with the message and audited
      (`ASSISTANT_ATTACHMENT`). Tests: `test_neva_attachments.py`, composer and
      stream-client tests.
- [x] 2.6 "Always allow read-only" kept per user and thread in Redis
      (`read_grants.py`), stamped with the active role and session version;
      a role switch (in the menu or by `USE ROLE`) or a new session drops it,
      in memory and in Redis; without Redis nothing is restored (fail closed).
      Also closes a gap: before, an in-memory grant outlived a role switch made
      in the menu between turns. Tests: `test_neva_read_grants.py`.
- [x] 2.7 Cross-session memory scoped to user and role (decision 2026-11-09):
      Neva keeps durable facts the person stated in its own words in Studio's
      memory store under the system id `neva-assistant`; questions and
      credential-shaped text are refused before any model call; writes and
      removals are audited; the person can list and remove them. A different
      role starts without them. Tests: `test_neva_memory.py`.
- [x] 2.8 Trajectories: fallback plan and skipped checks, budget choice,
      view choice, lookup correction, role switch; memory and grant isolation
      across roles in unit tests; the existing consent trajectories unchanged.
- [x] 2.9 Role-based by default: Neva runs as the active role the user set in
      Nova; when access is denied it offers an approved `USE ROLE` to another
      assigned role, then continues under it (views re-listed for the new
      role, old results hidden, account menu updated). Trajectory:
      `tests/eval/test_assistant_role_switch.py`. Design: `arch-09`, Active role.

- [x] 2.10 Measured (`phase-2-checkpoint.md`): solved 0.21 → 0.42, understandable
      0.41 → 0.93, correct 0.50 → 0.70, empty answers 37% → 6%, leaks 3 → 0.
      Blocked on: wrong-and-confident 3 → 19 (wrong governed view).
- [x] 2.11 Fixes from the measurement (re-measured: `phase-2b-checkpoint.md`, solved 0.67, correct 0.95, wrong-and-confident 4): Neva names a governed view from the
      readable list and never falls back to the first one (asks the model to
      choose, `VIEW_CHOICE_REQUIRED`); a failed lookup that is not an access
      refusal gets one correction, then a plain "couldn't look this up" message
      instead of the engine's text; a missing capability is stated in plain
      words. Trajectories: `tests/eval/test_neva_lookups_and_views.py`.
      Also from the measurement: Neva runs the read-only checks the model
      skipped (guide lookup, the editor's failed query, a SQL check on the SQL
      its draft proposes; a draft with no SQL has nothing to check) instead of
      ending with "I won't guess"; the catalog, guides and the new
      `list_semantic_views` (views the active role can open) are always in
      reach. Knowledge: `nova-navigation.md` (every menu and tab, kept in step
      with the sidebar by a test), stale monitoring menus fixed, and every page
      address in the guides replaced by its menu name; the leak finder now
      flags page addresses and turns them into menu names.
      Re-measure: `runs/phase-2b-neva.jsonl`.

Acceptance: parity, leak and Studio/Dataviz evals green; simple-question
latency within the bound agreed from the phase 0 baseline.

## Phase 3: Intelligence UI in the panel

- [x] 3.1 Shared answer blocks: the UI-block checks (source in the caller's
      security context, state validation, public media, one-time actions,
      message visibility) moved out of the agents router into
      `assistant/ui_access.py`; Studio and Neva both call it after proving the
      thread is the caller's. `IntelligentUi` takes a source without an agent.
- [x] 3.2 Neva threads get the same UI routes (`/threads/{id}/messages/{id}/
      ui-state/{id}`, media, map metric) and accept a button or form action
      once, as user input; the reply is stored under the id sent in `done`,
      and `done` is sent only after it is stored.
- [x] 3.3 The panel renders interactive blocks next to tables, charts and
      citations (text keeps the panel's renderer: Run and Fix with Neva stay),
      shows Studio's process rail (open while working, then one summary line
      with steps and time), and restores every block when a conversation is
      reopened. Neva turns run with Intelligence UI on.
- [x] 3.4 Greeting card shared with Studio: daypart greeting, page-aware
      suggestions, recent chats (phase 1); conversations are now named from
      their first question.
- [x] 3.5 History: search by title, rename in place, delete with
      confirmation (an open conversation that is deleted starts a new one).
- [x] 3.6 Neva mark kept; `/unslop` pass: no tool names in previews, composer
      selectors become icons in a narrow panel instead of cut labels; the
      greeting keeps Studio's sky ink by request. Checked in light, dark and
      phone width in the browser.
- [x] 3.7 Every Nove affordance still present: panel, composer, tool-call,
      code-card, query-attach, model and approval selectors, history, dock and
      resize tests green.

Also measured: the turn used to wait about 17 s before its first step while
readable Semantic Views were checked twice, one by one; they are now checked
once per turn, four at a time, as the caller (about 3 s). Nothing is kept for
another request.

## Phase 4: ontology

- [x] 4.1 Fix: `scope=working` readable by stewards only (others: empty, 404 by id).
- [x] 4.2 Fix: unreleased revision history hidden from non-stewards, without authors.
- [x] 4.3 Fix: concept `classification` limits what reaches a model prompt.
- [x] 4.4 Acceptance on the dev engine's real ontology (5 roles, read-only) and
      `test_business_ontology_metadata.py` on the engine (6 passed, 1 skipped by
      design). These checks are Nova metadata gated by the active role; the
      Ranger evidence suite creates Ranger users and was not run without approval.
- [x] 4.5 Tools: resolve, explain, search, trace metric, related, release info,
      my proposals (read); propose, withdraw (write, consent every time).
- [x] 4.6 Grounding order: ontology → semantic view → verified query → ad-hoc
      SQL; ask when a term is ambiguous.
- [x] 4.7 Trajectories: grounding order, consent on propose, "proposed" never
      "changed", hidden domain refused like a missing one.

## Phase 5: smarter answers

- [x] 5.1 Drafted SQL verified as the caller (objects resolve, EXPLAIN passes)
      and repaired in the loop before it is offered.
- [x] 5.2 Written objects read back after a change.
- [x] 5.3 Source label in every answer; "Trusted" only for governed sources.
- [x] 5.4 Surfaces added: Users, Data Access, Agents, Database Explorer
      (selected object), Tasks and Business Ontology (open tab), next to
      Workspace, Roles and Query history; filter choices and page counts reach
      Neva.
- [x] 5.5 Ambiguous or unanswerable questions get a clarifying question or
      answerable alternatives instead of a guess.
- [~] 5.6 Knowledge search finds guides from plain questions ("how do I create
      a table" → the table guide), measured on a question set.

## Phase 6: acceptance

- [x] 6.1 Live run of the golden set (test half) on the configured provider.
- [x] 6.2 Lay-user judge rubric: solved, understandable, correct, cited, no
      technical leak, asked before changing; pass^3.
- [x] 6.3 Comparison with the phase 0 baseline with CIs; no task class worse.
- [x] 6.4 Security review of the full diff; Ranger acceptance; leak check green.
- [x] 6.5 `phase-6-checkpoint.md` with the decision per change.

## Log

| Date | Phase | What changed | Evidence |
|---|---|---|---|
| 2026-11-09 | – | Research, proposal and decisions recorded | `docs/research/neva-assistant-harness-research.md`, `docs/proposals/neva-assistant.md` |
| 2026-11-09 | 0.1 | Audit started: no tool calls Nova's HTTP routes; "endpoint" wording found in 7 skill/knowledge docs and provider error texts | this file |
| 2026-11-09 | 0.1–0.5 | Leak audit, leak finder, services-not-routes test, parity suite (52), golden set 40 with split | tests listed in phase 0 |
| 2026-11-09 | 0.6 | Nove baseline measured and judged | `phase-0-checkpoint.md` |
| 2026-11-09 | 1 | Neva rename and plain language (backend); passed access checks no longer reused across requests (a revocation made directly in Ranger denies at once) | unit 8,522 pass, eval scorecard 51/51, mypy 0, ruff clean; 4 `tests/eval` trajectories fail before and after (pre-existing) |
| 2026-11-09 | 2.2–2.4 | Analyst budget, governed metrics, read-only fallback plan, plain "won't guess" message; four stale trajectories updated to current behaviour (`sql` key in last result, `load_skill` always registered, chart intent mocked) | see phase 2 entries |
| 2026-11-09 | 2.9 | Neva follows the UI's active role and switches roles only with approval; views re-listed after a switch | `test_assistant_role_switch.py` |
| 2026-11-09 | 2.10 | Live run judged: better on every category; wrong governed view found | `phase-2-checkpoint.md` |
| 2026-11-09 | 2.11 | View choice, lookup correction, plain failure messages | `test_neva_lookups_and_views.py` |
| 2026-11-09 | 2b | Re-measured after the fixes: solved 0.21 → 0.67, correct 0.50 → 0.95, empty 0, leaks 0, wrong-and-confident 4 | `phase-2b-checkpoint.md` |
| 2026-11-09 | 2.3, 2.5–2.8 | Automatic budget, attachments, role-stamped grant in Redis, per-role memory, readable fallback text | tests listed in phase 2 |
| 2026-11-09 | 3 | Intelligence UI in the panel: shared UI checks, Neva UI routes and actions, blocks and rail in the panel, history search/rename/delete, titles; first step 17 s → 3 s | tests in `test_neva_ui_routes.py`, panel tests |
| 2026-11-10 | 5.4 (Workspace) | Page work in SQL Workspace: Neva writes, edits and optimizes the open SQL and puts it in the editor as a reviewed diff, then explains the change. Found while testing live: the provider gateway read only the first system message, so the page context, turn plan and loop corrections never reached the model (Neva said "I can't see your editor"). All system instructions now go as one message. Live: explain, optimize and edit-per-city all worked; the edited SQL ran and returned rows per city | `test_provider_request_options.py`, `test_neva_editor_proposal.py`, page-work trajectories in `test_neva_lookups_and_views.py` |
| 2026-11-10 | 4–5 | Ontology reader boundaries and Neva glossary tools; SQL checked as the caller before it reaches the editor; written objects read back; source labels; seven more page surfaces; frameless diff review | `f144c34`, `8402b26`, tests listed in phases 4–5 |
| 2026-11-10 | 6 | Test half solved 0.21 → 0.82 (0.73–0.91), correct 0.95, pass^3 0.75; page context solved 0.92, used the page 1.00; no leaks; Ranger acceptance green; two independent reviews fixed | `phase-6-checkpoint.md` |
| 2026-11-10 | 6b | Data tasks solved 0.38 → 0.88 (0.75–1.00): usual readings stated, presentation requests never block, years and names in results no longer refused, declared units decide percent; DA04 view choice still wrong | `phase-6b-checkpoint.md` |
| 2026-11-10 | 6c | Data tasks solved 0.96 (0.88–1.00), correct 1.00, no wrong-and-confident: the official view is used for an approved metric, answers name the view that answered, rounded cells and no-data years are backed | `phase-6b-checkpoint.md` |
