# Retired Agent history

Revision `0011_retired_agent_history.sql` prepares three historical stores:

| Source table | Preserved primary key | Source columns |
| --- | --- | --- |
| `CONFIG_AGENT_CAPABILITIES` | `agent_id` | 3 |
| `CONFIG_AGENT_MESSAGES` | `message_id` | 12 |
| `CONFIG_AGENT_SESSION_EVENTS` | `event_id` | 7 |

The [baseline inventory](BASELINE_AUDIT.md) observed 117 messages and 4,910
session events, with no capability rows. These were live aggregate observations,
not an immutable export. Removal commit `ea845165` retired the associated
collaboration runtime. The inspected application has no reader or writer for
these tables; their contents must still survive migration if present at export.

All 22 columns retain their names, nullability and reviewed types. Owner/run
references, opaque IDs, original timestamps, origin/delivery values and nullable
session sequences remain representable. The schema does not invent foreign keys
to active runs or normalize historical enum values. It retains each source
primary key and refuses a conflicting import instead of overwriting history.

The manifests and event payloads use JSONB. Import preserves exact JSON numeric
values, ordered arrays, Unicode and the difference between SQL NULL and JSON
null. A non-null source JSON column may contain JSON null. The metadata value
converter rejects ambiguous or unsupported input rather than coercing it.
Source timezone provenance is still required; synthetic UTC fixtures do not
establish the timezone of historical rows.

These retired tables have no runtime append stream. Flat primary keys preserve
the existing global message/event identities; this revision adds no retention
deletion or partition-maintenance process. Historical export, encrypted backup
and any later retention policy still require separate verification.

## Restricted preservation

The [retained-store boundary](PG_RETAINED_METADATA.md) also applies here:
explicit owner role, maintenance state, an exclusive deployment transaction
lock, and forced RLS. All twelve workload logins, readonly and backup SQL logins
have no SELECT, INSERT, UPDATE, DELETE or TRUNCATE grants. A matching historical
owner or active administrator role does not expose these retired records.
Physical backup privileges remain a separate provisioning contract.

Revision 0011 extends the fixed trigger allowlist for these three tables. It
does not grant new access to the seven earlier retained stores. The combined
regressions verify both groups. A controlled import can reset its target during
maintenance; ordinary runtime code has no reset API. Existing schema checksums
remain unchanged, and upgrading from revision 0010 preserves prior rows and the
existing metadata epoch without opening maintenance writes to applications.

Historical payloads grant no authority to execute tools, issue approvals or
restart delegation. This revision creates no agent loop, route or feature flag.
Current Studio continues to run one selected agent through the shared bounded
engine and its current journals.

The [verification packet](../../reports/production-refactor/WP-03/retired-agent-commands.json)
records 76 focused passes, followed by 409 PG17/PgBouncer passes without skips.
The final suite includes 31 historical-store cases and all 45 preceding retained
store cases, as well as the existing metadata boundary suites.

Final source schema comparison, encrypted exports, timestamp/redaction review,
resumable import and counts/key-set/digest validation remain open. No historical
production/development row has been copied by these schema tests, and StarRocks
remains the live metadata authority.
