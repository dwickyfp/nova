# Architecture 8: Auto Harness Validation

> Validation record for the durable Nova Studio multi-agent runtime, 2026-09-24–25.

The Auto unit, evaluation, frontend, live two-agent, and real worker-kill checks
below pass. The reported 2025 comparison has also been rerun with the configured
Sales model and dummy data. Event replay is durable and ordered, but a crash
between reserving a sequence and inserting its row can leave a sequence gap.

---

## Architecture discovered

Studio already had direct agent selection, a bounded `AssistantLoop`, tool consent,
role verification, StarRocks assistant threads and messages, agent run journals,
SSE replay, semantic models, memories, and a separate task worker. Direct agent
turns ran in the API process. There was no durable coordinator, child hierarchy,
or agent mailbox.

## Architecture implemented

Studio's platform-owned `__auto__` entry starts a StarRocks root run. A separate
`app.agent_worker` claims queued runs, plans with accessible capability manifests
and declared semantic ownership, creates isolated child runs, and executes up to
four concurrently. The root stores a checkpoint and enters `waiting_for_agent`;
child completion or a mailbox message requeues it. It may steer a running child
or add another specialist before synthesizing one answer. A direct agent still
uses the existing AssistantLoop route.

The code is in `app/modules/agents/{auto_planner,capabilities,harness_repository,
harness_tools,harness_worker}.py`, `app/agent_worker/`, the agents router, and the
assistant loop/provider/repository. The Studio UI and client are in
`frontend/src/features/{studio/studio-chat,agents/api}.tsx`/`.ts`. `dev.sh` starts
the worker. `docs/arch-08-agent-harness.md` describes the design; `HOW_TO_RUN.md`
describes operation.

## Persistence and APIs

Startup adds nullable hierarchy, checkpoint, lease, generation, and usage fields
to `CONFIG_AGENT_RUNS`, and creates `CONFIG_AGENT_CAPABILITIES`,
`CONFIG_AGENT_MESSAGES`, and `CONFIG_AGENT_SESSION_EVENTS`. The existing event
table gains a session sequence. All state stays in `NOVA_SYSTEM`.

`/api/v1/agents/capabilities` exposes role-scoped discovery and manifest updates.
`/__auto__/threads/{thread_id}/messages` creates a root and streams persisted
events. `/auto/threads/{thread_id}/runs`, `/auto/runs/{root_run_id}`,
`/messages`, and `/events` inspect durable state. Child messaging, root
cancellation, and child cancellation have dedicated POST endpoints.

## Runtime and security

Spawn and send use stable operation IDs. Mailboxes persist sender, recipient,
origin, correlation, and consumption state. A checkpoint callback injects
messages before the next specialist model call. Child context and memory remain
isolated. The worker checks the live session, security version, active role, and
verified agent access before execution and at child checkpoints. Mutating tools
are denied without attended consent. Depth, child count, concurrency, token,
iteration, tool, and wall-time limits are bounded. Actions write audit records.
Cancel writes each child's terminal event before the root terminal event. A
scoped replay reconciles a terminal child or a failed/cancelled root if its
status was committed before its terminal journal row became visible. Existing
matching events are reused, and a conflicting terminal status fails closed.
Child activity records verified access and actual loop bounds, selected
capabilities, plan and work-phase frames, tool progress and results, received
coordination messages, and the answer. It is a bounded public projection of
the shared loop, not raw private model reasoning. Credential-shaped text in
activity and table evidence fails closed to `[redacted]`; secret-shaped mailbox
content is rejected before persistence. A visible omission marker identifies
the activity cap.

The frontend shows Auto before accessible specialists. Each Auto turn binds its
root run to the originating user message. A process rail reads the persisted
event journal and reports the actual queued, started, delegated, waiting,
message, tool, and completed states as they occur. It is no longer a static
"Starting analysis" placeholder while a specialist works. A compact floating
card on desktop lists Main and the spawned specialists; a compact in-flow card
serves narrow screens. Selecting a specialist opens a compact-header
conversation panel beside the main chat on desktop; narrow screens switch the
visible column within the same page. The panel shows the delegated task,
bounded loop activity, messages in both directions, and verified answer. User
context can be sent to an active child; cancellation and replay use the same
durable run. The panel does not create a second agent engine or expose raw model
drafts. Direct-agent selection remains
available. Studio's visible top header and Nove Assistant dock were removed so
the chat is the primary surface. The requested antislop guidance was applied
during UI, copy, responsive layout, and code-comment work.

