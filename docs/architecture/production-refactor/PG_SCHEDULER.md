# PostgreSQL scheduler leadership and occurrences

Revision 0006 introduced leadership and due-occurrence storage. Task metadata
and job links now use the same PG authority. The standalone process factory
passes one `MetadataLeaderLock` to both its polling service and `SchedulerTick`;
reservations use that live lease. Runtime and migration evidence is recorded in
the [delivery report](../../reports/production-refactor/importer-agent-cutover/README.md).
This source wiring does not establish whole-feature or replica acceptance.

The scheduler login acquires a named lease with a holder UUID, database deadline
and increasing token. Acquisition is atomic; renew and release require the live
holder and token. Releasing a lease retains the row and token, so reusing a
process UUID cannot revive an older lease. Runtime roles cannot delete the row.
Lease durations are bounded to five minutes.

Leadership mutation requires a SERVICE actor. When reserving an occurrence, the
same scheduler login uses the original graph owner's principal, role and
security-context version in the domain transaction. This preserves the caller
context in the queued work. That transaction may take a shared lock on the
leadership row, but its USER actor cannot acquire, renew or release leadership.
The database UPDATE policy permits the lock and separately checks the SERVICE
requirement for a write.

An occurrence is unique by `(root_task_id, definition_version, scheduled_at)`.
The scheduled timestamp must be timezone-aware and already due according to PG.
Equivalent UTC instants deduplicate even if clients use different timezone
representations. The queue idempotency UUID is derived from the same tuple;
request randomness does not change it. A different definition version has its
own occurrence, while a changed digest or graph-run ID under the old version
returns a conflict. Existing root and graph-run IDs are accepted unchanged.

The repository reserves an occurrence, orchestration job and outbox event in one
transaction. `TaskOrchestrationRepository.create_scheduled_graph_run` inserts
the graph run with its durable job link in that transaction. A failed domain
write rolls the reservation and wakeup back.

Both an immediate and a deferred trigger validate the leader holder, token and
deadline. The shared row lock prevents takeover while the transaction is open;
the deferred check also refuses commit if the lease expires during that
transaction. An old leader therefore cannot publish after its deadline merely
because it obtained a lock earlier. Repeated delivery after a successful commit
returns the original reservation without creating another job or wakeup.

Scheduled jobs use the internal `task.graph` orchestration kind. This job may
update graph state and create separately fenced node jobs, but it must not run
SQL, Python or another external mutation itself. The graph runtime owns WHEN
evaluation, overlap policy and node admission. A false WHEN must leave Stream
offsets unchanged; an error must remain an error. This storage layer does not
evaluate predicates or claim those runtime acceptance cases passed.

Occurrence rows are append-only. Web callers see only their exact principal,
active role and security version. Task and scheduler SERVICE workers may read
occurrences for orchestration. Other workloads receive no table grant. The
[PG ownership matrix](PG_STORAGE_OWNERSHIP.csv) records these boundaries.

Revision 0042 permits the web login to read only `name` and `expires_at` for
`task.scheduler`. Forced RLS requires a valid USER actor with an administrator
role accepted by the monitoring API. Other lease names, holder IDs, tokens and
mutations remain unavailable to web. The monitor reports an unreadable PG
signal as unknown, independently of Redis availability.

The production-factory PG regression exercises two competing schedulers,
idempotent polling, a committed graph/job/outbox, and leadership handover.
Separate PG cases cover lease expiry, late commit refusal, actor isolation and
the monitoring projection. See the current delivery report for final results.
The original fifteen-case storage checkpoint and its broader test counts remain
historical evidence under [WP-08](../../reports/production-refactor/WP-08/README.md).
