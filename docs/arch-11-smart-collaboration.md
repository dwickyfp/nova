# Architecture 11: Smart Collaboration

> Smart and its specialists share one bounded assistant engine, durable mailboxes, and a reusable participant lifecycle.

## Architecture

```text
Studio thread → __smart__ root participant /root
                  ├─ /root/finance
                  │    └─ /root/finance/forecast
                  └─ /root/marketing
                       └─ /root/marketing/cohort

Every participant → AssistantLoop + AgentControl
AgentControl → authorized discovery / admission / direct mailbox / follow-up / wait
StarRocks → turn journal + message journal + ordered event journal
Redis → admission and event locks + wake notifications
```

Smart discovers relevant specialists and delegates through ordinary provider tool
calls. Discovery ranks authorized capability manifests and semantic ownership;
it does not execute a delegation plan. Specialists use the same collaboration
tools and can message one another without asking Smart to relay their findings.
Direct specialist chat outside Smart retains its existing execution path.

## Implementation

### Identity and lifecycle

`identity.py` defines the canonical `__smart__` identity and immutable `AgentPath`.
Paths are unique within one collaboration, start at `/root`, and consist of
lowercase letters, digits, underscores, or hyphens. Absolute paths, `..`, and `.`
resolve inside the caller's authorized tree; traversal above the root fails.

A participant session is the stable identity and mailbox. A turn is one bounded
execution. The first turn's UUID anchors the session; subsequent journal rows
refer to it through `payload.agent_session_id`. Each turn also records its number,
path, parent session, previous turn, and initiating message. This reuses the
existing primary-key run journal instead of maintaining a second session table.
`session_views` reconstructs the participant tree from those rows.

```text
new participant → queued turn → running ↔ waiting → completed turn → idle session
idle session → followup_task → queued turn → running → idle session
running turn → interrupt → interrupted turn → followup_task may resume the session
session → cancel → closed session; future follow-ups are rejected
```

Multiple pending follow-ups are serialized within a session. The active turn
remains the visible turn until it settles. Waiting follow-ups do not consume an
execution slot. A completion releases capacity for later work, so four finished
specialists do not impose a lifetime cap of four.

### Collaboration operations

| Tool | Effect |
| --- | --- |
| `discover_agents` | Return authorized, ranked candidates and semantic matches |
| `spawn_agent` | Create a child session and its first turn under the caller |
| `send_message` | Persist `QUEUE_ONLY` information; never start inference for an idle recipient |
| `followup_task` | Persist `TRIGGER_TURN` input and create one turn in an existing session |
| `list_agents` | Inspect paths, states, current turns, summaries, and bounded evidence |
| `wait_agent` | Await any/all selected participants, incoming mail, interruption, or timeout |
| `interrupt_agent` | Stop the selected participant's outstanding turns while retaining its session |

These tools are injected into every Smart participant's registry. They are
runtime collaboration capabilities, not standalone data tools for direct chat.
Their coordination effects are idempotent; tools that access or modify data keep
their existing permission checks and consent rules. An unattended worker denies
actions that require approval.

Admission is serialized under a root-scoped Redis lock. Session creation,
follow-up admission, direct sends, interruption, and final synthesis use that
boundary. Spawn and follow-up IDs derive from the initiating turn and operation
ID; replay cannot create a second logical operation. Reusing an operation ID with
different content or a different delivery mode fails.

### Delivery, waiting, and recovery

Mailboxes persist in `NOVA_SYSTEM.CONFIG_AGENT_MESSAGES`. The recipient reads new
mail before model calls and before finalization. Messages carry origin, sender,
recipient, correlation, and reply identifiers and enter the model context as
untrusted coordination data. The worker saves the bounded loop snapshot and
consumed-message IDs before acknowledging the mailbox. A crash before that save
leaves mail pending; a crash afterward restores its existing context instead of
injecting it again.

