# Roadmap Radar Update — 2026-09-18 (post-NOVA-51)

> **Kind:** radar run on the durable tracker NOVA-49, updating
> `docs/roadmap-snowflake-parity.md` against verified reality.
> **Trigger:** human owner, 2026-09-18T10:18Z — *"Lanjutkan untuk research ini."*
> **Base inspected:** `origin/main` @ `0c55f14`.
> **Verification date:** 2026-09-18. Everything below was read from `origin/main` or a
> primary upstream source at that revision. Uncertain items are marked `[BELUM TERVERIFIKASI]`.

---

## 0. Why this update exists

The roadmap document merged in PR #59 described Phase 0 as *scheduled*. Since that
merge, **both Phase 0 items moved or landed**, and Phase 1's own status text went
stale. A roadmap whose value is accurate tracking must be corrected when reality
moves — this is that correction, prepared for the next scheduled radar run
(`Next due 2026-09-25`) or for immediate follow-up if the Lead prefers.

Nothing here is a new capability decision. It records state, and it flags two
places where the document now contradicts `main`.

---

## 1. Verified deltas since PR #59 merged

| # | What the roadmap says | What `origin/main` @ `0c55f14` shows | Source |
|---|---|---|---|
| **#2** | Phase 0 item, *"`4.1.1` → `4.1.4`"*, pending | **Landed.** `docker/docker-compose-engine.yml:122,159` now pin `starrocks/fe-ubuntu:4.1.4` / `be-ubuntu:4.1.4`. `NOVA-51` is `done`. | `git show origin/main:docker/docker-compose-engine.yml`; NOVA-51 state |
| **#2 grammar** | Bump must re-vendor grammar from a **recorded commit SHA** | **Satisfied.** `backend/app/sql_dialect/grammar/upstream/StarRocks.g4` on `main` is **byte-identical** to upstream tag `4.1.4` (3,331 lines, `diff` = 0), and `StarRocksLex.g4` likewise (639 lines, `diff` = 0). | `diff` of vendored file vs raw `StarRocks/starrocks@4.1.4` |
| **#2 drift check** | CI must guard the pinned SHA | **Present.** `backend/scripts/check_grammar_drift.py` gained a change in the bump commit (`525f624`), plus `backend/tests/unit/test_grammar_drift_check.py`. | `git show --stat 525f624` |
| **#2 regressions** | Full L3 task-reconciler suite green on the new image | **Evidenced.** `backend/tests/integration/test_engine_4_1_4_regressions.py` (181 lines) added; the bump PR cycle (#67) went through a QA FAIL then a fix on `d1bbd44` and merged. | `git show --stat 525f624`; `git log origin/main` |
| **#12** | *"9b … NOVA-54, **in flight**"* | **Stale.** 9b is complete and merged — PRs #57, #60, #61, #62, #64, #65, and `README.md` now states 9b is complete. 9c (stream providers) is not started. | `git show origin/main:README.md` (Phase 9 prose); `git log origin/main` |

**Net effect on the phase plan:** Phase 0 item #2 is **complete**. Phase 0 now
contains only #9 (external catalogs). #12's remaining scope is **9c only**.

---

## 2. The one open question the roadmap explicitly deferred

The roadmap's Phase 0 carries a revisit trigger:

> *"if the `#9` spike shows Iceberg catalog support is gated on an engine fix that
> only `4.1.3+` carries, then #9 waits behind NOVA-51 … The spike must state which
> engine fix, with a link."*

That trigger is now **resolvable, and it resolves in the permissive direction**:
the engine is already on `4.1.4`, so the "waits behind the bump" branch is moot
regardless of the answer. **#9 is unblocked and is the next schedulable item.**

What is **not** yet done is the spike itself — the evidence that StarRocks 4.1.4
supports the Iceberg/Hive catalog surface Nova would wrap, on Nova's
**shared-nothing** topology. I have **not** run that spike; claiming the outcome
would be exactly the kind of unverified status claim this project rejects. It is
listed as the top open item in §4.

---

## 3. The corrections — APPLIED in this PR

Decision by the Team Lead (2026-09-18, autopilot `288b503a`) folded these into
this same PR rather than waiting for the scheduled 2026-09-25 radar: leaving `#2`
marked pending and `#12` marked "in flight" after both landed is a false record —
the same defect class as NOVA-57.

