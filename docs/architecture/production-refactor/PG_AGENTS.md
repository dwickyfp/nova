# Studio agent metadata port

Revision 0021 defines the 30 Agent-owned tables through `AgentMetadataRepository`
(`backend/app/modules/agents/metadata_repository.py`). Runtime callers now use
actor-bound PG transactions for the registry, run journal, Missions, automations,
memory, quality, sharing and Studio stores. The three retired Agent stores stay
as revision 0011 defined them ([retired history](PG_RETIRED_AGENT_HISTORY.md)).
The [delivery record](../../reports/production-refactor/importer-agent-cutover/README.md)
distinguishes tested ports from actual epoch activation; development processes
remain stopped pending final CI and cutover at this checkpoint.

## Workloads

Web owns interactive Agent requests; the standalone Agent process remains target
work. Automation and scheduled Task handlers use their own logins and a bound
actor (principal, one active role, login, security-context version and workload
identity). Revision 0034 grants Task handlers scoped learning/quality access;
0044 adds read-only custom-tool and selected release-manifest access so their
normal Agent verification can run. It preserves owner/shared-role RLS and grants
no definition writes. Columns the database owns (provenance,
timestamps, revisions, bound login and version) are not writable by any
workload; row guards set them.

| Tables | Read | Write |
| --- | --- | --- |
| Agents, role grants | owner, or a caller whose **active** role holds a grant | web, owner only |
| Skills (and parts), custom tools | owner, or a shared agent's run for the items that agent names | web, owner only |
| MCP servers, tools | owner | web, owner only |
| Versions, release manifests | owner; a manifest also to readers of the agent pointing at it | web, insert only |
| Automations | owner (web); the automation worker's SERVICE actor | web owner in the stored role (definition); automation worker (run bookkeeping only) |
| Shares | owner, target user, active target role | web owner |
| Runs, events | owner | owner in the run's role under the current claim; web deletes thread history |
| Missions, deliverables | owner in the Mission role | web, agent |
| Delegated resources and grants | owner in role scope | none (no current writer; import only) |
| Artifacts | owner | web, agent; web deletes |
| Legacy tile dashboards | owner | none; web deletes after verified conversion |
| Deep research, candidates, rule proposals | owner (candidate also its proposer; proposals in role scope) | web, agent; decisions by web |
| Memory, revisions, evidence | owner in role scope | web, agent, automation |
| Quality revision stores (5) | principal and active role | web, insert only |

Nothing in this domain stores a credential. The repository refuses
credential-shaped text before it reaches the database without repeating the
value, and the database refuses an MCP endpoint carrying userinfo. Guard and
policy errors use fixed messages.

## Rules the new store adds (Wave D and F)

- **Definition CAS.** An agent changes only from the `config_revision` the
  caller read; its configuration snapshots are written in the same
  transaction, so a failed CAS leaves no orphan snapshot. Skills, MCP servers,
  tools, custom tools, role verification, automations, artifacts and research
  runs carry a `metadata_revision` that advances by exactly one. Two
  concurrent edits from one revision give one success and one conflict.
- **Immutable snapshots.** Versions and release manifests are insert-only; the
  same ID with different content is a conflict. JSON that callers fingerprint
  is stored as text, byte for byte. A manifest needs its saved configuration.
- **Run journal.** A run starts with claim generation 1, bound to the starting
  login and security version. An event batch and the run's high-water mark
  commit together (a deferred check refuses events above the mark at commit,
  and the mark only names a stored event). Events are appended above the mark,
  carry the exact SSE frame whose embedded sequence must match, and are never
  updated. A resend of identical frames is accepted; the same sequence with
  different content is a conflict. A takeover or interruption advances the
  generation and rebinds the run, so an older producer can no longer append,
  heartbeat or finish; a newer security version cannot write under an old
  claim. Terminal runs are frozen. A role change marks the run so it can no
  longer be replayed or claimed.