## Verification

| Check | Result |
| --- | --- |
| Backend full suite on the standard local configuration | **4,245 passed**, 130 fixture skips, 3 dependency/data warnings; localhost socket tests run with socket permission |
| Scripted behavior scorecard | **48/48 scenarios**, **165/165 checks** |
| Offline assistant benchmark suite | **15 passed**, 1 skipped |
| Frontend browser test suite | **835 passed** in 108 files |
| Frontend production build | **Passed** |
| Frontend lint | **0 errors**, 22 existing warnings |
| Backend Ruff on changed harness, API, tests, and probe files | **Passed**; repository-wide Ruff still reports 75 findings outside this scoped check |
| Isolated seeded StarRocks integration suite | **165 passed**, 17 fixture skips, 1 warning |
| Parquet and RBAC integration modules on isolated test stack | **6 passed** |
| Live two-specialist smoke | **Passed on the final single-worker stack** (root `40875c4b-bf22-4a12-8167-b041df3854e6`): both children and root completed; both message directions, 11 replay events per child, three terminal events, and one final answer with both specialist markers verified |
| Live Studio browser | **Passed fresh active turn and saved replay**: Sales child spawned, user steered it, root and child completed; 25 child events replayed, with the five-channel table, saved Sales Agent name, history, keyboard, mobile, and both themes verified; no browser or API errors |
| Live Studio split panel | **Passed fresh and saved replay with a dummy specialist**: the root and child completed; persisted `thinking`, `plan`, `done`, and `answer` plus a user-to-child message appeared in the panel; desktop chat remained usable beside it; dark/light 320, 375, and 1440 px had no overflow, dialog backdrop, body lock, or browser/API errors; all dummy run/agent/thread/message/event/mailbox counts returned to zero |
| Live Studio cancellation | **Passed in a separate dummy Auto run before the split-panel revision**: browser Cancel returned 200; the child stored `cancelled` and `agent_cancelled`, the child conversation became read-only, and saved history replayed the cancelled child; no browser or API errors |
| Live root cancellation | **Passed with a running root and child**: both persisted as `cancelled`; child terminal event preceded root terminal event; scoped child timeline replayed `agent_cancelled`; SSE ended with `error` and `done`; no assistant final was saved; all eight dummy record counts returned to zero |
| Live worker SIGKILL and recovery | **Passed in an isolated two-phase probe**: a killed root was requeued and completed with one saved final; a killed child became `interrupted` without re-execution, its terminal event replayed, and the root completed with one saved final |
| Terminal-event and cancellation regression | **89 final targeted tests passed**: failed/cancelled root replay ends its SSE stream, root Cancel orders child before root, transient malformed terminal reads recover, zero-affected UPDATE is checked against persisted state, existing event repair is idempotent, conflicting terminal fails closed, and scoped child replay repairs a missing terminal |
| Child activity and redaction regression | **136 targeted tests passed**: real harness bounds, selected capabilities, coordination receipts, tool frames, and bounded replay are visible; credential-shaped activity/table values fail closed and mailbox tokens are rejected before persistence |
| Diff whitespace check | **Passed** |

The final live Studio Sales run used root
`691b06d2-636f-4282-bd89-2e5bb740293c` and child
`1c8f6ceb-904c-57cb-bd88-d48c17b27c35`. The separate browser cancellation
used root `cdf9f018-2326-48f8-8c7c-cb7e75708e42` and child
`ec1cf219-fd74-59d1-926e-bb654fc2e26e`. The browser script is
`frontend/scripts/probe-studio-harness-ui.mjs`; final captures are under
`frontend/__screenshots__/studio-harness-1790279515809/` and
`frontend/__screenshots__/studio-harness-1790280397819/`.

