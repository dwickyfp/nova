# Duplicate rows and historical dashboard identity

The seven live duplicate-key tables now have explicit target key definitions.
StarRocks duplicate keys are ordering keys, so their columns are not promoted
to PostgreSQL uniqueness constraints. Every legacy column, including existing
numeric/string IDs, remains present and unchanged.

| Source table | Target primary key | Existing ID retained without deduplication |
| --- | --- | --- |
| `AUDIT_LOG` | `event_time, record_id` | `log_id` |
| `AI_SEARCH_QUERY_LOG` | `created_at, record_id` | `id` |
| `LINEAGE_LOAD_HISTORY` | `started_at, record_id` | `load_id` |
| `QUALITY_TABLE_STATS` | `collected_at, record_id` | `stat_id` |
| `USAGE_LLM_FUNCTIONS` | `recorded_at, record_id` | `usage_id` |
| `USAGE_QUERY_STATS` | `started_at, record_id` | `stat_id` |
| `STAGE_FILE_MANIFEST` | `record_id` | `file_id` |

The temporal keys permit time partitioning. `record_id` is an additional opaque
UUID. Imported rows derive it from a stable snapshot UUID, table name, immutable
chunk SHA-256 and zero-based row offset across the entire table. Identical rows at different offsets
have different identities. An interrupted import reuses the same verified
snapshot/chunks; changing chunk boundaries or re-exporting is a new import.
New application events will allocate new record IDs in their owning repository.
The original IDs remain available to existing consumers; no uniqueness is inferred
from observed row counts or from auto-increment declarations.

`core.metadata_import.import_row_id` implements the deterministic identity rule.
Its input validation does not authenticate an export or prove a writer freeze.
The export/import orchestrator must verify the manifest, chunk hashes, row counts
and cutover ownership before using it. The packaged importer now verifies those bindings and commits chunk receipts
with their rows; one full disposable rehearsal matched the source inventory. The PG fixture proves that two occurrences with the
same legacy ID survive, replaying one identity conflicts, and a rejected value
rolls its transaction back. It does not prove a complete source import.

## Historical dashboard definition

Current compatibility readers refer to `CONFIG_STUDIO_DASHBOARDS`, which is
absent from the inspected live schema. Git contains its former owning DDL at
commit `ec9741123fac23780f0580747c36188e216e4210`, in
`backend/app/modules/agents/dashboard_repository.py:14` (blob
`58042971f73250da4f8db0d12be7ae86a90965f4`). It declares `dashboard_id` as the
primary key and includes `owner_name`, `title`, `layout_json`, `created_at` and
`updated_at`. The create/update writer uses `datetime.now(UTC).replace(tzinfo=None)`.
The current converter derives artifact identity from the existing dashboard ID.

The mapping records this declared key and retains `source_rows=NOT_PRESENT`.
It does not create the table or claim a live key check. If the table appears in
the final export, compare its actual schema and historical writer/deployment
provenance before importing; preserve owner, IDs and the original layout.

## Column conversion coverage

[COLUMN_TRANSFORMS.csv](COLUMN_TRANSFORMS.csv) maps all 1,578 observed columns in
157 live tables to supported PG value types and records each source DDL digest.
`core.metadata_import.ImportColumn` implements strict binding conversions:
bounded signed integers, explicit booleans, finite doubles, preserved Unicode
text/ciphertext, JSON with exact numeric text and distinct SQL/JSON null, and
timestamps requiring an explicit proven zone. Unsupported types, duplicate JSON
keys, invalid strings and lossy coercions are refused with a redacted exception.

All 249 DATETIME columns are classified in the packaged provenance ledger:
232 have reviewed writer clocks, six have snapshot-bound deployment evidence,
and 11 reserved columns remain unknown and reject non-null values. See
[TIMESTAMP_PROVENANCE_REVIEW.md](TIMESTAMP_PROVENANCE_REVIEW.md). Current engine
timezone alone does not establish the timezone used by every historical writer.
The helper rejects naive timestamps without a selected provenance zone, DST gaps
and repeated local times. It does not select a timezone or default fold to make
an import succeed. Operator/row-specific reconciliation must precede those imports.

There are no unresolved target key *definitions* in the ownership CSV after this
review. Logical uniqueness, FK/orphan resolution, domain checks, grants/RLS,
final deployment binding, second full rehearsal, payload/semantic validation
and cutover acceptance must still be completed on the delivered revision.
This review does not complete WP00 or WP03.
