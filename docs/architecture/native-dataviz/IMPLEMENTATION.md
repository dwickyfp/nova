# Native Dataviz implementation record

The approved target is the supplied [production specification](source/NOVA_NATIVE_DATAVIZ_PRODUCTION_SPEC.md).
This record distinguishes the prepared implementation from the active product.
Native chart/dashboard HTTP routes are registered through the shared document
service. Table/view dataset authoring is active through the same API. SQL datasets
(including declared, non-sensitive named parameters), published Semantic View datasets,
bounded row previews and governed aggregate queries (`viz/aggregate.py`, routed to the
query workload) are active. The native editor (`frontend/src/features/native-dataviz/`)
is the only Dataviz engine and also opens from Studio Artifacts. Superset was
removed on 2026-11-16 by the user's decision, without converting its assets
(see the [status record](../../reports/native-dataviz/STATUS.md)). No production
cutover, migration completion, or release is claimed.

## Baseline and authority

Work started from `origin/main` at `1ee12694e0ecb6ecfd8135965943be08f5195967`
on `feat/dataviz-native`. The original checkout was clean on `feat/qa-test`.
`main` was fast-forwarded from the remote before creating the feature branch.
Existing worktrees and development volumes were preserved.

The archived handoff and its fingerprints are in [source/](source/) and
[manifest.json](source/manifest.json). Instructions embedded in the archive describe
the requested target; they do not authorize unrelated effects or destructive
tests. The user's request and the repository's current contracts govern delivery.

The development baseline was schema **47**, with an active epoch. The handoff's
schema-45 observation is historical. After 38 isolated PG17 document/migrator
checks passed, the separate operator froze metadata, backed it up, applied
additive migration 0048, verified unchanged counts in 180 existing tables and
the preserved epoch, then reopened writes and restarted the original checkout.
Development was then upgraded to schema **49**, preserving its epoch and counts
in all 193 existing tables after a verified backup. Migrations 0048 and 0049 are
applied and immutable; corrections require a later migration. Migration 0050
was subsequently applied after 63 native/migrator PG17 tests passed, following
the same freeze, verified backup, count reconciliation and unfreeze procedure.
It is also immutable. See [schema-50 evidence](../../reports/native-dataviz/development-schema-50.json).
See the [upgrade evidence](../../reports/native-dataviz/development-schema-48.json).
The subsequent [schema-49 evidence](../../reports/native-dataviz/development-schema-49.json)
records backup verification, retained failed attempts and the unchanged epoch.

Migration 0049 extends workspace binding through the existing
`workspace_namespace_permission` owner. Personal documents use their authenticated
owner's private namespace; named workspaces retain a real foreign key through a
generated shared-workspace column. Both namespace columns are database-generated
and cannot be supplied or reassigned by application writers. A document grant
does not make another person's personal workspace accessible. This adds no
workspace registry or implicit shared workspace.

## Ownership and entrypoints

| Boundary | Current owner | Required integration |
| --- | --- | --- |
| Dataviz | `frontend/src/features/studio/dataviz/dataviz-workspace.tsx` | Native dashboard list and editor with Neva beside it; no embedded frame |
| Studio artifacts | `frontend/src/features/studio/studio-artifacts.tsx` | Same editor and stable document IDs |
| Add to Dataviz | `backend/app/modules/viz/studio_publish.py` | Capability check and publish through the native document service as the caller (Superset removed 2026-11-16) |
| Native definitions | `backend/app/modules/viz/contracts/` | Canonical v2 definitions; generated browser schemas |
| Control metadata | `backend/app/core/metadata_db.py` | Existing actor-bound PostgreSQL transaction |
| Workspace membership | `backend/app/modules/workspaces/metadata_sharing.py` | Existing assigned-role membership; separate artifact action ACL |
| Data execution | `backend/app/modules/query/service.py` | Caller identity and exactly one active role; StarRocks/Ranger enforcement |
| Formula language | `backend/app/modules/viz/calc/` and `compiler.py` | Extend the existing language, not a second parser |
| Neva | `backend/app/modules/assistant/` and `modules/agents/tools/` | Same command service and receipts, one selected agent |
| Payloads and long jobs | Existing storage and worker owners | Private verified export objects and durable execution intent |

The legacy Viz compute path still probes sample rows, limits results to 500, and
executes dashboard zones sequentially. Those paths have not been relabeled as
the native dataset/query engine. A searchable caller inventory is retained in
[source-references.csv](../../reports/native-dataviz/source-references.csv).

## Prepared document store

Migration 0048 uses one normalized `viz_resources` registry for dataset, chart,
and dashboard identities. This follows the appendix's explicitly permitted
normalized-registry option and provides real foreign keys for ACLs, revisions,
publication references, receipts, and events. Definitions remain in the existing
Viz domain. There is no second connection pool or metadata backend selector.