The split-panel browser probe used a short-lived dummy user and Sales Agent
fixture. Its final root was `bd76482a-797e-4d4f-a940-d01102843fdc` and child
`7a7b52aa-2ee8-5b98-87ee-b08692940fe2`. The synthetic model selected no
tool, so the live trace truthfully displayed “No tools selected”; tool-call and
progress rendering remains covered by component and backend event tests.
Screenshots are under `frontend/__screenshots__/studio-split-panel-complete-20260925/`.

Two intermediate re-runs of the two-specialist smoke exposed issues that were
fixed before the final pass. A worker whose queue was occupied could claim a
child before a transiently unavailable start event and leave it running; start
journaling now has bounded retry and lease-fenced requeue before child tools
execute. A completed run then exposed a malformed StarRocks terminal row during
page-two child replay; the terminal lookup now retries and preserves the cached
sequence without rewriting a completed event. The final smoke tested the full
run, paged replay, both message directions, and one persisted final answer.

An additional live run exposed a replay failure when a specialist used a tool:
the event reader rejected its own `tool_activity` event. The event types now
share an explicit allowlist for writes and reads. The affected run's 11 stored
events, including `tool_activity`, were replayed through the SSE generator
without an exception. A regression test covers that event type.

A later synthetic smoke lost its API connection when stale-run reconciliation
tried to write an event for a root already removed by test cleanup. The agent
worker exited, which made `dev.sh` stop the other local processes. Recovery
now retries a temporarily invisible root and skips only that orphan event when
the root remains absent. The same two-specialist smoke then passed end to end.

A later replay audit found that concurrent child terminal events could receive a
sequence before their StarRocks writes became visible. Event writes now use a
renewing Redis lock and atomic sequence counter. Replay reads issue `SYNC` on
their query connection, and root completion reconciles child terminal events.
The final live smoke verified all three terminal events and a final answer
containing both sample agent results. Cancellation after root completion no
longer emits a conflicting terminal event; deletion blocks late event writes.

The smoke creates temporary Finance and Marketing sample agents, a temporary
role/user, and a Studio thread, then removes them. Run it with
`STARROCKS_FE_MYSQL_PORT=29030 RANGER_ENABLED=true .venv/bin/python -m
scripts.smoke_auto_two_agents` from `backend/` on a configured local stack.

The isolated worker-loss probe used `scripts.probe_auto_worker_kill` with the API
up and no competing Auto worker. It killed the process with `SIGKILL` after a
dummy root claim, backdated only that claimed lease to trigger the normal stale
recovery path, and verified resumed root `eea87ff7-727a-420b-92c8-1cff778aec0f`
and completed child `953088a1-b704-5565-a64e-9f0996567a0b`. It then killed a
worker after a child claim and verified completed root
`6af03da7-bbec-48c8-891f-800fb94734f6` and interrupted child
`7bc255a2-c764-5800-8253-ce62b3ddb7a0`: no child completion or re-execution,
with its interruption and the root final available through replay. Both phases
exited successfully; the probe removes its temporary user, role, agents, threads,
and run rows. The normal `dev.sh` stack was restored afterward.

The separate `scripts.probe_auto_root_cancel` first exposed a real race: a
StarRocks UPDATE could persist `cancelled` while reporting zero affected rows,
leaving an active child and an incorrect HTTP 409. Root and child cancellation
now verify their scoped persisted status with a bounded retry. A later probe
exposed a transiently malformed terminal-event lookup after both runs had
cancelled. Terminal lookup now uses `SYNC` and requires stable valid reads before
inserting a new terminal event; malformed reads remain bounded and fail closed.
The final live probe passed with root
`ef4f6c29-0265-4728-ac7a-3132d68b2bda` and child
`f37bde22-19ef-5480-947d-bd281ea6f568`. Child `agent_cancelled` sequence
`179028378287100` precedes root `179028378352500`; replay after the root
terminal returned exactly `error` and `done`. The probe cleaned every dummy
user, role, agent, capability, thread, run, event, and mailbox row it created.

