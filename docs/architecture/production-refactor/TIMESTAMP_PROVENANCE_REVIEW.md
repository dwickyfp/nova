# Metadata timestamp provenance

This review concerns the existing development deployment. On 2026-11-13 the
owner confirmed that its engine timezone and `NOVA_TIMEZONE` have always been
`Asia/Jakarta`. This is a deployment binding, not a default for another host.
The exporter verifies the current session and global zones against that binding.

The packaged `backend/metadata_import/timestamp_provenance.csv` records the
writer clock for each of the 249 inventoried DATETIME columns. Evidence points
to the domain reviews and source commit
`3f6b378977d562b6584caba3d595609b97060f1f`. Python UTC writers retain UTC;
pooled engine `NOW()`/`FROM_UNIXTIME` and Catalog's `nova_now()` use the pinned
engine-session clock. Direct AI/ML connections use the engine-global clock.
Reserved stores with no proven writer remain UNKNOWN; a non-null timestamp in
one of those stores refuses import before any target row is inserted.

The retired Agent message and session-event writers were also checked at
`ea845165^:backend/app/modules/agents/harness_repository.py`. Both their creation
and consumption timestamps use `_now()`, defined as UTC with tzinfo removed.
Import retains that history and does not revive the retired product behavior.

## Semantic Views

These tables have mixed historical producers: normal writes use engine time,
while the former semantic-model migrator copied Python UTC timestamps. They
cannot receive an unconditional table-wide engine-clock classification.

The read-only export on 2026-11-13 contained 11 views and 58 versions. Each view
and initial version matched exactly one successful CREATE audit event by name
and creation time; the other 47 versions each matched one successful ALTER
event. The audit journal contained no semantic-view MIGRATE event, and no
version validation carried the legacy migration marker. Source code confirms
that CREATE/ALTER write the physical timestamps with engine `NOW()`.

Only these reviewed row sets receive `DEPLOYMENT_REVIEW`. Each classification
is bound to the table's full row digest in the provenance CSV. Changed rows
invalidate that proof and require another review. The importer refuses to use
a deployment-specific clock without the matching digest. This evidence does
not permit guessing the clock for a subsequently discovered legacy row.

The earlier provenance-evidence export reported 163 tables, 1,009,174 rows, no schema drift,
no uncontracted tables, and unchanged partition versions. Its manifest SHA-256
is `928abbae87fd76eafe547bc2b78de80ade253edd8cbeef2625d4476c0121550d`.
The private export contains application data and encrypted values; it is not
a Git artifact. Updating the packaged provenance requires a new export with
the new provenance hash. This read-only export is not a completed rehearsal
or cutover, and it does not replace the final writer freeze.

## Remaining acceptance

The importer has passed disposable PG tests for interruption after a committed
chunk, resume without replay, ciphertext separation, exact historical Agent
payloads, legacy stage identity, timestamp conversion, epoch publication,
refusal after publication, changed chunks, and unproven clocks. Full-data
rehearsal 04 also verified payload receipts and semantic checks. Two rehearsals
on the final importer, final freeze and epoch publication remain delivery gates;
see the [repair evidence](../../reports/production-refactor/importer-agent-cutover/README.md).
