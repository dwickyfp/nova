# Importer and Agent writer repair checkpoint

Updated 2026-11-13, Asia/Jakarta. Branch `feat/production-control-plane`, based on
`e0b5599448b26065bf84815ca07e0ead724d2d48`. The repository commit containing
this checkpoint identifies the delivered repair source; CI is pending.
No development cutover or backend/workload startup has occurred. A frontend-only
preview reached the login screen; authenticated docs inspection awaits cutover. Existing StarRocks
metadata and payloads have not been modified by import rehearsal.

## Implemented source

- Agent memory: actor-bound projection/revision/evidence transaction, explicit
  tombstone and relearning, Outcome linkage and scoped shared projection.
- Missions: PG/CAS with independent public and storage revisions, atomic audit
  and concurrent projection checks.
- Automations: durable immutable occurrence/job/outbox, fenced claims and
  completion, exact MCP delivery intent before the call and receipt afterward.
  Unknown outcomes remain blocked for verification without advancing the schedule.
- Scheduled Quality grants and task actor binding. Retired Agent helpers that
  mutated source metadata are removed; imported IDs and bindings are preserved.
- Reviewed import contract for 163 table entries / 1,578 source columns,
  timestamp provenance, strict conversion, immutable chunk receipts, resume,
  complete source/target row/key digests, managed payload verification and
  cross-row semantic checks. Epoch publication checks the committed PG receipt.
- Task role-binding import trigger correction in migration 0038. Application
  startup no longer attempts StarRocks audit DDL for Autopilot or migration
  metadata DDL for ingestion. Retired migration source CRUD helpers are removed.
- News story writes check scope ownership before comparing revisions, preserving
  the existing 404 refusal for a different owner/role/security context.
- Engine integration fixtures now provision their own PG metadata schema and
  real workload logins. Ingestion runs in a separate process; proxy pools belong
  to the proxy thread. Task fixtures use claims/generations and retain run history;
  ML fixtures retain reservation tokens. Seed/restart/fault cases remain CI-only.

## Evidence and retained failures

Detailed evidence files are private local artifacts under `/tmp`; this report
contains no exported source rows. A restricted persistent copy of source export
03 and PG provisioning files is stored outside the checkout under the local
Nova data directory. This local copy is not off-host recovery acceptance. Rehearsals use the existing dedicated PG17.11 TLS
fixture at port 55432, with ownership-checked schema/role cleanup, alongside the
unchanged `docker` development project. No second engine stack was created.