Applied edits to `docs/roadmap-snowflake-parity.md` (the delta is the status
refresh only; no capability item is added, removed, or reordered, and no R1/R2/R3
text or MLflow guardrail is changed):

1. **Header** — engine pin line updated `4.1.1` → `4.1.4`, with a pointer to row #2.
2. **§0 table, R2** — marked executed/complete; the pin is now `4.1.4`.
3. **§2 Phase 0, row #2** — marked **COMPLETE** with evidence: compose pins
   `4.1.4` (`docker/docker-compose-engine.yml:122,159`), the vendored
   `upstream/StarRocks.g4` is byte-identical to upstream tag `4.1.4` (diff = 0),
   and `check_grammar_drift.py` + `test_engine_4_1_4_regressions.py` are in bump
   commit `525f624`; NOVA-51 `done`.
4. **§2 Phase 0 sequencing + R3 trigger** — rewritten as completed/settled; the
   spike is named as NOVA-60 and can now only amend the minimum engine version.
5. **§2 Phase 1, row #12** — "9b … in flight" → 9b complete and merged
   (PRs #57, #60, #61, #62, #64, #65; NOVA-54 `done`), remaining scope 9c only.
6. **§2 dependency summary + §6 provenance** — same two statuses corrected so the
   document has no remaining self-contradiction.

**Scope disclosure:** the Lead's instruction named "two cells". Five locations
plus the dependency block were touched, because the two statuses appear in more
than one place and correcting only the rows would have left the document
internally contradictory (`§0`'s R2 row and the provenance row both asserted a
pin that no longer exists). No cell beyond those two statuses was altered.


---

## 4. Open items for the next radar run

| Item | Why it is open | Owner-visible next step |
|---|---|---|
| **#9 readiness spike (top)** | The roadmap requires the spike to name any engine gating, with a link. Engine is now `4.1.4`, but the Iceberg/Hive catalog surface on shared-nothing has **not** been probed. | Run the spike on the live `4.1.4` stack: create an external Iceberg catalog, `SHOW CATALOGS`/`DESC`, query a table, verify credential redaction. Record the result as evidence. |
| **Matrix tracker metadata** | NOVA-49 metadata still reads `engine_pin: starrocks/fe-ubuntu:4.1.1` and `starrocks_head: 4.1.3`. Since NOVA-51, `main` is `4.1.4` and `4.1.4` also has a release page now. `[BELUM TERVERIFIKASI]` for the release-page status at this run. | Update the tracker's audit fields on the next scheduled run (2026-09-25). |
| **9c stream providers** | Remaining Phase 1 work for #12; depends on 9a/9b only. | Not authorised to start by this update. |
| **MLflow sidecar** | Proposal merged; implementation is a separate L. | Not authorised. NOVA-50 (secret providers) also stays backlog. |

---

## 5. What this update does **not** do

- It does **not** open, close, or re-scope any issue. NOVA-50, NOVA-51, and the
  MLflow implementation keep their existing status and scope.
- It does **not** claim the #9 spike passed. That spike has not been run; it is
  tracked separately as NOVA-60.
- It does **not** change any capability item, dependency edge, or the MLflow
  guardrails. The only substantive edits are the two status refreshments in §3.
- It does **not** re-open PR #59, which is merged at `02c1337`.

## 6. Provenance

| Claim | Source |
|---|---|
| Engine pin is `4.1.4` | `git show origin/main:docker/docker-compose-engine.yml:122,159` |
| Vendored grammar byte-identical to upstream `4.1.4` | `diff` of `origin/main:backend/app/sql_dialect/grammar/upstream/{StarRocks,StarRocksLex}.g4` vs raw `StarRocks/starrocks@4.1.4` → 0 lines |
| Bump commit contents (drift check, regressions, compose) | `git show --stat 525f624` |
| 9b complete; 9c not started | `git show origin/main:README.md` Phase 9 prose; `git log origin/main` |
| Roadmap Phase 0 / Phase 1 text and the #9 revisit trigger | `git show origin/main:docs/roadmap-snowflake-parity.md:75-103` |

**Not verified at this run:** whether `4.1.4` now has a GitHub release page (it did
not on 2026-09-18); the Iceberg/Hive catalog behaviour on `4.1.4` shared-nothing;
any MLflow runtime characteristic.
