# Architecture 8: Studio Auto Agent Harness

> A durable coordinator runs authorized specialists in parallel while direct agent chat keeps its existing path.

---

## Architecture

```text
Studio thread
  ├─ direct agent turn → AssistantLoop and existing run journal
  └─ Auto turn → CONFIG_AGENT_RUNS root
       ├─ specialist run → isolated AssistantLoop
       ├─ specialist run → isolated AssistantLoop
       ├─ CONFIG_AGENT_MESSAGES (root ↔ child)
       └─ CONFIG_AGENT_SESSION_EVENTS (one session timeline)
                         ↓
                 synthesized Studio answer

API → NOVA_SYSTEM queued run → nova-agent-worker → waiting checkpoint
                                             ↑
                         child completion or message requeues root
```

The API writes an Auto root and returns an SSE stream. The separate agent worker
claims queued rows from StarRocks. The SSE connection only reads persisted events;
closing the browser does not stop the run. The root creates children with stable
operation IDs, persists a waiting checkpoint, and releases its worker slot.
Children can report findings during execution. Auto can then steer a running child
or add another specialist. A message appears to the child before its next model
call. The root synthesizes once every child has settled.

## Implementation

`app/modules/agents/harness_repository.py` owns the run hierarchy, mailboxes,
events, claim transitions, cancellation, and stale-run reconciliation.
`app/modules/agents/harness_worker.py` owns coordinator and child execution.
`app/modules/agents/auto_planner.py` retrieves compact capability manifests,
resolves declared semantic aliases, and asks the configured provider for a
structured plan. Studio's selected chat model is retained on the root run; if
none is selected, the planner uses the highest-ranked accessible specialist's
chat model. The planner accepts only accessible agent IDs. Semantic owner
matches rank ahead of supporting domains, and overlapping words alone do not
select a specialist.

The existing `AssistantLoop` remains the child engine. Its optional checkpoint
callback supplies coordination messages before a model call. Ordinary tools
remain serialized. Child context contains its own agent instructions, tools,
skills, semantic models, and bounded delegated task. Internal coordination
messages do not enter persistent user memory.

The runtime caps depth at one, total children at four, active worker tasks at
four per worker, child context at 20,000 tokens, coordinator use at 30,000
tokens, aggregate session use at 120,000 tokens, and session time at ten minutes.
Each child also respects its configured wall-time budget up to five minutes.
Mutating child tools require consent and are denied by the unattended worker.

## Integration Points

Studio exposes Auto in the same selector as accessible direct agents. Direct
agent turns still use the existing run journal and API process path. Auto uses
the following endpoints under `/api/v1/agents`:

| Endpoint | Purpose |
| --- | --- |
| `GET /capabilities?query=...` | Discover accessible, ranked manifests |
| `PUT /capabilities/{agent_id}` | Set an owned agent's manifest |
| `POST /__auto__/threads/{thread_id}/messages` | Create a root and stream its events |
| `GET /auto/threads/{thread_id}/runs` | List roots for a Studio thread |
| `GET /auto/runs/{root_run_id}` | Inspect the run tree |
| `GET /auto/runs/{root_run_id}/messages` | Inspect coordination messages |
| `POST /auto/runs/{root_run_id}/children/{child_run_id}/messages` | Send user context to an active child with an operation ID |
| `GET /auto/runs/{root_run_id}/children/{child_run_id}/timeline?after=...&limit=...` | Page one child's durable conversation and work trace |
| `GET /auto/runs/{root_run_id}/events` | Replay the session timeline |
| `POST /auto/runs/{root_run_id}/cancel` | Cancel the root and descendants |
| `POST /auto/runs/{root_run_id}/children/{child_run_id}/cancel` | Cancel one child |

Studio binds each Auto root to the user turn that created it. Its main process
rail projects the persisted root events into concrete delegation updates, so a
queued or running child is visible before the final answer. The compact agent
card reports status and opens a right-side child conversation panel beside the
main chat. That conversation
pages the same session journal in sequence and shows the delegated objective,
the child's bounded loop activity, parent-to-child and child-to-parent messages,
and the child answer. The card never substitutes a final summary for the child's
transcript. Reopening the Studio thread reconstructs both views from durable
events, including after a browser disconnect.
On desktop the child panel shares the page width with the main chat, so both
remain usable. On narrow screens it replaces the chat column in the same page
layout and offers a back/close control; it does not mount a modal backdrop.

Timeline reads enforce the root owner, active role, Studio thread, and child
lineage. The worker saves a bounded, redacted projection of child loop events;
it does not expose provider frames, credentials, or unverified draft answer
tokens. The final answer event is written after the same evidence checks used
for the coordinator. Each child continues to run the shared `AssistantLoop`
with its own instructions, context, time and token budgets, tool catalog, and
fail-closed consent policy. The split panel is an observation and coordination
surface, not another execution engine.

The worker resolves the same live Redis session as the API. It checks the
session security version and active role before each run and child model
checkpoint, and verifies agent access when discovering, spawning, and executing.
Children run under the user's StarRocks credentials and active role. No agent
owner or system credential is substituted.

## Configuration

`./dev.sh` starts `python -m app.agent_worker` alongside the backend, scheduler,
task worker, and frontend. A non-development deployment must run this command
as a separate supervised process. StarRocks is the durable queue source of
truth; the worker polls queued rows and reconciles stale leases. Redis Streams
are not required for correctness in this version.

Startup adds nullable hierarchy and lease columns to the existing
`CONFIG_AGENT_RUNS` table and creates `CONFIG_AGENT_CAPABILITIES`,
`CONFIG_AGENT_MESSAGES`, and `CONFIG_AGENT_SESSION_EVENTS`. Existing direct
history remains readable. An additive `origin` column distinguishes user
messages from agent messages. Message rows are clustered by recipient and creation
time; session events have a root-local sequence and a stable event ID. Existing
event tables gain the sequence column and their earlier rows are backfilled.
Schema changes are additive,
and the repository has no down-migration convention.

## Recovery and Limitations

Coordinator spawn IDs and message IDs are stable across a retry. A stale root
requeues and rebuilds its plan from the checkpoint. A stale child becomes
`interrupted`: its tools are never replayed because an external side effect
cannot be assumed idempotent. A partial answer can still use other child
findings. Messages are acknowledged after their content is saved in the root
checkpoint; a child acknowledges messages after its loop settles. The final
Studio message has a stable ID.

The event sequence is reserved with a conditional update on the root row.
Concurrent children cannot claim the same number. A process crash between
reservation and event insertion can leave a gap. StarRocks does not make the
counter update and event insert one cross-table transaction, so this version
does not claim exactly-once event delivery across such a crash. Reconciliation
requeues stale roots and marks stale children interrupted. A lost live session
enters `waiting_for_auth` and times out safely; reconnecting with a new session
does not resume that run.

The reproducible local smoke test is
`backend/scripts/smoke_auto_two_agents.py`. It creates two temporary private
specialists and a temporary StarRocks user, runs the Auto worker against the
local API, and verifies discovery, concurrent child completion, user-to-child
messaging, child-to-root mailbox consumption, event replay, and one persisted
Studio answer. It requires a
configured live chat model and removes its temporary records afterward.
