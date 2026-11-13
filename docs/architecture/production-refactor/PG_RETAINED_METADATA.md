# Retained metadata without runtime consumers

Revision `0010_retained_legacy_metadata.sql` prepares seven stores identified in
[the source-access review](WAVE_J_REVIEW.md). None has a data reader or writer
in the inspected application tree. This preserves their migration destination;
it does not restore retired dashboard or pinned-query behavior.

| Source table | PostgreSQL primary key | Partition key |
| --- | --- | --- |
| `CONFIG_DASHBOARDS` | `id` | None |
| `CONFIG_DASHBOARD_WIDGETS` | `id` | None |
| `CONFIG_PINNED_QUERIES` | `id` | None |
| `LINEAGE_LOAD_HISTORY` | `started_at,record_id` | `started_at` |
| `QUALITY_TABLE_STATS` | `collected_at,record_id` | `collected_at` |
| `STAGE_FILE_MANIFEST` | `record_id` | None |
| `USAGE_QUERY_STATS` | `started_at,record_id` | `started_at` |

All 71 source columns retain their names, nullability and reviewed types.
Opaque IDs, nullable owners, Unicode text, historical references and signed
counters remain representable. Four duplicate-key stores add `record_id`
without replacing their legacy numeric IDs. The existing import converter
derives each UUID from an immutable export address, preserving identical source
occurrences separately. Reusing an imported primary key conflicts; the schema
does not silently collapse records or treat a conflict as successful replay.

Archived dashboard and stage references are preserved without new foreign keys
to active stores. Their targets may already have been deleted, and the source
has no corresponding enforced relationship. A future product reader would need
its own authorization and reference-validation contract before receiving access.

The three time-partitioned parents initially route all rows into default
partitions. This admits historical timestamps without inventing a retention
window. Partition maintenance and verified export/backup are still required;
there is no automatic data deletion. Source timezone provenance must be supplied
explicitly to the converter. The synthetic Asia/Jakarta test does not establish
the timezone of production rows.

## Import boundary

There are no workload, readonly or backup SQL grants on these stores. Even a
runtime connection with valid `ACCOUNTADMIN` actor context is refused. Every
parent and leaf enables and forces RLS. Parent import policies apply only to
the explicit `nova_meta_owner` role while deployment maintenance is active.
The migrator login must explicitly select that role in its transaction.

Statement triggers require this identity and maintenance state for INSERT,
UPDATE, DELETE and TRUNCATE, and hold the exclusive deployment advisory lock
until transaction end. Leaf triggers also fence direct TRUNCATE, which RLS
does not cover. A deliberate reset under the import identity is allowed during
maintenance; it is not a runtime cleanup API. The controls do not claim to
constrain a trusted superuser or an operator who can alter database objects.

PostgreSQL 17 refuses `COPY FROM` on a table with applicable RLS. Import must use
bounded parameterized INSERT batches within the reviewed import transaction;
RLS is not disabled to permit COPY. Tests retain that refusal both during and
after maintenance. See the [PG17 COPY contract](https://www.postgresql.org/docs/17/sql-copy.html)
and [row-security limits](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).

## Verification and remaining work

The real-PG suite independently checks columns against the captured source
inventory, round-trips populated and nullable rows, preserves duplicate
occurrences, refuses exact-key replay, checks all workload/readonly/backup
identities, and tests import rollback, deployment locking and maintenance
fencing. The first experiment incorrectly expected COPY to succeed under
forced RLS; its failed result is retained. The corrected contract uses INSERT
and keeps the security boundary intact.

The [verification packet](../../reports/production-refactor/WP-03/retained-commands.json)
records all three PG attempts. The final combined run passes 378 cases without
skips, including 45 retained-store cases. CI's test image includes the captured
schema inventory so it runs the same independent column checks.

The earlier zero source counts are observations, not permission to omit a table
from final export. Final schema comparison, source timestamps, historical
redaction review, encrypted export storage, resumable import, and counts/key-set/
digest validation remain open. No development rows have been moved, and
StarRocks remains the live metadata authority.
