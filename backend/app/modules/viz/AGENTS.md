# Native visualization guide

Inherit the [root](../../../../AGENTS.md) and [backend](../../../AGENTS.md)
contracts. Read the [SQL frontend](../../sql_frontend/AGENTS.md),
[access control](../access_control/AGENTS.md), and
[metadata migration](../../../metadata_migrations/AGENTS.md) guides for changes
that cross those owners. Implementation and rollout evidence belongs in the
[native architecture record](../../../../docs/architecture/native-dataviz/IMPLEMENTATION.md),
not this guide.

## Ownership

- Both Dataviz and the Studio Artifact Panel use the same document and query
  services. A conversation link does not copy a dataset, chart or dashboard.
  Keep widget identity separate from the referenced chart identity.
- `contracts/` owns strict versioned definitions and HTTP types. Generate the
  browser contracts with `scripts/generate_viz_contracts.py`; never hand-edit
  generated schemas. Existing `compiler.py` and `calc/` own chart calculations;
  published Semantic Views retain their existing registry/compiler owner.
- Dataset authoring derives fields from the caller-authorized analyzer and
  catalog identity. Never infer types from rows or treat reflection as SELECT
  permission. Revalidate physical identity, schema and security context around
  execution; source replacement requires an explicit new revision.
- Keep engine and directory I/O outside metadata transactions. The document
  repository owns revision CAS, immutable references, idempotency receipts,
  audit and transactional outbox. An unknown mutation outcome is resolved by
  its receipt before another intent is submitted.
- Public reads use `NativeDocumentService` projections. The repository's full
  dataset definitions serve internal execution and authorized draft editing;
  publication responses must not expose source SQL or metric expressions.
  Persist definitions and provenance, never preview rows or resolved secrets.
- QueryService remains the execution owner. Preserve exact integer/decimal
  values, explicit timezones, bounded admission/streaming and caller identity.
  Reject unsupported or ambiguous semantics instead of substituting a source,
  privilege, metric, type or result.
- Native Dataviz is the only chart and dashboard engine. Apache Superset was
  removed on 2026-11-16 ([historical note](../../../../docs/arch-17-superset.md));
  do not add Superset publication, sync or compatibility paths. Legacy
  worksheet views stay read-only through the viz core. Keep historical and
  current behavior explicit in architecture and user documentation.

## Validation

Follow the root validation router. In addition to applicable backend gates,
run affected native contract/service/HTTP tests, real PG17 revision/ACL races,
and governed source checks through the normal gateway. Cover stale identity,
role switches, revoked access, replacement sources, interrupted streams,
publication privacy and unknown mutation outcomes. Browser consumers require
generated-contract drift checks and the frontend gates. Passing metadata or
fixture tests does not establish an editor, migration or release acceptance.
