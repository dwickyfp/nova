# Catalog metadata port

Revision 0019 prepares the thirteen Catalog tables in `nova_meta`:
`CATALOG_CATALOGS`, `CATALOG_OBJECTS`, `CATALOG_COLUMNS`, `CATALOG_SYNC_RUNS`,
`CATALOG_CONNECTIONS`, `CATALOG_CONNECTION_CREDENTIALS`,
`CATALOG_CONNECTION_WRITE_CREDENTIALS`, `CATALOG_SECURABLES`,
`CATALOG_ANNOTATIONS`, `CONFIG_OBJECT_TAGS`, `CATALOG_LINEAGE`,
`CATALOG_SECURABLE_LINKS` and the retained legacy `CONFIG_EXTERNAL_CATALOGS`.
`CatalogMetadataRepository` (`backend/app/modules/catalog/metadata_repository.py`)
owns the ported catalog, connection, securable, business-metadata, lineage and
link callers. Development activation still awaits verified cutover; the old
source metadata is retained. See the [current delivery evidence](../../reports/production-refactor/importer-agent-cutover/README.md).

Only Nova's own rows move. What the engine lists for a caller, `SHOW CREATE`,
information-schema harvests and source reads stay engine observations behind
their adapters, and a stored row never makes anything visible.

## Access

Catalog metadata is control-plane state. The owning services authorize the
caller (security admin, Ranger `ALTER` through effective access, engine
visibility), so PG access is per workload through a bound actor context:

| Table | Read | Write |
| --- | --- | --- |
| Catalogs | web, query, proxy, agent, maintenance, notebook, task | web, query, proxy, maintenance |
| Objects, securables | web, query, proxy, agent, maintenance | web, query, proxy, maintenance; never deleted |
| Columns | same | web, query, proxy, maintenance, including delete |
| Sync runs | web, query, proxy, maintenance | same; never deleted |
| Connections | web, query, proxy, maintenance, notebook, task | web, query, proxy |
| Read credentials | ciphertext: web, query, proxy, maintenance | web, query, proxy |
| Write-back credentials | existence: web, query, proxy; ciphertext: notebook, task | web; delete also query, proxy |
| Annotations, tags | web, agent | web |
| Lineage | web, query, proxy, agent, maintenance | web, query, proxy, maintenance; never deleted |
| Links | web, query, proxy, agent, maintenance | same, including delete |
| Legacy external catalogs | none | migrator import only |

These are the workloads that reach the source repositories today: the API,
catalog SQL through the query service and MySQL proxy (`CREATE CONNECTION`,
`CREATE CATALOG`, `REFRESH CATALOG`), the maintenance refresher, the agent's
catalog search and Semantic View tool, and notebook/task write-back. Scheduler,
ingestion, ML, automation and outbox have no grant.

Revision 0039 adds a fixed audit projection for the maintenance login, bound to
the `catalog-refresher` service principal and its `SECURITYADMIN` role. It returns
only query ID, event time, database and redacted statement text for successful
INSERT/CREATE query events, with a required time bound and at most 5,000 rows.
It grants no general audit visibility. Manual refresh retains the caller's
existing audit RLS. Lineage passes use aware timestamps; imported naive
watermarks retain the reviewed deployment timezone.

## Gaps closed in the new store (Wave G)

- **Sync fence.** A sync claims its catalog by advancing `sync_generation`
  and names that generation in transaction settings for its writes. Every object,
  column and securable write locks the catalog row `FOR SHARE` and must name the
  current generation, so a claim waits for in-flight writes and every later
  write of an older harvest, including a tombstone or `retire`, is refused. Sync
  status on the catalog is recorded only by the current generation. An imported
  orphan with no catalog row can only be tombstoned.
- **Revision CAS.** Catalog, connection and annotation changes name the
  revision they read and return the written row; concurrent edits from one
  revision give one success and one conflict. A raw definition change without a
  new revision is refused. Tags upsert atomically per key and can name a revision.
- **Created once.** Sync runs are created once by id, start `running`, finish
  once as `ok` or `error`, and name the claimed generation.
- **One transaction.** Column replacement, owner-link replacement and
  connection deletion with both credential rows each run in the caller's single
  transaction.
- **Derived identity.** Runtime object, column, securable, lineage and link ids
  must equal the source's `row_id` hashes of their names; securable parents are
  checked the same way (column securable ids are case-folded in Python and are
  not recomputed in SQL). Imported ids are kept as they were.
- **Lineage order.** Overlapping or late harvests only widen an edge's span; the
  newest statement keeps its query id. Lineage times must be timezone-aware.

## Secrets

Credentials are stored as ciphertext of `app.common.crypto.encrypt`; the
database refuses any runtime value that is not an `enc:` Fernet token and any
credential for a missing connection. Column grants keep the write-back
ciphertext from management workloads; credential entry is an update followed by
an insert, because an upsert would have to read the excluded ciphertext. No
repository method returns ciphertext
or plaintext except `read_credentials`, which decrypts for engine use only.
Audit rows name the connection, never a value, and driver errors are mapped to
generic categories. Catalog properties and connection options are refused when a
key matches the `sql_guard` credential-key rule. `secret_ref` must be a
reference (`nova://<own name>`, a supported scheme or a Secrets Manager ARN).

## Stewardship

Business metadata keeps the governance rules: setting or changing an owner, and
any annotation or tag on a catalog or database, requires an active role in
`AccessControlService.SECURITY_ADMIN_ROLES`. Classification applies to columns
only; owner type and name are set together. Ranger `ALTER` for relation edits
stays a service check. Catalog definitions, connections, credential entry or
removal, annotations and tags write a success audit row (annotations list the
changed fields); inventory, runs, lineage and links are derived state and are not
audited, as in the source.

## Findings not fixed in the storage layer

- Catalog and connection services run the engine DDL (`CREATE/ALTER/DROP
  CATALOG`) before writing the Nova row, so a failed metadata write leaves an
  engine catalog without its row. The port needs persisted intent before the
  engine effect and reconciliation after an unknown outcome.
- Manual `REFRESH` runs the whole sync and the lineage/link pass inline in the
  web, query and proxy processes. The runtime target gives catalog refresh to
  maintenance with HTTP persisting intent; grants follow today's callers and
  should narrow when that moves.
- Runtime sync claims its catalog generation and commits the harvested metadata
  and completion together. External catalog DDL still needs the effect recovery
  work above.
- Legacy catalog/tag migration no longer runs at application startup. The
  one-shot import owns historical preservation.
- Source timestamps are naive deployment-zone values (`nova_now`); import must
  convert them with the recorded zone. Runtime rows use the database clock, and
  lineage times come from audit event times, which must be timezone-aware.
- A legacy catalog with invalid `properties_json` is imported as is but must be
  given valid properties before its first runtime change.

Validation is recorded in
[the Catalog metadata checkpoint](../../reports/production-refactor/catalog-metadata/README.md).