- **Missing history.** Imported runs keep their high-water mark even when some
  or all events were never written. Replay returns the stored frames and
  reports `complete=false` instead of waiting or inventing events.
- **Missions.** A Mission changes only from its revision and is always written
  by the current login and security version; an older Mission must be resumed
  explicitly. A deliverable is recorded once per request digest; a retry with
  the same payload returns the receipt, a different payload conflicts.
- **Automations.** The due scan belongs to the automation worker's SERVICE
  actor. A claim compares the generation the scan saw, so exactly one worker
  wins; only that generation can record the run and advance the schedule. The
  worker cannot change a definition, and the owner's definition change cannot
  touch run bookkeeping. Retired Smart/Auto automations cannot be enabled.
- **Revision stores.** Quality records and memory revisions append the next
  revision above every retained one; a runtime fork, a stale revision or an ID
  from another principal/role scope is refused (a narrow SECURITY DEFINER head
  check reads only the maximum revision and whether another scope owns the ID).
  Imported same-revision forks are kept and readers refuse them until
  reconciled. A memory projection changes only with its revision row.
- **Deduplication.** A pending or adopted verified-query plan is unique per
  agent and View in the transaction; a candidate and a rule proposal are
  decided once, by the agent owner, and the reviewer is recorded.

Definition, grant, skill, MCP/tool, release-manifest, automation-definition
and share changes, and candidate/proposal decisions, write a success audit row
in the same transaction. Runs, events, Missions, knowledge and bookkeeping
(MCP status, automation run state) are history and are not audited.

## Findings for the caller port

- **Durable consent** is not stored here; Wave D/R39–R41 require its own
  principal/login/role/version-bound decision and consumption record.
- **Shares:** the prepared facade and PG RLS use the active role plus direct
  user grants. The API checks ownership before granting. Revisions 0029/0030
  expose fixed conversation/dashboard projections under the viewer identity;
  a dashboard projection contains only charts named by its stored layout.
  Client edits to a layout cannot widen that membership. Direct cross-owner
  SELECT remains refused, and queries run through the viewer's governed session.
- **Shared domain memory** and remaining learning scans still need narrow
  cross-owner projections. Usage/session observability now uses PG with the
  bound owner; usage totals and series cover the same UTC calendar-day window.
- **Artifact conversion:** runtime no longer changes schema or scans another
  owner's artifacts. The CLI discovers owner names through a maintenance-only
  projection, then explicitly authorizes delegation and binds each owner's
  identity for conversion. Historical tile readers use PG and propagate outages.
  Superset effect reconciliation and the legacy import/type transformations
  still need full rehearsal; storage tests do not prove those external effects.
- **Shared-agent MCP tools:** owner-only. If a shared agent must call the
  owner's MCP tools, add a reference-bound read like skills and custom tools.
- **Run visibility** is owner-wide because thread deletion spans roles; replay
  and claims filter the active role in the repository and through the
  role-change marker.
- **Timestamps:** Agent, assistant, artifact and run writers used Python
  UTC-naive values; quality/memory revision rows used engine `NOW()`. The
  implemented importer converts each by its reviewed producer. The operator
  confirmed Asia/Jakarta for historical engine timestamps; UTC writers stay UTC.
- **Heartbeat cadence:** every heartbeat is a row update; batching or a Redis
  liveness signal may be preferable at cutover.
- **Delegated resources** have no current writer and are import-only; a future
  writer needs its own grant and guard.

## Remaining agent work

- Activate the verified PG epoch and validate normal runtime ownership.
- Complete standalone interactive Agent execution and durable consent;
  Redis admission and process-local consent currently require session affinity.
- Complete external effect recovery and whole-feature governed/live AI QA.
  Interrupted/resumed imports have passed; they do not prove these runtime gates.

Validation is recorded in
[the Agent metadata checkpoint](../../reports/production-refactor/agents-metadata/README.md).