The new tests cover semantic ownership, accessible discovery, duplicate spawn
and message prevention, safe checkpoint steering, parallel execution, dynamic
delegation, suspend/wake, stale lease handling, cancellation, role change,
budget limits, event ordering, final-answer deduplication, API scope, and
frontend activity. Existing direct-agent behavior remained in the passing
regression suites.

## Numeric evidence in Auto answers

The reported 2025 channel comparison reached the Sales specialist and its SQL
query returned rows. The numeric answer check then rejected valid rounded
currency and percentage values. A direct-agent run of the same question also
hit that check. Auto compounded the failure by retaining the specialist's text
but dropping its result table before coordinator synthesis.

The numeric check now validates explicit decimal rounding, Indonesian and
English amount scales, and ratio-to-percent formatting against the matching
table row. The child run stores a bounded, redacted table preview in its
checkpoint. The coordinator receives that preview as structured evidence,
checks its own numeric claims, and includes the authorized result table in the
final answer. Unsupported numeric claims are replaced with the table; if a
data task has no query result, the coordinator cannot invent a number. A
scripted Auto evaluation exercises the child query, root synthesis, and
unsupported-number fallback with synthetic data.

The preview keeps the latest three tables, up to 20 rows and 12 columns each;
omissions are marked. The user confirmed that the Sales data is dummy data and
authorized the configured external model for the live query test.

At the start of that test, Sales Agent was absent from Studio and Auto discovery
because its stored access fingerprint no longer matched the current Semantic
View dependencies. Nova's Verify Access operation for `ACCOUNTADMIN` confirmed
all nine dependencies and refreshed the fingerprint; Sales Agent appeared in
both surfaces afterward. This was a configuration verification state change,
not a change to the Sales data or permissions.

An independent, authenticated read-only SQL query through Nova's query API
returned the following 2025 baseline. The active Semantic View defines revenue
as `SUM(net_revenue)`, margin as `SUM(gross_profit) / SUM(net_revenue)`, and
orders as `COUNT(DISTINCT sale_id)`, excluding cancelled orders.

| Channel | Recognized revenue (IDR) | Gross margin ratio | Order count |
| --- | ---: | ---: | ---: |
| Marketplace | 2,893,685,878,419.00 | 0.36990534 | 152,078 |
| Mobile App | 3,368,049,065,451.00 | 0.36962540 | 177,450 |
| Store | 3,013,024,030,096.50 | 0.36952650 | 158,569 |
| Website | 1,935,135,942,274.50 | 0.36930145 | 101,418 |
| WhatsApp B2B | 847,526,929,200.00 | 0.37028179 | 44,266 |

The exact question was then exercised through direct Sales run
`5b1ad305-e605-4bc9-b87e-da6d6ba2e3db` and Auto root run
`6d695468-3e57-4574-a558-9fa426d3cec4` with Sales child
`84708748-9354-5ded-ba8b-fdbf0dcf3e8f`. Both final answers used
deterministic comparison rendering from the verified table and matched the five
baseline channels and values on independent reread. The Sales child selected
and ran its query through the model and tool path. The Auto coordinator also
ran provider synthesis before the final comparison rendering. The child retained
its query evidence; the root persisted one final message, 11 events, and a
replayable SSE timeline. Earlier live direct drafts included one unsupported
number and one incorrect qualitative ranking. Those observations drove the
multirow comparison rule, which derives both values and rankings from the
authorized result table.

The Auto root also saw one transient capability metadata read failure during
replanning after the Sales child completed. Later lookups showed no stored Sales
capability manifest, so the default manifest path is valid; the exact cause of
that one inconsistent read is not yet established. Recovery uses the completed
Sales finding rather than losing its verified query result.

A fresh full live probe after the visibility guard and event reconciliation
passed: direct Sales run `462b14f2-5ce1-4e4f-9c43-f189f22a5b77`, Auto root
`8b34ffbe-fa47-42a7-9476-ca7a10b69340`, and Sales child
`a51bdeb1-c309-5724-9fc5-fb03637d1650`. The direct stream and replay matched
the five-channel SQL baseline, including the correct high and low values. Auto
retained the child's query evidence, wrote 11 ordered events, replayed them over
SSE, and persisted one matching final message. All numeric claims in that final
answer were supported by the independent baseline. The local API and Vite
frontend both returned HTTP 200 during the probe. This validates the observed
paths and does not establish universal 100% correctness.