Waiting subscribes to Redis notifications before inspecting durable state. This
closes the check/subscribe race. A missed notification is reconciled against the
journal at most 30 seconds later. A call waits no more than 60 seconds. Waiting
coroutines retain their leases but release the worker's runnable-capacity count.
Waiting for oneself or an ancestor's completion is rejected.

Snapshots preserve curated messages, usage, iteration, tool caches, deferred
provider calls, pending artifacts, evidence, and composition state. The existing
worker lease and generation fence all checkpoint writes. A stale Smart turn with
a safe snapshot returns to the queue. A crash across an unconfirmed mutating tool
boundary interrupts the turn instead of automatically replaying that mutation.

Completion notifications are reconstructed from terminal turns with stable
message IDs. Message events also have stable IDs; retrying a send repairs a crash
between mailbox insertion and timeline publication. Terminal events and the
root's final answer retain their existing deterministic identities. Finalization
checks for outstanding turns and unseen root steering under the admission lock.

### Evidence and security

Every participant retains the root's owner, active role, thread, authentication
session, and security-context version. Discovery and spawn use verified agent
access. Workers revalidate authentication, role version, and specialist access at
checkpoints. Existing tool execution enforces semantic, connection, SQL, and data
permissions; delegation does not copy privileged credentials or broaden grants.
Nova has no separate tenant/workspace identifier on this journal: isolation uses
the existing owner/role/thread/authentication-session boundary.

Context inheritance modes are deliberately bounded:

| Mode | Included data |
| --- | --- |
| `fresh` | Explicit delegated context only |
| `parent_summary` | Parent objective and available result summary, plus explicit context |
| `last_n_turns` | Objectives and result summaries from the parent's last three turns |
| `full` | All available parent turn objectives and result summaries within the same bound |

All modes retain at most 8,000 characters. They exclude raw authentication state,
credentials, private reasoning, and full provider transcripts. `full` refers to
the bounded public turn history, not an unbounded prompt fork.

Data questions require authorized discovery before root SQL execution. Discovery
matches configured metric names, synonyms, and dimensions against the complete
user question. When an authorized specialist owns the requested metrics, the
root delegates to that specialist. The specialist queries its own semantic
coverage directly; the root's routing instruction is not inherited as an
instruction to recursively delegate the same question.

Specialist query tables retain source-agent, source-turn, semantic-plan, and
model provenance through `EvidenceTracker` and numeric verification. Completion
checks require the matched metrics, dimensions, and explicit single-year period.
Catalog listings and coordination messages cannot establish business results.
Incomplete results receive bounded repair attempts, then an explicit error.
Checkpoint serialization preserves database decimal precision as decimal strings.

Final comparisons use verified business tables. Repeated projections are omitted
only when their source, model, query scope, and row values agree; the original
evidence remains available. Different periods, values, sources, and truncated
results remain distinct. Smart's prompt requires it to reconcile findings,
distinguish inference, and state uncertainty. The root waits
for outstanding turns before saving a final answer; an optional branch can be
interrupted when its result is no longer needed. Public activity includes
intentional messages, actions, tools, and results; Smart thinking/plan frames are
not published as private reasoning.

### Cancellation

Targeted interruption affects the selected participant's outstanding turns;
siblings and ancestors continue. It preserves the session for a follow-up.
Cancellation also marks the session closed, including an idle session. The
participant interrupt endpoint accepts `cancel=true` and `subtree=true` for
explicit subtree cancellation. Root cancellation retains the existing whole-tree
behavior. Interrupt and cancel operations write audit records.

## Integration Points

Studio offers one **Smart** entry. Its activity card is recursive and groups
follow-up turns under the same participant. Selecting a node opens its task,
timeline, messages, results, steering, interrupt, and cancel controls. An idle
specialist exposes a follow-up action while its root remains active. Selecting
Smart allows root steering. Headers and composers stay outside inner scrollers.

Studio history is one recency-ordered list across all agents accessible to the
current user and role, including historical Auto and Smart conversations. The
repository filters by owner before returning rows, and the API verifies access
once per distinct agent. Opening, renaming, or deleting a history item uses that
conversation's stored agent identity. Changing the composer agent does not filter
the sidebar. History caches are scoped to the user and active role.

