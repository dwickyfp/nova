# Native Dataviz baseline audit

Baseline commit: `1ee12694e0ecb6ecfd8135965943be08f5195967`.
Inventory date: 2026-11-13. This is an inventory, not migration acceptance.

## Current owners and replacement boundaries

| Entry or effect | Observed owner | Native integration boundary |
| --- | --- | --- |
| Studio Dataviz tab | `frontend/src/features/studio/dataviz/dataviz-workspace.tsx` mounts `BiFrame` | Mount the shared native editor after conversion gates |
| Studio artifact panel and artifact CRUD | `studio-artifacts.tsx`, `modules/agents/studio_router.py`, artifact repository | Stable native resource and revision IDs, same document commands |
| Dataviz publication and repair | `modules/superset/dataviz_router.py` and publication workflow | Preserve durable intent, receipt and recovery through native commands |
| Charts and dashboards in Superset | `modules/superset/artifacts.py`, client, conversion adapters | Convert exact definitions and ACLs; retain source authority until cutover |
| Inline visualization and calculations | `modules/viz/service.py`, `compiler.py`, `calc/` | Extend existing calculation and SQL owners |
| Data access | QueryService, security context, SQL frontend, patched FE and Ranger | Viewer identity and one active role on every execution |
| Document metadata | Sole PostgreSQL metadata owner and actor-bound transaction | Immutable definitions, CAS, ACL, receipts and outbox |
| Named workspace membership | Workspace sharing repository and SpaceService | Membership plus separate document action grants |
| Assistant operations | Existing assistant harness and agents tool adapters | One command owner, consent and caller identity |
| Exports, files and background effects | Existing workspace/storage and durable worker owners | Persist intent before effects; private payloads and recovery receipts |
| Browser launch and proxy routes | `features/bi/`, `/bi-launch`, gateway `/bi/`, Superset router | Authorized legacy ID mapping until references are retired |
| Superset runtime | Existing web, worker, beat and application database | Retain through migration, regression and soak acceptance |

The searchable [caller inventory](source-references.csv) includes source, tests,
deployment and documentation references. The approved integration map is retained
with the handoff; source facts above determine current ownership.

## Captured assets and identities

Read-only snapshots contain 7 dashboards, 52 charts, 22 datasets, 44 metrics and
227 columns. Dashboard/chart/dataset owner associations are complete: no missing
owner IDs. All seven dashboards are unpublished, with no dashboard role
associations or slugs. One contains native filters. The snapshot has no scheduled
reports, embedded dashboards, favorites, annotation layers or custom plugins.

Identity metadata includes 3 user identifiers, 10 role identifiers, 5 user-role
associations and 813 permission-role associations. Passwords, database connection
credentials and opaque key-value payloads were excluded. Private snapshots are
outside Git with restrictive permissions; reports contain counts and hashes.

Nova has 10 stored BI artifacts (8 charts, 2 dashboards), 6 publication records
(5 succeeded, 1 failed final), and 22 semantic dataset registrations, including
11 non-null BI references. Two artifacts have thread references. The recognized
numeric dashboard and slice URL scan covered 885 assistant messages and found
no additional URL candidates; raw conversation text was not exported. This scan
does not assert that arbitrary prose or external bookmarks have no references.

The snapshots expose two migration defects: one missing BI object and one
missing dataset. They remain unresolved. No replacement object, permissions or
role mapping has been invented. Conversion must reconcile these IDs explicitly;
unpublished sources retain owner-only visibility until verified grants exist.

Evidence: [asset counts](superset-inventory-summary.json),
[identity and Nova references](identity-reference-inventory.json),
[reference analysis](legacy-reference-analysis.json),
[thread links](thread-reference-inventory.json).

## Runtime, dependencies and validation boundary

The starting development epoch was active at schema 47. Additive schema 48 was
subsequently installed through the migrator after a private backup; the epoch and
180 existing table counts were preserved. Readiness of the original checkout was
verified with the repository's readiness gate. Superset remains active.

PG tests use the single user-approved disposable PG17 fixture. No extra FE/BE,
Ranger or object-store stack was created. The fixture's first broad regression
failed during a database recovery; both the failure and topology adjustment are
retained. [Upgrade evidence](development-schema-48.json) and
[fixture recovery](pg-fixture-recovery.json) distinguish these environments.

CycloneDX 1.6 SBOMs describe 141 backend and 739 frontend components. Six missing
license declarations were resolved against the exact installed version or its
upstream release. The [license inventory](license-audit.json) accepts open-source
licenses; distribution notices still need the release audit. GridStack 14.1.0 is
MIT; ECharts 6.1.0 is Apache-2.0. The official GridStack React wrapper passed real
browser mount, isolation and text-safety checks.

The baseline dependency scan after the Zod and Seroval fixes reported 0 critical,
8 high, 22 moderate and 9 low findings. The subsequent pinned security updates
are recorded separately in `frontend-audit-attempt-3.json`: 0 critical, 0 high,
4 moderate and 2 low. The updated frontend SBOM contains 736 components with
complete license metadata. Remaining findings and distribution notices remain
release work. An inventoried package is not a security approval.

The complete 254-scenario acceptance matrix remains open. This audit establishes
the baseline and identifies conversion blockers; it does not establish native
product behavior, semantic parity or a safe Superset cutover.