After the event read path was optimized, the completed 2025 Auto run still
replayed 11 stable ordered events without requiring an event writer. A fresh
two-specialist smoke run also passed: both specialists completed, the
user-to-child message and child-to-root finding persisted, the root consumed
the finding, and replay showed one final answer. The final full backend and
frontend suite counts are recorded in the verification table above.

After the inspectable child timeline was added, the live direct Sales run
`6fd57a91-7d7d-46d5-969b-a67ea41f11b2` and Auto root
`60e7bbcf-b458-45cd-933d-c1bb837c1fbd` with Sales child
`05363b17-5c25-586e-af31-e338ec410890` passed the same independent
five-channel SQL comparison. The Auto journal replayed 31 ordered root events;
the scoped child endpoint replayed 24 ordered events including the child's
bounded work trace and one verified answer. The root's user message ID and
deterministic final message ID matched the Studio thread on reread. The
scripted behavior scorecard remained at 48/48 scenarios and 164/164 checks,
and the standard full backend suite passed 4,190 tests with 130 skips.

The final browser run used Auto root `b46dd6a3-8def-4391-8e97-157a968c206a`
and Sales child `4f53b425-0681-5ad8-adce-7f30541cd56e`. While the child was
working, the rail showed its real call and start, the drawer accepted a user
message, and that message appeared as “You to Sales Agent.” Both runs completed
without browser/API errors. The first immediate child timeline read returned
an empty page despite the root journal containing 29 events. The child endpoint
now uses the completed root's terminal anchors and retries a missing child
start before advancing its cursor. A replay after restart returned 25 ordered
child events, including queue, start, message, harness work, tool, answer, and
completion. The final five-channel answer and child table rendered in the
browser. Browser checks covered keyboard Enter/Escape, the 44 by 44 px close
target, 320/375/1440 px layouts, dark/light themes, and no outer overflow.
The active turn preceded the timeline patch; the patched build's replay of that
turn passed.

The final serial two-specialist smoke used root
`802b061e-d289-4a30-93cb-2e02488f2bd5` and children
`2506ee22-7463-5312-8311-f40b4ab8c467` and
`fe904a6c-e9a5-52d0-a035-4a90fcb6b823`. Both children and the root
completed; each child replayed 11 ordered timeline events with both message
directions, activity, answer, and terminal status. The root consumed both
findings and stored exactly one deterministic final Studio answer containing
both sample markers. The smoke removed its temporary run, thread, agents,
user, and role. A previous concurrent smoke exposed a worker recovery conflict
between a stale-looking child row and its existing completion event. Recovery
now restores the prior terminal state under a run/root fence and contains an
unreadable prior-terminal case rather than stopping the worker. Regression
tests cover both outcomes.

## Studio UI delivery gate

The user chose antislop **during** implementation. The named antislop
`SKILL.md` was unavailable in this session; its existing project checklist
below and `skills/nova-page-layout/SKILL.md` guided the revision during the
work. The Design Read used the Nova console palette and type, plus the supplied
Codex interaction reference. The declared dials are ENERGY 1, RHYTHM 2,
MOTION 1. The main conversation remains the focal point; the event rail carries
progress and the floating card is a compact secondary index. Selecting a
specialist opens a same-page right panel with a two-row header. Orange
identifies Nova actions, while status colors communicate real run state.

The following is the final antislop PASS/FAIL report for the new Studio Auto
surface. Every line is a **PASS** based on the cited implementation or check.

### Hard gate

