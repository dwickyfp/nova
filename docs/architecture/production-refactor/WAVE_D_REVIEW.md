# Assistant, Studio and Notebook repository boundaries

The repository calls classified here address Nova metadata only. This review
does not certify each route, delegation or runtime, and does not close WP00.

## Studio configuration and artifacts

`AgentRepository` owns agent configuration, role grants, skills/body chunks,
MCP registrations, tool definitions, historical semantic models, verified
queries and semantic usage. Its `_read_rows` helper receives only metadata
SELECTs at the six current call sites. Shared-agent reads deliberately cross
the owner filter after checking a named sharing role; owner-only RLS would
break this supported path. The target policy must enforce the same verified
sharing contract without exposing private owner resources or credentials.

Dynamic update columns and schema additions use fixed allowlists. The schema
loop also initializes fixed version/release/quality tables; runtime DDL must
move into checked migrations. The legacy skill-author migration updates only
assistant threads/messages after checking audit provenance. It is a migration
operation, not normal application authority. Preserve old semantic binding IDs
and distinguish null bindings from an explicit empty array.

Agent updates already use the prior `config_revision` in the UPDATE predicate.
They write prior/new version snapshots before that CAS, so a failed CAS can
leave unpublished snapshots. A PG unit of work must commit the snapshots and
pointer together while preserving version IDs and evaluated release manifests.
Role replacement, MCP/tool deletion and skill/body-chunk publication similarly
need one metadata transaction. MCP command, args and endpoint remain inert
configuration until the authorized sandbox path executes them.

`ArtifactRepository` targets `CONFIG_STUDIO_ARTIFACTS` only, including its
DESCRIBE/ALTER compatibility paths. Its SQL is stored provenance, not executed
through this repository. Preserve artifact IDs, ownership and Superset object
pointers; legacy chart/viz columns cannot be dropped until verified conversion.
The timestamp-based update predicate must become a revision CAS without losing
the existing conflict response. Superset effects need the publication receipt
protocol; PG cannot commit Superset's database.

## Conversations and run events

`AssistantRepository` targets the thread, message and UI-state tables. Dynamic
list/count/message queries retain fixed table names and owner predicates.
Learning scans intentionally span consenting threads and therefore require a
separate service policy, rather than weakening end-user RLS.

Message sequence allocation currently reads COUNT then inserts separately.
That does not prove uniqueness under concurrent appends. Thread deletion spans
three deletes, and appending a message touches its thread separately. PG must
reserve sequence numbers atomically and publish related rows in one transaction.
UI state uses a Redis lease around read/check/write; keep its program hash and
role scope while replacing that lease with a revision predicate. Consent is
not a message flag: durable consent needs its own bounded, principal-bound
decision/consumption record and replay tests.

`AgentRunJournal` and `run_coordination.update_run` address only run/event
metadata. The latter accepts SQL as its second argument; all five callers in
`run_journal.py` update the fixed run table. These calls need explicit review
even though the scanner's pass-through heuristic recognizes first-argument
SQL wrappers. The terminal system-adapter call is inventoried.

Event insertion and updating `last_sequence` currently commit separately.
Redis coordination around row updates does not fence event insertion. PG must
publish event batches and their high-water mark atomically, preserve exact SSE
frames/sequences, reject a reused sequence with conflicting content, and bind
claims to principal, role, security version and generation. An interrupted
producer is not proof that its tool effects failed.

Agent configuration, assistant, artifact and run writers use Python UTC with
the timezone removed before persistence. Their import rule is UTC-naive, not
the engine-session conversion used by `NOW()` writers. Preserve stored legacy
values and record any mixed-writer exceptions before applying conversion.

## Notebook persistence

The seven `NotebookRepository` system calls address runtime, execution and
package metadata. Output bytes remain in managed object storage; notebook
documents remain owned by WorkspaceService. Runtime/execution timestamps are
epoch inputs converted by engine `FROM_UNIXTIME`, while package timestamps use
engine `NOW()`. Import must account for that original session timezone.

The current live package table has PRIMARY KEY(notebook_id), although reads,
writes and runtime restore use `(notebook_id, user_name)`. Two users of a shared
notebook can overwrite each other's requested package manifest. The target
key must include the principal, with a regression that writes both manifests
and restores each runtime independently. Import can preserve the surviving
source row; it cannot reconstruct an already overwritten manifest.

Queued/running execution intent and runtime generation currently reside in
Redis; durable execution rows are written at completion. The new controller
must persist intent before wakeup, fence completion by runtime generation,
retain object-output receipts and verify cleanup after worker loss. Preserve
per-notebook/per-person runtime scope and do not convert a shared workspace's
namespace into a shared runtime identity.
