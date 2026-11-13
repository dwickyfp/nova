# Governance, publication and bootstrap boundaries

User identities, passwords, role markers, default roles and engine properties
remain in StarRocks. The user/auth services' system calls are restricted engine
administration or observations. PG must not become an authentication store.
Ranger role projections, policy status and data-scope assignments are Nova
control records. Their timestamp producer is Python UTC-naive. Creating a
Ranger policy, creating an engine role marker and recording projection state
are separate external effects; a PG commit cannot certify FE enforcement.
Preserve ACCOUNTADMIN protection and revalidate/invalidate delegated sessions
when role membership changes.

Semantic View definition/version reads and writes are metadata. Create,
MAX-plus-one version allocation, activation and retirement need one PG
transaction with expected revision and unique namespace/version constraints.
The existing null-safe `<=>` active-version comparison needs an explicit PG
equivalent. Immutable published definitions and verified-query proofs retain
their fingerprints and original IDs. Legacy semantic migration retains source
records and private visibility; it must not run implicitly on each worker.
These row timestamps use engine time, except imported legacy timestamps whose
original producers must be retained in the transform contract.

Ontology's revision repository has two fixed consumers: twelve Ontology tables
and the three Business Event tables. These are deployment-wide records whose
actor is provenance, not a private ownership predicate. Service authorization
must still restrict stewardship and publication. Per-record and batch writes
currently use different Redis lock keys; a batch's preflight checks and its
500-row chunks are not a transaction. PG must resolve conflicts atomically,
preserve same-revision branches for reconciliation, and keep released revision
references stable. Row timestamps use engine time; payloads use UTC values.

## Dataviz and reporting

All Superset module system calls concern Nova metadata: author roles,
publication journal, semantic dataset mapping, message preview and dashboard
context. Superset's own DB remains independently owned. Journal operations use
Python UTC-naive timestamps; role/mapping rows use engine time. Preserve
operation ID, idempotency scope, request/query/visualization fingerprints,
caller role/version and every external object ID. PG needs unique admission and
fenced journal revisions; Superset chart/dataset/dashboard effects need receipt
verification before repair or replay. Author-role replacement is atomic.

The Viz legacy migrator changes artifact tags and explicitly removes converted
tile-dashboard rows. This behavior is not permission to omit legacy objects
from the PG import. Preserve legacy artifacts until their separately verified
conversion completes.

## Bootstrap, probes and less visible writers

`nova_system`'s 33 calls are metadata bootstrap, migration and setup markers.
The fixed Task/Streams/Ranger/ML definitions and helper columns were inspected.
Source ML `*_PK` intermediate tables and `*_LEGACY` archives must be recognized
if discovered after an interrupted legacy migration. The four history sort-key
targets also belong to metadata; their engine ALTER workflow retires after PG
cutover in favor of explicit indexes. No runtime app role may execute this DDL.

The readiness `SELECT 1` remains an engine dependency probe; production readiness
also needs role-specific PG/epoch checks. Global timezone initialization is
engine administration. `StarRocksConnectionFactory.execute_system` is the mixed
root-capable terminal adapter that must be split, not redirected to PG.

AI function usage flush writes only `USAGE_LLM_FUNCTIONS`, using Python UTC-naive
timestamps. It currently merges a failed batch back into memory; a lost commit
acknowledgement can therefore duplicate usage and process death can lose the
unflushed batch. Use stable batch identities and durable accounting admission.
Autopilot overview counts use the fixed Autopilot control-table map. The demo
seed reads both training-set metadata and one synthetic user-data row; the latter
remains an engine data call and the seed must never run as a production worker.

Reviewing direct connection factories found an additional inventory gap:
`UserService.list_users` reads audit metadata on a raw root connection. Provider,
AI function and ML registry services also open direct connections. The scanner
now includes raw connection factories and metadata-bearing cursor functions;
these new candidates require their own review before WP00 can close. The old
791 pooled/indirect-call count alone was not a complete writer inventory.