- R-02 PASS: New user-facing labels have no em dash; the rail uses plain status sentences.
- R-03 PASS: Browser replay at 320, 375, and 1440 px in both themes had no document overflow; the mobile panel filled the content area beside Nova's 56 px navigation rail.
- R-17 PASS: The card shows persisted run counts and statuses, with no invented statistics.
- R-18 PASS: No testimonials or invented people appear in this surface.
- R-23 PASS: The existing Nova mark was reused; no avatar, navigation link, or visual asset was invented.
- R-24 PASS: Existing sidebar navigation was retained; the new child rows open real scoped conversations.
- R-25 PASS: The contrast checker measured dark title/muted/status at 18.09:1, 7.97:1, and 9.02:1; light at 20.16:1, 5.20:1, and 5.07:1.
- R-26 PASS: Browser clicked child card, rail, send, Cancel, and close; component tests exercised retry actions.
- R-27 PASS: The card and panel show specific loading, empty, failed-load, and retry states.
- R-28 PASS: No FAQ was added.
- R-32 PASS: Browser used Tab, Enter, and Escape with the panel; the desktop chat composer remained focusable beside it, and focus returned when the panel closed.
- R-33 PASS: The feature is implemented in React and Tailwind source, with no script that patches CSS or source text.
- R-34 PASS: Browser captured dark and light at 320, 375, and 1440 px without broken layout or console errors.
- R-35 PASS: Production build passed; 835 frontend tests passed; browser checked the split panel, live activity, and replay.
- R-36 PASS: UI copy reports persisted activity and makes no security, compliance, or performance claim.
- R-37 PASS: Direction and ENERGY/RHYTHM/MOTION dials were set before UI work from the existing Nova identity and user reference.
- R-38 PASS: Agent names, tasks, events, messages, statuses, and SQL evidence come from scoped APIs, not fabricated UI content.

### Purpose gate

- R-01 PASS: No new gradient or glow was used.
- R-04 PASS: Workflow, chevron, close, and send icons identify the corresponding action or hierarchy.
- R-06 PASS: Existing Nova type styles distinguish title, status, and activity without display monospace or decorative uppercase.
- R-07 PASS: No decorative background grid was added.
- R-08 PASS: The arrow icon occurs only on the send action.
- R-09 PASS: Run status is plain text with a small dot, not an ornamental capsule badge.
- R-10 PASS: The card and panel use opaque surfaces, with no glass effect.
- R-12 PASS: The floating card has one restrained shadow to separate it from the transcript.
- R-13 PASS: No decorative glow was added.
- R-14 PASS: The surface has an activity index and conversation panel, not repeated feature cards.
- R-19 PASS: Motion is limited to small hover feedback; reduced-motion styling is present on the rail affordance.
- R-22 PASS: No illustration was added.

### Liveliness

- Dials PASS: ENERGY 1, RHYTHM 2, MOTION 1 are declared and match the restrained hierarchy and motion.
- Focal point PASS: The user question and answer occupy the main column; the activity card remains secondary.
- Whitespace PASS: Space separates the main conversation from activity rather than filling the screen with decorative sections.
- Accent PASS: Nova orange is reserved for actionable controls and existing brand identity.
- Motif PASS: The same run hierarchy appears in the main rail, compact card, and detail panel.
- Design Read PASS: The established console identity and the user's supplied interaction screenshots were reviewed before implementation.

### Craftsmanship and quality locks

- C-1 PASS: Each new visual element maps to run state, hierarchy, navigation, or readability.
- C-2 PASS: New controls have tested actions: open, send, cancel, close, and retry.
- C-3 PASS: The rail, card, and panel expose actual agent work; no filler section was added.
- C-4 PASS: Empty/loading/error states, both themes, narrow and wide screens, and keyboard use were tested; the footer includes safe-area padding.
- C-5 PASS: The new surface shows persisted run data and no fabricated claim.
- R-05 PASS: The chat, in-flow mobile card, desktop floating card, and optional split panel each serve different content needs.
- R-11 PASS: The input, card, row, and panel use different radii according to interaction and grouping.
- R-15 PASS: Actions say “Send to subagent,” “Cancel agent,” and “Retry,” not generic calls to action.
- R-16 PASS: No AI marketing buzzwords were introduced.
- R-20 PASS: The visible event hierarchy and conversation panel express Nova's real specialist workflow.
- R-21 PASS: The existing theme system supports both modes; neither mode was forced by the new surface.
- R-29 PASS: Existing neutral surfaces and one brand accent dominate; semantic status colors identify state.
- R-30 PASS: The supplied Codex reference guided the interaction, while Nova's own navigation, type, palette, and event vocabulary remain.
- R-31 PASS: Card placement keeps status available, the rail explains progress in context, and the panel gives the child enough reading width without covering desktop chat.