Endpoints under `/api/v1/agents`:

| Endpoint | Purpose |
| --- | --- |
| `GET /threads` | List the user's accessible agent conversations together, newest first |
| `POST /__smart__/threads/{thread_id}/messages` | Start a Smart collaboration |
| `GET /smart/threads/{thread_id}/runs` | List current and historical roots |
| `GET /smart/runs/{root_id}` | Return session projection and all turn rows |
| `GET /smart/runs/{root_id}/messages` | Inspect the durable message journal |
| `GET /smart/runs/{root_id}/events` | Replay ordered events |
| `GET /smart/runs/{root_id}/children/{turn_id}/timeline` | Replay any depth; include all turns of its session |
| `POST /smart/runs/{root_id}/participants/{target}/messages` | Queue user steering, including root steering |
| `POST /smart/runs/{root_id}/participants/{target}/followup` | Start or queue another turn |
| `POST /smart/runs/{root_id}/participants/{target}/interrupt` | Interrupt; optional cancel and subtree flags |
| `POST /smart/runs/{root_id}/cancel` | Cancel the root and its active tree |

Historical `__auto__` threads and runs remain readable. Existing `/auto` endpoint
aliases remain available, and history queries accept both identities. New roots
and conversations use `__smart__`. The legacy planner is used only for persisted
Auto roots. Redis key names, final-message UUID namespaces, internal TypeScript
`Auto*` type names, and old exception names remain where changing them would
break replay or compatibility. They do not create another selector entry.

## Configuration

| Environment variable | Default and ceiling |
| --- | ---: |
| `SMART_MAX_AGENT_DEPTH` | 4 (root depth is 0) |
| `SMART_MAX_CONCURRENT_AGENTS` | 8 specialists, excluding the root |
| `SMART_MAX_TOTAL_AGENT_SESSIONS` | 32, including the root |
| `SMART_MAX_TOTAL_TURNS` | 128, including the root |
| `SMART_MAX_TOTAL_TOKENS` | 120,000 reported prompt + completion tokens |
| `SMART_MAX_WALL_TIME` | 600 seconds |

Settings may lower these bounds; root creation records the effective limits.
Per-agent context, step, time, consent, and tool limits remain in force. Tree
token totals are checked at admission and worker checkpoints. Concurrent provider
requests already in flight may complete before the next aggregate check.
The coordinator uses the root's 30,000-token limit; a specialist uses its
20,000-token limit. The root must not inherit the smaller specialist limit when
resuming after a wait.

Schema initialization adds `delivery_mode VARCHAR(16) NOT NULL DEFAULT
'QUEUE_ONLY'` to the existing message table. Old messages inherit queue-only
semantics. Participant/session linkage uses existing JSON payload columns;
there is no destructive rewrite, new database, foreign key, or additional index.
The additive change follows StarRocks's documented
[ALTER TABLE syntax](https://docs.starrocks.io/docs/sql-reference/sql-statements/table_bucket_part_index/ALTER_TABLE/).

## Limitations and validation

Deterministic worker tests use the real control layer and shared assistant loop
with scripted providers and in-memory journal I/O. They cover nested delegation,
direct messages, follow-up reuse, steering, interruption, recovery before and
after consume checkpoints, and final synthesis. Browser tests exercise the real
components with mocked APIs. Live local UI testing additionally exercises the
configured provider, StarRocks, Redis, and workers: direct Sales and Smart return
the same 15 metric cells for the reported 2025 comparison. English paraphrasing,
non-data requests, replay, and narrow viewports are covered in the linked report.
Production deployment and a production cluster upgrade were not performed. A
team message board remains outside this direct-mailbox implementation.

See the [engineering report](benchmarks/nova-smart-mode-v2-2026-09-25.md) for commands,
results, acceptance evidence, and the legacy-assumption audit.
See the [live data parity report](benchmarks/nova-smart-data-parity-2026-09-25.md)
for the subsequent routing fixes and provider-backed UI evidence.