Each immutable revision contains a validated definition and canonical SHA-256.
`draft_revision` advances on content edits; `metadata_revision` orders all
committed events; `grants_revision` provides independent CAS for sharing changes.
Publications pin a document revision and transitive chart/dataset revisions.
Restoring a publication creates another draft revision.

An operation UUID is bound to the request digest, principal, active role, login,
and security-context version. The repository locks the operation identity and
resource, checks the expected revision, then writes the definition, receipt,
outbox entry, and redacted audit in the caller's transaction. Reusing an operation
with different intent conflicts. A lost commit response is resolved by the
receipt; the repository never retries an uncertain transaction automatically.

Dataset authoring stores a canonical authoring-intent digest rather than relying
only on the analyzed definition. Exact create/update retries check the persisted
receipt before source I/O, and check again after a source failure in case another
request committed meanwhile. Original caller, active role, login, context version
and current edit permission still apply. Definition-based legacy receipts retain
their original validation path and the existing receipt lookup endpoint.

The shared dashboard command accepts `datasets.replace` for revision-pinned data
resources. Creating a new dataset inside a dashboard uses
`POST /viz/dashboards/{id}/datasets`: source inspection happens outside PostgreSQL,
then a single actor-bound transaction inserts the dataset, its revision/receipt/
outbox and the dashboard's updated revision/receipt/outbox. Workspace creation and
dashboard edit permissions are checked before inspection and again before writing.
A parent failure or competing dashboard revision rolls back the new dataset.
The existing document repository and QueryService own both entrypoints; there is
no second persistence or execution engine. The browser transport is implemented;
the editor integration remains open.

Receipt lookup retains that complete identity even after the actor downgrades
their own grant to view. A new login, changed role/context version or another
editor cannot use the original receipt. Bounded keyset listings select either
authorized drafts or current publication titles; they never expose a newer
draft title to a viewer. History is editor-only. Revision reads use the exact
publication-reference RLS boundary. Event replay returns ordered invalidations
with a captured sequence ceiling, without definitions or query data.

Reference validation memoizes shared subgraphs and caps unique references,
depth and total closure entries. Cached paths are checked again for ancestor
cycles and depth. The digest check runs against the stored JSON before model
defaults are expanded, preserving older immutable snapshots when optional
fields are added. Parameter controls revalidate defaults against their declared
parameter so a sensitive default cannot be persisted through a dashboard.

All tables force RLS. Workload logins cannot assume the schema owner. Fixed
security-definer functions use `pg_catalog` search paths, actor validation, and
restricted grants. Web is the current command workload; query and agent logins
have read grants only. Neva's command admission/wiring remains pending and must
not be implemented by handing the agent owner credentials.

Deferred constraints reject a head transition without its receipt/event,
unrelated revisions attached to an edit, and later changes to publication
references. Grant replacement uses one statement snapshot so a manager can
remove their own access without a partially applied ACL.

Workspace membership and artifact actions are separate checks. Workspace
membership may use assigned roles according to the existing file contract.
Artifact role grants apply only to the active role. Neither grants data access.
Query, preview, filter-option, export, and assistant paths must still execute
under the viewer through QueryService; those native paths are not implemented yet.

The prepared document command service revalidates the original login, active
role and security-context version against the current session before each
metadata transaction. It carries no stored password into repository scope.
Sharing checks management before directory lookup, performs that lookup outside
the transaction, then rechecks the session and management when committing.
Refusal audits contain the actor, action and resource ID, without definitions
or parameter values. They also cover stale sessions and workspace lookup refusals
before a document transaction is opened. HTTP routes now call this service;
Neva tools remain pending.
Dataset authoring uses the governed source-validation integration described below;
published semantic sources use the same path. Parameterized SQL previews bind
typed values through the existing QueryService; sensitive parameters are refused.

HTTP mutation requests require UUID `Idempotency-Key` values and strong revision
`If-Match` values; command-body identifiers must agree with the headers. Body
reading is bounded by one MiB and ten seconds. Validation errors omit input
values, successful reads prohibit caching, and typed responses exclude internal
execution state. The native endpoint is registered by the normal application
factory and uses the existing authentication dependency and credential redactor.

The browser document adapter uses the shared API client and generated response
schemas. Command IDs remain supplied by the editor; it does not retry an unknown
commit or invent a replacement operation. Strong revision headers, response
identity checks and a captured security epoch protect pending edits. Role/login
changes abort requests and discard responses, including changes during response
decoding. Token renewal cannot overwrite a newer login. Neither editor surface
is wired to this adapter yet.

