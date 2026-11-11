# Catalog, Explorer and query boundary

Catalog repositories persist a materialized inventory, annotations, tags,
securable links, lineage and sync runs in Nova control metadata. Their batch
helpers have fixed table callers: objects, columns, sync runs, tags and
annotations. The credential selector chooses only the separate read or write
credential table. Both encrypted stores belong in PG with restricted worker
read grants; user-facing connection metadata must not select those columns.

Connection deletion must transact the connection row and both credential rows.
Column refresh and owner-link replacement must publish an entire new set
atomically. Catalog sync needs a fenced generation so an older harvest cannot
mark newly discovered objects dropped. Preserve stable securable IDs, tombstones,
ordinal-based column IDs and lineage IDs. `nova_now()` timestamps need its
configured-timezone conversion, unlike the Python UTC writers reviewed earlier.

The legacy tag migrator renames a nonempty source before copying only mappable
rows. The renamed source is part of the preservation inventory if present;
unmappable names are not permission to discard records during PG import. Runtime
DDL and legacy imports move to the one-shot migrator after source profiling.
Audit-derived lineage reads are PG metadata reads after cutover.

EngineHarvester's seven calls stay in the restricted engine observer: catalog
and database discovery, information-schema table/column/materialized-view
records, SHOW TABLES and DESC. The cached result is not an authorization grant.
Catalog SHOW CREATE remains an engine observation whose properties must be
redacted before a response. Explorer's stage/task reads move to PG; its other
calls are engine observations, restricted to caller-visible objects by the
existing caller metadata path. Row counts, properties, comments and definitions
still need policy-boundary QA; an observer account does not grant public access.

Query history/counts/preferences and StageRuntime's stage lookup are PG
metadata. Stage authorization must precede secret resolution. Object-service
schema/stage/task listings also query these PG stores. Engine capability/version
probes remain engine observations.

`QueryRepository.execute_as_system` accepts arbitrary SQL and has no current
application caller found by repository search. It is classified as a mixed,
unrestricted adapter to remove during the split, not a candidate PG SQL bridge.
Function listings use the system observer today; their visibility needs caller
filtering. Function creation/deletion have a system-connection fallback, but
their only application callers are routes that supply the authenticated user
connection. Eliminate that fallback when separating engine administration;
do not make the PG metadata identity capable of registering user UDFs.