| Check | Observed result |
| --- | --- |
| Frozen source export 03 | 163 entries, 1,009,174 rows; no schema drift/uncontracted table; manifest SHA-256 `3c702a82c96416d8d4beb548a663c06c46e03230f31cb93daf31d77c6f19824e` |
| Full rehearsal attempts 01/02 | Failed during task role-binding import: shared trigger addressed an absent `provenance` column. First attempt exercised a committed-chunk interruption before this failure |
| Full rehearsal attempt 03 | PASS for row counts, key sets/digests and full row digests on the disposable target. This predates final receipt/payload/semantic hardening and does not satisfy the final two-rehearsal gate |
| Full rehearsal attempt 04 | PASS with committed-chunk interruption/resume, immutable validation receipt, 453 payload references and cross-row semantic checks. A subsequent missing-reference refusal was added; the final importer still requires repeat rehearsal |
| Read-only managed payload scan 01 | 453 references verified; receipts digest `a580d400b4e7afb7b2cb64e363ba9e19c765339c78ff1277768296c60973071e` |
| Agent/import PG suite 05 | 59 passed, including interrupted/resumed import, duplicate occurrences, ciphertext/timestamps, payload manifest recovery and forged epoch-publication refusal |
| Pure import suite 05 | 120 passed; the earlier truncated-read fixture used the correct length accidentally, then was corrected and rerun |
| Legacy Agent helper replacement | 45 unit tests passed |
| Agent scorecard 02 | 54/54 scenarios, 189/189 checks |
| Agent eval 02 → 03 | First attempt: 357 passed, one obsolete StarRocks memory fixture failed. Updated actor-bound trajectory: 358 passed |
| Full application mypy 07 | PASS, 733 source files |
| Engine collection 03 | 241 engine cases collected without the former 11 collection errors; execution is still required |
| Selected engine cases 01/02 | Earlier attempts retained fixture identity/Redis errors and a cross-scope 404/409 defect; fixes are under retest |
| Selected engine cases 03 | Skipped because the local wrapper used incorrect port/environment names. Not a pass; corrected wrapper is under retest |
| Full unit/coverage 02 | 11,132 passed, 64% coverage |
| Full unit/coverage 03 | 11,136 passed, 64% coverage, 525.99 seconds; ingestion startup was subsequently updated, so final run 04 is required |
| Migration startup/connector/preflight/retarget/data-mover unit tests | 149 passed, including PG-unavailable refusal without engine metadata fallback |
| Managed payload unit tests 06 | 9 passed, including missing active-file/version/READY-model references and incomplete reads |
| Agent eval 04 / scorecard 03 | 358 passed / 54 scenarios and 189 checks passed |
| Metadata test image build 01 | PASS; packaged importer contract included. Rebuild on final source remains required |
| Docs content tests 01 | 13 passed; running browser inspection remains pending |
| Selected engine cases 04 | 20 passed, one namespace fixture column-order failure; corrected for run 05 |
| Full unit/coverage 04 | 11,138 passed, 64% coverage; a subsequent News scope guard is covered by final run 05 |
| Full PG17 suite 01 | 958 passed in 1,190.74 seconds; the later disliked-message scope and importer checks also passed in selected run 07 |
| Selected engine cases 05 | 53 passed, 19 failed: stale Task workload/API fixtures, Ontology exception class, and a real News scope refusal defect |
| Selected engine cases 06 | 67 passed, five retired Task repository API failures |
| Selected engine cases 07 | 72 passed, no skips. Includes real PG Task metadata/lowering, News, Ontology, Agent feedback scope, importer and the safe original engine selection |
| Agent eval 05 / scorecard 04 | 358 passed / 54 scenarios and 189 checks passed |
| Full application mypy 08 / changed-file Ruff | PASS |
| Metadata image builds 02/03 | PASS, including the final News guard and test fixture changes |
| Full unit/coverage 05 | 11,138 passed, 64% coverage in 484.18 seconds on final application source |
| Engine collection 06 | 241 engine cases / 1,228 total, no collection errors |
| Owned diff / docs links / credential scan | PASS; checksum-bound import contracts preserve exact bytes across Git checkouts |
| Rehearsal 05/06 and new CI | IN PROGRESS / NOT RUN |

The deployment owner confirmed historical engine timestamps always used
`Asia/Jakarta`; reviewed Python UTC writers retain UTC conversion. Six Semantic
View timestamp columns are bound to reviewed source row digests. Unknown clocks
remain refusals, not implicit Jakarta defaults.

## Remaining delivery gates

1. Local code gates, full PG17, selected safe engine cases and the metadata image
   have passed as recorded above. Authenticated `/docs` browser inspection waits
   for the cutover runtime; the preview currently redirects to sign-in.
2. Complete two final rehearsals with committed-chunk interruption/resume on
   the final importer, including payload/semantic checks and immutable receipts.
3. Fetch/integrate remote main; commit and push owned changes. Follow CI to a
   passing conclusion. The owner authorizes destructive seeded integration in
   CI; it must not reseed this development stack.
4. Verify a final writer freeze and backup, export/import the final source,
   validate and publish the epoch. Provision stable private runtime credentials,
   run every development role through `dev.sh`, and smoke-test the migrated app.
5. Continue the feature QA matrix, live AI and fault acceptance. Large external
   PoC load/24h soak and live embedding tests retain only their recorded owner
   waivers. Durable interactive consent/agent separation, production Compose,
   ML/sandbox isolation and off-host recovery remain broader architecture work;
   this checkpoint does not claim those are complete.
