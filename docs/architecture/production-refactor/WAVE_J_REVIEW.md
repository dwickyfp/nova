# Table-level access review

This review binds the inspected source calls to table sets. It does not grant
PostgreSQL privileges or certify a migration. The application tree is
`e3bcb3f5604f20c68c98e8beb3f5e9c67abfcee416c7ab4767dc8954ae546759`.

`TABLE_ACCESS_REVIEW.csv` records 755 metadata, schema and adapter sites from the
expanded source inventory, including four Explorer observations whose earlier
destination classification was corrected during this review. Each row retains
its source-file and callsite digest. The remaining 120 inventoried sites are
engine administration, authentication, caller data or engine observation;
`SYSTEM_CALLS.csv` retains their classification.

The table sets come from SQL literals, local query variables and reviewed
fixed-map/helper callers. They include Intelligence's 14-table map, Ontology's
12-table map, the three Business Event tables, five Agent Quality tables and
13 Autopilot tables. A dynamic table argument is not accepted merely because
its module has a recognizable name. Schema imports and DDL tuples are bound
separately from data reads/writes. Connection factories and pass-through
adapters are identified without pretending that opening a connection itself
mutates a table.

Modes have these meanings:

- `READ`: statement reads control rows; fixed helper alternatives are a union.
- `WRITE`: statement can mutate control rows. The table set includes any
  subquery sources; it is a conservative dependency set, not an INSERT/UPDATE
  privilege list. In particular, legacy ML copy statements read the source
  and write only their `_PK` target.
- `DDL`: bootstrap or schema change. Retire these runtime duties at cutover;
  the one-shot migrator becomes the schema owner.
- `OBSERVE`: reads engine schema facts about Nova tables. It does not read
  control-row values and is retired or replaced with schema-epoch checks.
- `DELEGATE`: context manager whose nested cursor operations are also listed.
- `ADAPTER`: connection or generic executor; terminal consumers are classified
  separately. Mixed adapters cannot be switched wholesale to PG.
- `ENGINE_OBSERVATION`: the corrected Explorer paths; no Nova target table.

The four corrected Explorer calls use local variables containing queries of
`information_schema.tables_config`, `views`, `materialized_views` and `pipes`.
They remain engine observations. Caller-visible filtering remains mandatory.
The previous wave classification had incorrectly labeled these dynamic calls
as metadata; that correction is retained in the source-call notes.

`METADATA_OWNERSHIP.csv` now names the actual read/write, schema and observation
sites per table. `NONE_IN_CURRENT_APPLICATION` means no data consumer exists in
the inspected application tree. It does not mean an operator, backup or old
binary cannot access the table; migration must still freeze/revoke every old
writer and verify the frozen live schema.

Seven live tables have bootstrap DDL but no current runtime data consumer:
`CONFIG_DASHBOARDS`, `CONFIG_DASHBOARD_WIDGETS`, `CONFIG_PINNED_QUERIES`,
`LINEAGE_LOAD_HISTORY`, `QUALITY_TABLE_STATS`, `STAGE_FILE_MANIFEST` and
`USAGE_QUERY_STATS`. Each had zero rows at observation time. Their rows and
identity must still be preserved if present at final export; do not drop these
stores based on an earlier zero count. The three historical Agent tables retain
the separate history-preservation policy already recorded in the baseline.

Autopilot's proposed process destination is corrected to `automation`, matching
the approved responsibility split. Maintenance still owns refresh, sweeps and
reconciliation, not the Autopilot action executor.

Per-role consumer grants, typed transforms, historical timestamp provenance,
constraints and cross-store transaction protocols remain unresolved. Therefore
this table-binding review does not close WP00 or authorize data migration.
It makes those decisions reviewable against concrete readers and writers.
