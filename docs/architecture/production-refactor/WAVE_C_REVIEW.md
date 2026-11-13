# Entity, Feature Store and AI Search persistence

This review separates control metadata from analytical projections. It does not
prove the PG port, authorize a cutover or waive the security tests. The owner's
embedding-test waiver does not waive authorization or lexical-search behavior.

## Ownership and constraints

`EntityRegistry` owns `CONFIG_ENTITIES`; all five system calls address that
table. Source-access probes use the caller's query path. Preserve opaque IDs and
the catalog/database/schema/name namespace, including the empty schema value.
Entity creation and catalog relinking currently commit separately. Their PG
replacement needs transactional metadata plus durable relink intent.

Feature Store owns six `CONFIG_FEATURE_*` control tables. Keep view/group names,
IDs, pinned versions, JSON definitions, generated SQL and training/model lineage.
The six-table bootstrap loop is metadata DDL, not an analytical-table writer.
Before adding foreign keys, profile entity references, active versions, group
members embedded in JSON, training sets and ML links. Group deletion currently
removes definitions while training lineage can remain; importing history must
not cascade away that evidence.

Search owns `CONFIG_AI_SEARCH_INDEXES`, `CONFIG_AI_SEARCH_VERSIONS`,
`CONFIG_AI_SEARCH_SYNC_STATE`, `AI_SEARCH_QUERY_LOG` and `AI_SEARCH_EVAL_RUNS`.
The query log uses Duplicate Key storage and requires lossless import identity.
Retain the lexical-only representation (`model_id` null, zero dimensions), model
revision, source watermark, exact build states and per-version identity. Source
reconciliation fingerprints are hashes, not a consistent source snapshot or a
durable external-commit receipt.

Metadata timestamps in these writers use engine `NOW()`. They require the
observed source timezone during import. Feature event timestamps are user data
and follow a separate contract; do not apply a metadata conversion to them.

## Engine data remains outside PG

`FeatureStoreService._materialize` creates `_NOVA_FEATURES` and versioned `FV_*`
tables, runs CTAS from a configured source and counts the result. `drop_view`
removes those physical versions. These are engine operations. The metadata
updates around them become PG jobs and receipts; translating the whole method
to PG would move user data into the wrong store.

`SearchService._source_fingerprint` and `_build_locked` read configured source
rows. The build also reads prior `_NOVA_AI_SEARCH.IDX_*` projections and copies
or inserts derived content, metadata and vectors into a new projection. Query
retrieves projection candidates before checking each candidate through the
caller's source query. `StarRocksVectorBackend` only observes FE capabilities or
creates, drops, searches and explains these analytical projections; none of its
seven system calls belongs in PG.

The source reads and projection operations currently use the root-capable
system adapter. A successful caller `WHERE 1=0` probe does not authorize an
unrestricted root CTAS, source scan or provider upload. Background definitions
do not persist a governed execution identity. These are unresolved R15
violations: the port must retain caller/service-principal authorization through
build, refresh and provider calls, without putting infrastructure credentials in
the job or runtime. Retrieval must also bound candidate counts, rank/score and
other derived signals by the caller's effective policy, not merely replace the
returned text after an unrestricted candidate query.

## Publication, concurrency and recovery

Feature version allocation uses `MAX(version)+1`; materialization then drops
the selected physical table before rebuilding it. Concurrent refresh can select
the same version and destroy another attempt's output. Three separate updates
deprecate, activate and publish a version for both Features and Search. Reserve
versions transactionally, isolate each attempt's physical output, and CAS the
active pointer only after a verified receipt. Keep old versions readable until
publication and cleanup are independently recoverable.

Search currently serializes with a renewing Redis lock and in-process tasks.
Loss of that lease cancels the Python task but does not prove an engine command
or provider call stopped. The poller selects BUILDING rows again, and the build
drops the same projection before restarting. PG claims need fencing, immutable
build definitions, durable outbox intent and reconciliation before replacing
unknown output. A Redis restart must not authorize replay or deletion.

Search and Feature creation routes still use assigned-role `require_role`.
Management methods use active-role-aware `can_manage`, but creation has no
existing owner to protect it. Switch these route gates to the existing active
role contract and test assigned-but-inactive administrators before acceptance.
Source access, namespace ownership, revision conflicts and crash boundaries
need independent negative and concurrency cases; the inventory classification
does not count as passing those tests.
