# Requirement traceability

The accepted plan and ACCEPTANCE.md govern this delivery. Original spec test IDs
remain useful identifiers; multi-host HA and the original performance profile
are excluded from this delivery's acceptance by the owner's explicit updates.

| Requirement | Work packages | Evidence needed | Status |
| --- | --- | --- | --- |
| Complete metadata ownership and writer inventory | WP00 | Live schema, static + dynamic call review, migration mapping | IN_PROGRESS |
| Consistent architecture instructions | WP01 | Root/scoped rules and operational docs agree with runtime/migration state | IN_PROGRESS: placement rule updated; runtime and operator documentation still pending |
| PG-only transactional metadata | WP02–07 | DB01–15 as applicable; all domain regressions; no stale metadata path | IN_PROGRESS: transaction boundary including login identity and malformed-context refusal, immutable schema installer, restricted identity bootstrap and PgBouncer actor/deadline checks tested on PG17; preference, provider/model, function-alias and Stage storage tested with RLS/CAS; immutable AI function definitions bind configuration changes to jobs/outbox; prepared stores now include atomic success audit; Stage scope/action, namespace identity, tombstone/CAS, legacy preservation and PgBouncer isolation pass the 489-case local PG suite; remaining domains, caller ports, refusal-audit integration, production Compose provisioning and cutover pending |
| Lossless schema translation | WP03 | Per-column types/provenance, relational constraints, duplicate retention and full import checks | IN_PROGRESS: 1,578 column type mappings, strict value conversions and all target key definitions; seven unused stores and three retired Agent stores have import-only PG destinations preserving 93 columns, original identities and duplicate occurrences; 249 timestamp provenance reviews, remaining domain DDL and import orchestration remain open |
| Durable jobs and effects | WP08, WP13 | JOB01–23; crash matrix; engine receipts; Redis loss | IN_PROGRESS: PG queue/outbox storage, lease expiration, immutable intents and receipts pass controlled real-PG checks; scheduler leadership and occurrence storage also pass; participant verification, runtime ports and external fault acceptance pending |
| Explicit workload processes | WP09 | Admission, supervisor, cancellation, resource isolation | NOT_STARTED |
| Durable agent consent/replay | WP10 | Restart, role revoke, foreign approval, repeated tool invocation | IN_PROGRESS: existing replay now drains failed-run pages, closes missing completed history explicitly and journals Mission updates. Read-only grants bind login/principal/role/version, reload expiry/revocation before each turn and validate context at tool dispatch/prefetch; one-action decision responses preserve the existing read-only selection; controlled regressions and final local API/helper fixtures pass, with the earlier creation timeout retained. Proposal UUIDs now separate approvals/prefetch from reused provider wire IDs, with protocol pairing preserved. Durable PG run/event transactions, pending login/role/version binding, consent and restart/revocation acceptance remain open |
| Notebook and ML execution boundaries | WP11 | Sandbox network/host/token tests; signed-model tampering and process isolation | NOT_STARTED |
| Role/session security | WP12 | Atomic role race, principal binding, active-admin gates; live governed regressions | IN_PROGRESS: Stage namespace aliasing reproduced and corrected on the local patched-FE/Ranger stack; active-role prefix gate and security-epoch UI state tested; atomic role/revocation and remaining domain gates still open |
| Single Compose, no .env | WP14 | Fresh start, upgrade, secrets checks, private ports, role health | NOT_STARTED |
| Recovery and observability | WP14–15 | Restore drill, external manifests, alerts, fault classification | NOT_STARTED |
| Safe one-way cutover | WP16–17 | Two rehearsals, aborted import, all-writer freeze, post-PG recovery | NOT_STARTED |
| Every feature, including live AI | WP18 | FEATURE_QA.csv plus runtime/API/SQL matrix; provider evidence | IN_PROGRESS: docs/UI/OpenAPI inventory and session-auth tests; embedding owner waiver |

A missing or skipped mandatory result remains open. Inventory presence, passing
unit tests or a generated Compose file cannot change final acceptance to PASS.