The mobile supplement also passed: distinct in-flow card at narrow widths,
same-page child panel occupying the content area beside the 56 px navigation
rail, no outer overflow, keyboard and touch equivalents, 44 by 44 px close
target, and bottom safe-area padding. The main chat column swaps back on Close.

## Definition of Done

| Item | Status | Evidence or limit |
| --- | --- | --- |
| Auto exists in Studio | PASS | Studio list and browser tests |
| Direct accessible agents remain selectable | PASS | Direct route unchanged; regression suite |
| Accessible agent discovery | PASS | Role tests and live smoke |
| One specialist spawn | PASS | Unit trajectory |
| Multiple async specialists | PASS | Concurrency test and live smoke |
| Isolated child contexts | PASS | Child runtime and tests |
| Root messages during child work | PASS | Coordinator steering test |
| Child messages during root/other work | PASS | Mailbox test and live smoke |
| Safe checkpoint visibility | PASS | Eval trajectory |
| Dynamic specialist addition | PASS | Scout finding test |
| Root suspend | PASS | Checkpoint test and live waiting state |
| Root resume | PASS | Wake test and live completion |
| Durable runs, messages, events | PASS | StarRocks-backed repository and live replay |
| Inspectable run hierarchy | PASS | Scoped API and live tree |
| Cancellation | PASS | Root/child repository and API tests, live browser child Cancel/replay, and live root Cancel with child-before-root event order |
| RBAC | PASS | Owner/role and role-change tests |
| Budgets and limits | PASS | Budget and depth/count tests |
| Reconnect | PASS | SSE replay test and live reconnect via read APIs |
| Restart/reconciliation at safe boundaries | PASS | Stale lease tests and live root/child `SIGKILL` probe with replay |
| Idempotent retries | PARTIAL | Stable spawn/message/final IDs tested; cross-table crash may leave an event sequence gap |
| Duplicate spawn prevention | PASS | Stable operation ID test |
| Duplicate message processing prevention | PASS | Durable mailbox test |
| Final answer once | PASS | Replay test and live smoke |
| Auto clearly visible | PASS | Picker and browser test |
| Execution tree visible | PASS | Browser component test |
| Live status | PASS | Polling UI and browser test |
| Child activity visible in the UI | PASS | Persisted safe `thinking`, plan, tool activity, answer, and terminal events; live split-panel replay and redaction tests |
| Child conversation beside main chat | PASS | Same-page desktop panel, compact header, usable main composer, and mobile content swap in browser QA |
| Simple direct chats | PASS | Separate direct path and regression suite |
| Child work absent from sidebar | PASS | Child runs use internal IDs, not Studio threads |
| No chain-of-thought exposed | PASS | Public event and UI fields restricted to safe activity |
| New tests pass | PASS | Backend/eval/frontend suites |
| Relevant existing tests pass | PASS | Full suites and isolated integration run |
| No regression ignored | PASS | Socket sandbox failures were rerun with localhost access and passed |

## Known limits and next steps

StarRocks occasionally returned an inconsistent row during the live run.
Identity, shape, and scoped count checks with bounded retry now protect Auto
discovery, run, event, message, and conversation-history reads. The broader
database driver behavior merits separate diagnosis under concurrent workload.
A crash between reserving and inserting an event can leave a sequence gap;
replay remains ordered but is not a cross-table exactly-once transaction. Stale
child tools are interrupted rather than replayed because side effects cannot
be assumed safe. A lost session waits for authentication and eventually
expires. A production deployment must supervise `app.agent_worker` separately.
The 17 skipped integration cases require their own platform/provider fixtures.
Repository-wide Ruff currently reports 75 findings; the changed harness,
route, test, and probe files pass their scoped Ruff check.