Migration 0050 prepares exact revision dependency edges derived from immutable
definitions. Reverse graph traversal protects transitive draft pins and retained
publications. Reference creation locks the target against concurrent deletion.
Only the resource owner can tombstone a document; revisions and receipts are
retained indefinitely, with no application purge privilege. A deleted document's
receipt remains limited to its original caller/login/role/context. These deletion
contracts passed the native PG17 suite, including both delete/reference race
orders, shared-chart dependencies and a schema-49 upgrade with existing data.
The definer lock projection allows row locking but rejects updated rows under
that identity. HTTP reads and replay continue through the caller's forced RLS.

## Specification resolutions

* The work-package table contains WP00 through WP17: **18** packages, despite
  references to 17 packages elsewhere. All 18 are tracked.
* The permission table and prose disagree about comment implying interact.
  The explicit permission prose is used: comment implies view only; edit implies
  view/interact/comment; publish additionally requires edit; manage implies all
  document actions except export; export is separate. The owner has all actions.
* Schema 48 follows the observed schema-47 baseline, not the historical 45.
* v1 conversion never rounds 24-column positions. Exact even coordinates convert
  to 12 columns; unsupported layouts/filters/calculations become explicit blockers.
  The initial adapters are intentionally incomplete migration coverage.
* Decision 2026-11-17 (NDV-DATA-014, under the owner's close-everything
  authorization): dashboard-local metric views and their promotion are not
  part of this release. Governed metrics ship through published Semantic Views,
  which already have authoring, review, pinned revisions and native execution.
  A local view has no execution plan: the query path refuses any dataset with
  metrics ("needs a semantic or parameter execution plan"), and no editor
  creates one. Promoting a local metric onto a base dataset would add a metric
  to that dataset and make every chart on it unanswerable. Shipping promotion
  first needs a metric formula compiler in `sql_frontend/` with additivity
  rules. That is a separate work package, not a missing test. The contract
  keeps `dashboard_local_metric_view` so stored references stay valid. The
  dependency graph still pins and authorizes its base dataset and refuses
  cycles.

## Dataset integration investigation

