# Smart Report architecture

## Definition and execution state

`ReportSpecV1` is a declarative definition: pinned semantic sources, widgets,
typed visual encoding, filters, pages and 12-column placements. Identity, ownership,
grants and revisions are server-owned. Pydantic rejects additional fields and
validates references, layout overlap and dependency cycles. The generated JSON
Schema is consumed by frontend Zod; generate it with
`cd backend && uv run python scripts/generate_report_contracts.py`.

`ReportViewStateV1` is exploration state. Filter, page, sort, selection and drill
events update only the Redis ViewSession. They never patch a definition. A draft
belongs to an editor; published versions remain immutable after an explicit
publish. Saved exploration presets are separate metadata records, scoped to the
principal, active role and exact definition revision. Applying one uses CAS on the
existing ViewSession so conversation and full view remain synchronized.

## Metadata journal and recovery

`NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS` stores append-only records for reports,
drafts, published revisions, grants, presets, migration records and artifact
provenance. Record ID + revision + operation ID preserves competing branches so
the reader can detect forks. Every row carries a request hash and payload checksum.
The repository uses the existing metadata lease and checks the head before and
after append. Ambiguity returns 409.

Publish writes a verified immutable version before advancing the report pointer.
Readers cannot select a version beyond that pointer. Replaying the same operation
after a timeout returns the existing write; changing its request is a conflict.
If a crash leaves a version without a pointer, replay the original operation.
Do not repair a fork by deleting history or selecting the newest timestamp.

## Governed data

The report adapter compiles widget configuration and validated session filters into
the existing SemanticPlan. Semantic Views checks the pinned version/fingerprint and
executes through QueryService using the caller. No report SQL parser, agent loop,
storage system or alternate authorization engine is introduced.

Each batch pins a common as-of instant. Results include definition/state revisions,
state fingerprint and per-widget execution evidence. The compiler applies local
calculation ASTs after aggregation and before display limits. Table totals are
independent engine aggregates over the filtered input, with window-dependent
calculations omitted rather than summed from displayed rows. Decimal values are
strings at the transport boundary.

Report grants use the active role, not all assigned roles. Report access never
implies semantic source access. The live authentication session is checked before
and after data execution; a role/security-version change rejects the result.
Report grants and ViewSession state are also rechecked before returning render,
filter-option, Ask Nova and insight results. Cross-request result caching is bypassed until policy freshness can be verified.
Within one batch, identical plans share an in-flight query.

## Chat, handoff and rendering

The server-issued `smart_report` content block contains safe report/draft references
and fallback copy, not a spec or rows. The same renderer is used in compact chat,
expanded chat, full report, editor preview, saved delivery results and the
headless export presentation.

An Open Full handoff is random, single-use, ten-minute Redis state bound to the
principal, active role, security version, authenticated session and report. Its
token is removed from the browser URL after consumption. The chat binding and full
view share a ViewSession. A copied canonical link creates an independent session.

Ask Nova resolves the report, session, current selection, conversation ownership
and data access on the server. Evidence is queried under that state. Client labels
or chat prose cannot override the effective filters. Shared transcript sanitization
does not expose private report context or materialized report rows.

## Legacy sources

Migration copies dashboard metadata into a replacement draft, records the source
fingerprint, and never executes legacy SQL as a service account. Unknown mappings
remain non-executable placeholders. Re-running migration does not overwrite edited
drafts. Changed sources receive a ledger status requiring reconciliation.

New artifact provenance is issued only for a lossless semantic plan conversion and
bound to the producing identity and exact SQL. Copying an artifact into a report
copies configuration, not a live reference that can mutate a published report.

Supported scalar Vega encodings are copied into the typed report chart adapter.
Unrepresentable transformations, additional dimensions or changed artifact
fingerprints require explicit mapping and cannot execute as report widgets.

## Draft review and preview

Undo/redo appends validated draft revisions with bounded history stacks. A new
edit clears redo; neither action rewrites published versions. Submitted reviews
pin a draft revision and checksum. Publishers can inspect only submitted copies,
then explicitly copy one into their own draft and publish separately. Subsequent
editor changes remain private. New editors start from the published definition or
the report's initial revision when no published version exists.

Candidate data preview executes the proposed definition with its default filters,
under the editor's current data access. It uses the shared renderer and query
runtime without persisting a draft or changing the exploration session. Results
are discarded if the draft revision or authorization changes during execution.

## Calendars and observations

Reports default to Nova's configured timezone, Gregorian year and Monday week
start. Fiscal start month and week start are explicit definition fields. The existing
semantic time resolver pins relative boundaries, and the existing compiler shifts
engine buckets before aggregation. Absolute calendar tokens retain their original
Gregorian meaning. Calendar edits stay in the draft.

Deterministic insights inspect freshly authorized, complete widget results. Period
comparison preserves decimal precision and reports no relative change for a zero
baseline. Largest-value observations preserve ties and distinguish null from zero.
Neither detector claims causality. Paged, truncated, denied and empty results are
explicitly excluded. These detectors are the implemented subset, not the complete
S13 insight catalog.

## Analytics and delivery

Pivot cells, subtotals and grand totals are independent semantic queries over the
full authorized input; distinct totals are never summed from visible cells.
Detail queries require published dimensions from one dataset and every declared
grain key. They retain the report filters, selected group and caller identity,
with 100 rows per page and a visible 2,000-row bound.

Local metric ASTs support arithmetic, CASE, null-safe division, running totals,
moving averages and rank. Decimal literals use their actual precision: inflating
every constant to DECIMAL(38,s) can cause engine COALESCE promotion to DOUBLE.
Calendar comparisons use one pinned instant, explicit timezone, fiscal start and
week start. Comparable leap/month spans are clamped with a returned warning.

Insights expose period variance, largest values, reconciled contribution and
unusual time values. Contributions require nonnegative groups whose sum equals an
independently queried total. The unusual-value detector requires eight complete
time groups and nonzero median absolute deviation, with modified z-score > 3.5.
These are descriptive signals, not causal or forecasting claims. Partial results
are excluded; server evidence accompanies every observation.

PDF and PPTX use headless Chromium and SmartReportRenderer; XLSX packages every
widget with exact decimal strings and an evidence sheet. PDF pages use the same
1,200-pixel layout as screenshots to prevent print media from clipping a canvas.
PPTX splits long pages into readable image segments. Pagination collects all rows
before writing a payload. Tables above 4,999 rows or reports above 20,000 rows fail
explicitly; no partial file is silently released. Renderer resources remain local;
report maps use the bundled overview and the existing map adapter.

Exports, templates, subscriptions and activities use the append-only report
journal. Binary payloads and immutable activity snapshots use the configured
managed object store. Export download and activity read reexecute pinned evidence
under current authorization because a policy freshness fingerprint is unavailable.
Changed rows, masks, grants or execution binding prevent release.

Subscriptions configure reports.distribute on the existing task orchestrator.
Each subscriber consents to their own user/active role. The scheduled handler
uses the verified delegated connection and rechecks the role binding. It writes a
prepared activity before deterministic Studio thread/message IDs; replay recovers
without duplicating delivery. Changed subscription configuration terminalizes a
prepared activity as cancelled, allowing a later run to proceed. Published version
changes require review. Thresholds use Decimal, typed windows and persisted cooldown.
Only Nova history and report activity receive results. No email or Slack path exists.
