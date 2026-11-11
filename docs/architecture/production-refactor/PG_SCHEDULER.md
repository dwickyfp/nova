# PostgreSQL scheduler leadership and occurrences

Revision 0006 and `task_orchestration/metadata_scheduler.py` add leadership and
due-occurrence storage. They do not switch the running Task scheduler, import
graph history or execute a Task body. The existing cron, interval, stream-WHEN,
overlap, DAG and finalizer behavior still needs its complete PG runtime port.

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
transaction. The owning Task service must insert the matching immutable graph
definition and graph-run record in that same transaction when its port is wired.
Those domain tables and their final foreign keys are not supplied by this
storage checkpoint. A failed domain write rolls the reservation and wakeup back.

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

The focused real-PG suite passes fifteen cases, including concurrent leadership,
token reuse, duplicate due times, timezone normalization, definition drift,
future/expired refusal, transaction rollback, actor isolation and late-commit
refusal. Its initial failure exposed the shared-lock policy issue described
above. The combined PG suite passes 179 cases without skips. Full unit validation
passes 10,705 cases with 65% combined coverage; evidence is recorded under
[WP-08](../../reports/production-refactor/WP-08/README.md).