The existing `StarRocksCatalogProvider` already reads column type, ordinal and
nullability with the caller's role through `QueryRepository`. Native source
binding must extend this owner. The catalog's descriptive securable IDs are
derived from names and deliberately revive after a drop/recreate; they cannot
serve as the dataset's physical source identity. The pinned engine exposes a
table ID in `information_schema.tables_config`. Inspection of the
[pinned source](https://github.com/StarRocks/starrocks/blob/4a9848edf03f5c936dac664b2d52527f48e72eb0/fe/fe-core/src/main/java/com/starrocks/service/InformationSchemaDataSource.java#L238)
shows that the ordinary view branch does not populate the ID; it is assigned in
the OLAP/materialized-view helper. View identity therefore needs an engine-owned
solution and governed runtime evidence, rather than a name-derived fallback.
The new one-line FE patch assigns the existing view object's ID after the
unchanged authorization check. It compiled with the existing patch set and is
now applied to the development FE; source identity acceptance is recorded
separately from image build and health checks.

`StarRocksCatalogProvider.get_source_schema` now returns the engine identity,
catalog column schema and a deterministic schema digest. It reads identity
before and after column metadata, refusing a replacement during observation.
Nullability, ordering and unique column names must be authoritative. External
connectors without verified physical identities fail explicitly. This is not a
DDL lock: dataset save/execution must revalidate the stored identity and digest.
The authoring and execution consumers remain to be implemented.
Live acceptance now verifies a stable ID across table rename and a different ID
after view drop/recreate. The pinned grammar has no `ALTER VIEW ... RENAME`;
that unsupported operation is not claimed. The shared type parser handles
catalog integer display widths such as `bigint(20)` without changing the type's
64-bit representation. Empty-source decimal precision/scale and nullability
were checked against actual catalog metadata, with no sampled rows.

Metadata reflection remains governed by the existing BI access policy owner.
Its permission is separate from table or view access; a table grant does not
grant view access. The live fixture used temporary, column-scoped reflection
policies and separate view permissions, retaining all pre-existing QA grants
and Ranger attributes. Denied, inactive, revoked, zero-role and multi-role
requests did not obtain source identity. No service credential fallback exists.

The current `QueryResult.column_types` contains protocol type and the driver's
precision/scale slots; it does not expose structured nullability or prove that
display lengths are logical decimal precision. The existing relation binder
has explicit unsupported cases, including set operations and some joins. Dataset
schema work must extend these shared owners without inferring types from rows.

Named value parameters now use the central grammar, a registered typed query
AST and the existing native planner, QueryService lifecycle and authenticated
driver. The grammar reuses the colon and identifier tokens so map entries and
array slices retain their syntax. Immutable declarations travel in the plan;
values remain in the private execution context. After session-function rewrites,
AST source spans become driver placeholders. The driver quotes the values.
Lists are accepted only as a sole `IN`/`NOT IN` operand. Parameters cannot name
tables, roles, sort expressions or other SQL structure.

Execution rejects unbound/unused names, DML, multiple statements, EXPLAIN,
session variables, query hints, stages, external table functions and unapproved
functions. Bounds are 32 declarations, 256 references, 500 nonempty list items,
4,096 expanded arguments and 1 MiB of values, with a 200 KB SQL limit. Strings
are UTF-8; integers are signed int64; decimal inputs retain up to 38 digits;
timestamps require an offset and are converted to Nova's session timezone before
driver encoding. Parameterized projections require aliases to prevent the engine
from putting input values in generated column labels. Result rows still represent
the caller-authorized query output.

Bound queries use managed sessions and require a result cap of at most
100,000 rows. They cannot borrow a proxy transaction. The existing row-sink path
now accepts bound queries, preserves native values and refuses a stream exceeding
its declared row cap before delivering the excess row. Refusal and cancellation
close the connection without draining the unbounded remainder. Other existing
stream consumers retain their own bounds. Template lowering runs in the existing SQL CPU worker. Each
managed session disables query profiles before executing bound SQL and refuses
execution if that setting fails. Engine errors retain their code with a generic message. Nova
audit/history record symbolic SQL, and Autopilot receives neither values nor a
replayable sample. These guarantees were exercised through the real QueryService
under existing QA identities, including Ranger row filters and masks.

This is an execution foundation, not completed dataset authoring. The SQL dataset service currently rejects named inputs. Parameter controls and
typed dataset execution still need to call this binding foundation.
The current text-protocol driver transmits quoted literals to StarRocks;
engine audit/profile redaction has not been established. Sensitive parameter
execution must not be released until that separate boundary is proven.

### Object dataset authoring

`POST /api/v1/viz/datasets/inspect` now resolves a table/view and selected
columns through the existing caller-bound catalog provider. It derives field
UUIDs from the dataset identity, engine object ID and column name. It uses the
engine analyzer's physical types and nullability through `EXPLAIN SCHEMA` on
a zero-row projection. QueryService verifies SELECT authorization independently
of catalog reflection. Analyzer metadata distinguishes BOOLEAN from the
catalog's `tinyint(1)` representation. The schema digest includes both the
catalog fingerprint and the ordered analyzed output types/nullability.
The engine identity/schema and the login security context are checked again
before returning metadata. Inspection is bounded to 30 seconds and 512 fields;
it neither returns nor samples data rows. Unsupported physical types are refused.

`POST /api/v1/viz/datasets` requires the inspected physical ID and schema digest,
resolves both again, and derives the complete persisted definition on the server.
Caller-supplied field schemas and authority fields are rejected. External reads
finish before the existing actor-bound document transaction creates the revision,
receipt and event. Dataset listing, publication, ACL, history and deletion use
the same resource engine as charts/dashboards. The browser document client uses
the same auth-epoch cancellation and typed response boundary for these operations.

Catalog reflection remains a governed permission. A QA role without reflection
was refused in the first live attempt. A subsequent isolated source fixture
verified a temporary explicitly authorized role, an empty view, exact decimal
schema, source binding, replay, publication and personal isolation. The fixture
preserved existing QA grants/attributes and removed its own roles/database;
the created dataset was soft-deleted through the normal API. This proves the
API path, not the Data tab or dataset repair. SQL authoring and row preview have
their own evidence below, as does published semantic-source authoring.
DDL observation is still not a lock across engines: every future data execution
must revalidate the saved identity and schema and refuse stale bindings.


### SQL dataset authoring

The same inspection/create routes accept a SQL source. The central SQL frontend
requires one composable read query, resolves scoped CTE references and rejects
side effects, dynamic identifiers, unsupported external sources and unbound
inputs. Every physical table/view dependency is resolved under the caller's
active role before schema planning. Source identity/schema and the session are
rechecked afterward. Output names must be unambiguous.

The patched FE provides `EXPLAIN SCHEMA` through normal analysis, authorization
and planning. The first implementation read physical expressions; live join
checks showed that their nullability changes with optimizer rewrites. The
corrected patch reads analyzed output fields, with a separate compiled/live
gate recorded in the delivery evidence. The corrected image and live HTTP
checks passed, including stable schema before and after fixture data was inserted. It never runs the result rows to guess
types. Nova's parser gives this statement its own typed AST and refuses nested,
mutating, external-effect and unsupported forms before execution.

Saved SQL definitions contain the validated query token digest, physical
source dependencies and authoritative output schema. Comments, whitespace and
a trailing terminator do not change field identity. A schema digest binds the
query, source IDs/schemas and output columns. Save re-inspects before opening
PG and rejects a stale digest. The shared document engine owns revisions,
permissions, publication, receipts and deletion for SQL datasets too.

Declared named parameters are inspected with typed NULL casts (no input values),
stored with the dataset and bound as typed driver arguments at preview. Inspection
refuses undeclared names; changing a declaration changes the schema fingerprint.
Governed HTTP evidence: `parameter-dataset-http-attempt-6.json` and `-7.json`.
Local metric views and the mounted Data-tab editor remain unfinished; the SQL path
is not a completed work package or release.

### Explicit dataset source updates

`PATCH /api/v1/viz/datasets/{id}/draft` takes a reviewed table/view/SQL selection,
its inspection fingerprint and source identity where applicable. The same
`If-Match` and idempotency receipt rules apply. A short actor-bound read requires
edit permission before any source I/O. Re-inspection runs outside PG, then the
existing document repository checks current permission and revision CAS and
atomically writes the new revision, audit, receipt and outbox event. The route
does not accept client-provided field definitions or change workspace ownership.

This provides explicit repair for replaced sources: the old revision remains
stale and immutable, the new revision binds the reviewed physical identity and
new field IDs, and chart/publication pins do not change automatically. Real
view drop/recreation, refused old previews, edit replay/conflicts and unchanged
publication were verified through the normal gateway. Published semantic selections
can also be edited explicitly. A parameterized SQL dataset can be edited only with
another SQL selection and declarations. Source edits with local metrics or
relationships remain refused until their binding migration is implemented.

### Bounded dataset previews

`POST /api/v1/viz/datasets/{id}/preview` accepts a selected revision, draft or
publication scope, page size and offset. The shared document repository checks
workspace membership, ACL and revision visibility. Drafts require edit access.
No caller-provided SQL, schema, actor or credential is accepted by this route.
Source inspection verifies the saved source ID, schema digest and fields before
and after query execution. A final short metadata read rejects intervening
revision, publication or grant changes. No engine I/O occurs inside a PG
transaction, and no result rows are persisted in a document or receipt.

Vite and the deployment gateway route inspection and preview to the existing
query tier. Dataset creation and document mutation remain on web. Both source
operations use the shared query admission owner, capped at four active Dataviz
requests per process, and a 30-second deadline. This bounds local admission;
it does not establish distributed fairness, replica capacity or production load.

The central SQL frontend constructs the bounded projection, preserves the inner
SQL query's grouping/limit, quotes catalog-bound names and orders preview rows
by all output fields. Each page is a new query, not a repeatable-read snapshot.
At most 201 rows cross the stream for a 200-row page, including a lookahead;
offsets stop at 10,000. Text projections are bounded to 4,097 characters before
transmission, then the sink refuses values over 4,096 rather than silently
truncating them. The query repository streams one row at a time for this sink
and closes interrupted/refused connections before cursor cleanup can drain.
The shared default stream batch remains unchanged for other consumers.

The response budget is one MiB including typed field metadata. Integers and
decimals use exact decimal strings, finite FLOAT/DOUBLE values use JSON binary64
numbers without conversion to decimal, Boolean and date values are explicit, and
timestamps retain microseconds and the declared timezone offset. Ambiguous or
nonexistent local DST times, mismatched types and unsupported values fail with
redacted errors. The lookahead determines `has_more`; no fabricated count is
returned. Published semantic selections use the same bounded preview path.
Parameterized plans remain unsupported until their complete execution and
sensitive-value boundaries are implemented.

Live FLOAT/DOUBLE transport retained binary fractions, the smallest positive
binary64 subnormal and the largest finite binary64 value. A separate pinned-FE
numeric-literal boundary remains open: unquoted very small scientific literals became zero during
analysis, while an explicit string-to-DOUBLE cast retained the same value. The
transport fixture uses the explicit cast to establish its stored input, not a
production SQL rewrite. Both the failed literal attempt and the direct engine
comparison are retained. This is not evidence of general numeric SQL correctness.

### Paged aggregate queries

`DatasetQueryRequest` accepts an optional `offset` (strict integer, 0 to
10,000, default 0) beside `limit` (1 to 10,000). Unknown fields, including any
cursor, remain refused. The aggregate planner keeps its total order: explicit
sort keys, then every dimension ascending. Grouping makes the dimensions unique
per row, so pages are disjoint and complete for an unchanged source. The planner
appends `LIMIT limit+1 OFFSET offset`; the lookahead row sets `truncated`, and
`DatasetQueryResult` echoes `offset` and returns `next_offset`, or null when no
further page exists or it would pass 10,000. At most 10,001 rows and 4 MiB cross
the stream per page, and the engine's ordered window is at most 20,001 rows.
Each page is a new query, not a repeatable-read snapshot: a data load between
pages can shift rows. Keyset paging was not used because nullable, mixed-direction
and date-grain sort keys would need typed seek predicates in the planner.

A page uses the same admission, pinned revision, caller session and active
role, source identity checks before and after execution (`SCHEMA_STALE`) and
error mapping as any dataset query. The browser Table visual requests one page
per `page_size`, bounded by the chart's `row_limit`, keyed by offset and viewer
sort so pages cache. A grand total on a multi-page table is a separate
measures-only query over all matching rows. Tables with table calculations and
all downloads still read the full `row_limit` result. Conditional-format ranges
on a paged table are computed from the page on screen.

### Studio draft charts

A chart in a Studio answer is a native `ChartDefinition` over a draft dataset
that is never stored as a document. The dataset id is
`uuid5(draft-namespace, thread + producing tool call)`. Revision 1 and the
field ids come from inspecting the answer's statement as the caller, the same
way SQL dataset authoring does. `NativeDatasetService.query_statement` serves
`POST /agents/studio/dataviz/drafts/{ref}/query`:

- It re-inspects the statement as the caller.
- It plans the aggregate over it with the shared planner.
- It executes the plan under the same admission, capacity, 30-second timeout,
  held engine session, 4 MiB sink and source fingerprint check (`SCHEMA_STALE`)
  as any dataset query.

The statement comes only from Nova's record of the caller's own message, never
from the client. `viz/studio_drafts.py` owns the signed reference, the owner,
role and fingerprint checks, and the per-query audit record. In the browser,
`NativeChart` takes a `draftRef` and reads through
`nativeDocuments().queryDraftChart`, with the same result checks and cache
keys per source.

A draft chart can be edited and saved from the answer
(`viz/studio_artifacts.py`, see
[Architecture 19](../../arch-19-studio-dataviz.md#chart-artifacts-and-the-save-suggestion)).
An edit validates the definition against the draft dataset's fields and adds
a revision to a role-bound per-answer log in `config_assistant_ui_state`; it
writes no document. A save is the person's explicit
`POST /agents/studio/dataviz/publish` with `draft_ref`: the dataset revision
from the governed statement, the latest chart revision rebound to that dataset
by source column and re-validated, and a `ChartWidget`, all through the native
document service as the caller with revision CAS. The model has no save tool.

### Next required source boundaries

Dashboard definitions now include an optional bounded `datasets` registry of
dataset IDs and pinned revisions. The existing dependency closure validates
these references even before chart widgets exist, using the same workspace,
RLS and immutable-revision checks. Drafts and retained publications keep their
deletion protection. Empty registries are omitted from serialization so existing
v2 definitions and operation digests remain byte-compatible after parsing.
Real PG17 tests cover hidden/cross-workspace references, version pinning, removal
from the current draft and protection by an earlier publication. A later live
draft-only test exposed a missing PostgreSQL dependency edge: the original
extractor recognizes `dataset_revision`, while this registry uses `revision`.
Migration 52 extends the extractor and backfills prior revisions without changing
their definitions or digests. It refuses activation if the dependency closure of
an active draft or retained publication contains a deleted resource. The PG17
regression reproduces the old failure, verifies upgrade and rollback, and the
governed gateway now refuses the draft-only deletion. Schema 52 is deployed with
the same epoch and unchanged unrelated table counts. The retained failure and
correction are [finding 1](../../reports/native-dataviz/dashboard-dataset-dependency-finding-1.json)
and [finding 2](../../reports/native-dataviz/dashboard-dataset-dependency-finding-2.json).

The shared React preview components use TanStack Table/Virtual and the existing
native document client. They preserve integer/decimal strings, virtualize both
axes, support cell keyboard navigation, reset on security/revision changes and
fetch rows only after an explicit preview action. Pagination uses the server's
observed next offset without a fabricated total. They are not yet mounted in
the product editor. Browser request cancellation is verified; server execution
cancellation on HTTP disconnect requires a separate lifecycle acceptance.

The shared Data tab now composes source selection, dataset creation, revision-pinned
metadata and explicit row previews. The table/view picker uses Explorer; published
Semantic Views use their existing registry and version fingerprint; SQL uses Nova's
locally bundled Monaco integration. The creation form retains one exact operation
body/key across an unknown save outcome and clears source/schema state when the
security epoch changes. A controlled React value round-trip lost characters during
one rapid-input browser test; the SQL draft's Monaco model now owns live text, while
change notifications update the inspect/save input. Failed attempts are retained.
The same Data tab can select an existing editable dataset from its workspace,
read the chosen immutable revision and append its reference through the document
operation service. It does not copy definitions or query rows. The library uses
bounded keyset pages, rejects cross-workspace or wrong-kind responses, disables
duplicate references, retains the original operation after an unknown outcome,
and requires reload after a concurrent edit. A fresh save preserves the reloaded
dashboard's other references. Role changes abort pending requests and discard
the selection and any late receipt.
The SQL source form also has a parameter declaration editor (name, type, list
values, required/default/NULL, decimal precision and scale). It validates names
before any request, sends declarations with inspection and saves the declarations
the analyzer returned; the saved preview offers typed inputs. Allowed values,
ranges and sensitive declarations are not editable in this UI yet.
These components are not yet mounted in the product shells; this does not complete
the Data editor, local-metric work or either native dashboard surface.

`GET /viz/{collection}/{id}?draft=true&revision=N` reads an exact immutable revision
under current edit permission. Revision selection is refused in publication scope;
missing revisions do not fall back to the latest draft. `GET
/viz/dashboards/{id}/datasets?revision=N` projects the ordered registry in one
bounded metadata query after checking the dashboard revision and edit access.
Child titles require current child edit access; unavailable entries retain only
the IDs and pins already present in the authorized dashboard definition. The API
does not return child SQL or fields in this index. Both use the existing actor-bound
repository and RLS, without a schema migration or a separate metadata owner.
Draft preview reads the requested immutable revision under current edit permission;
it must not substitute the latest head when a dashboard pins an older dataset.
Publication preview still selects the current authorized publication. Both verify
the source identity and permission before and after caller-bound data execution.

Published dataset HTTP responses now use an explicit `DatasetPublication`
projection containing field metadata, parameter declarations, metric labels and
grain. Source bindings/SQL, schema fingerprints and metric expressions are not
serialized, even for an owner reading the publication. The internal repository
retains the immutable definition for authorized execution; draft reads still
require edit permission. The browser refuses a full dataset definition on a
publication read. Focused wire/service and real governed API checks pass; PG
owner/viewer checks and final regression are recorded separately in the delivery
record. This projection does not prove engine log redaction for parameter values.
The live history canary confirmed generated SQL escaped the document projection
through query audit. Dataset operations now carry task-local provenance through
the existing QueryService, including nested Semantic View authorization queries.
The shared audit writer replaces source SQL/error text with resource, operation
and revision while preserving caller, role, status, timing and query correlation.
Query Autopilot does not sample private generated source, and managed dataset
connections disable engine profiling before execution. Dataset calls cannot
borrow another engine session. The governed rerun retains audit history without
the source expression. Prior audit rows are retained; this change is not an
audit-history deletion. Engine process lists, audit/dump output and sensitive
parameter values still require their separate redaction acceptance; no complete
source-confidentiality claim follows from this Nova history correction.

Semantic authoring now reuses `SemanticViewService.get_version_for_agent` and
`SemanticCompiler`, with the published view ID/version/fingerprint and current
caller checks. Typed selections name dimensions, metrics and declared filters.
The compiler owns joins, grain, fanout and multi-fact rules; the shared dataset
analyzer derives output types and binds physical dependencies. Field identities
include the model pin and physical dependencies, and numeric dimensions retain
their declared role. No model copy, generated SQL or invented Ontology release
ID is persisted in the semantic source. Model and source authorization is
rechecked around preview; a new active model version does not retarget an
existing dataset. Model deprecation or revoked access fails closed.

Migration 51 gives the query workload SELECT on visible published Semantic Views,
their published versions and the entity metadata used by the existing permission
checks. Forced RLS retains a bound actor and service identity, hides private
models from other users, and excludes drafts; there are no new write grants.
The live lifecycle fixture exercises explicit model publication, typed native
preview, pin stability after a newer model publication and deprecated-source
refusal. These checks do not establish native chart query, multi-fact dashboard
or local metric-view acceptance. Local metric sources now reference a pinned
base dataset in the document dependency graph; their authoring/execution remains
unfinished. Semantic charts must plan through the existing compiler, including
its additivity rules, rather than blindly reaggregate a preview result.

## Dashboard schema 3 (free canvas)

[ADR-001](ADR-001-free-canvas.md) records the decision. Dashboards are schema 3:
pages are canvases with integer pixel geometry, stacking order, `tab_order`,
`mobile` projection and authoring-only `canvas.grid` settings
(`contracts/dashboards.py`). The schema-2 models remain in
`contracts/dashboards_v2.py`; `canvas.py` upcasts them on every read without
rewriting immutable revisions, and `legacy_widgets` is the exact inverse used by
grid-speaking callers. Migration 0063 permits `schema_version` 3 for dashboards
and requires it to match the stored definition.

`operations.py` applies typed canvas changes (`elements.*`, `page.canvas.set`,
`page.tab_order.set`, `page.mobile.set`) and adapts the unchanged schema-2
changes, so stored receipts keep their digests and replay. The assistant tools
and Studio publish still send grid changes.

The editor (and Studio's artifact panel, which mounts the same editor) draws
pages with the frame renderer in `frontend/src/features/native-dataviz/dashboard/`:
`canvas.tsx` (page frame, absolutely positioned items, SVG shapes and
connectors, phone column, pointer move/resize) over the pure `frame.ts` (scale,
reading/tab/phone order, snapping, clamping, connector paths, token styles).
Moves and resizes commit one `elements.arrange` per gesture or key press; adding,
duplicating and replacing widgets still go through the grid-shaped changes.
GridStack is no longer a dependency.

Authoring (P2, part 1) adds a selection of several items, a marquee, moving
them together, smart snapping to other items' edges and centres with guides and
gap labels (`authoring.ts`), and editor commands (`commands.ts`) that each build
one operation: align, distribute, Auto-arrange, restack (`elements.reorder`),
`elements.group`/`elements.ungroup` (server ops that keep positions, stacking,
interactions, tab and phone order), lock/hide (`elements.update`), duplicate and
delete. Grid show/snap and page settings commit `page.canvas.set`. Undo records
the inverse of every canvas operation (`inverseAll` in `operations.ts`). A move
of items on the page translates their nodes each animation frame instead of
re-rendering them, so a drag costs the same with 6 or 60 items.

Active: the contract, upcaster, adapters, migration, the frame renderer and
P2 part 1. Tested: server golden, property and digest tests, the browser
upcaster against the same goldens, the PG17 upgrade from schema 62 (CI),
`frame.test.ts`, `authoring.test.ts`, `qa-authoring.test.tsx` (including the
60-item drag frame budget) and the editor browser suites. Open: P2 part 2
(selection pane with layer and tab order, creating text, shapes, icons,
backgrounds and connectors, keyboard-only scenario), P2 part 3 (images),
resolving data-point anchors and value bindings, and P3 Neva on canvas
operations.

## Dataviz assistant tools

`backend/app/modules/agents/tools/native_dataviz.py` gives the Studio Dataviz
assistant (and any Studio turn asked from a native dashboard) eleven tools in
the shared `AssistantLoop`: list, read, describe a dataset, aggregate query,
add charts, update a chart, arrange/remove widgets (applied as canvas changes), filters, create, attach a
dataset (existing or from a table), and publish. They call
`NativeDocumentService` and `NativeDatasetService` in-process with the turn's
caller; no service credential or HTTP hop is involved.

- Reads are `read_only`. Every change is `session_change` with consent, like the
  other Dataviz writes; `auto_read_only` never covers it.
- Each change uses the draft revision it read as `expected_revision`; a conflict
  is re-read and retried once. Operation ids derive from the tool call, so a
  retried call returns its existing receipt. A failure with an unknown outcome is
  resolved by `receipt` lookup and is never sent again.
- A committed change returns `dashboard_id` and the receipt's `new_revision`; the
  loop emits a `dashboard_changed` SSE event so the page reloads that revision.
- The turn context accepts `dataviz.native_dashboard` (`dashboard_id`, shown
  `revision`, `page_id`, `selected_widget_id`). The router reads that dashboard
  as the caller into the prompt, and for the Dataviz assistant routes governed
  data questions to its datasets.

Covered by `tests/unit/test_native_dataviz_assistant_tools.py` and
`tests/eval/test_native_dataviz_trajectory.py` with faked services. No live
model, PG17 or browser run has exercised these tools yet.

## Failure and recovery boundaries

Invalid definitions fail before persistence. Revision races return the existing
metadata conflict error. Unauthorized or expired role grants fail closed.
Database failures use the existing redacted metadata exception mapping.
Transactions cancelled before commit roll back definitions, events, and audits.
Redis is not required to prove a committed operation; realtime transport and
replay admission still need implementation. No S3 effect occurs in this store.

The development Superset inventory contains 7 dashboards, 52 charts, 22 datasets,
44 metrics, and 227 columns. Private definitions were captured outside Git with
restricted permissions; the [sanitized summary](../../reports/native-dataviz/superset-inventory-summary.json)
contains counts and hashes. Inventory is not conversion or semantic parity proof.

## Validation and release status

The [delivery record](../../reports/native-dataviz/STATUS.md) and
[254-scenario matrix](../../reports/native-dataviz/QA_MATRIX.csv) track evidence.
Focused contract and PG tests do not establish browser flows, live Ranger
behavior, migration equivalence, resilience, or performance SLOs.
