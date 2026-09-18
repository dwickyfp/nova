# Nova End-to-End Audit — Production-Grade Readiness (NOVA-103)

> **Type:** analysis / gap audit — no production code changed.
> **Auditor:** Nova DW Research & Roadmap.
> **Revision audited:** `1a1c875` (`origin/main`, re-pinned 2026-09-19).
> **Open PRs considered:** #105 (this document). The NOVA-94/102 implementation
> PRs (#100, #101, #102, #103) and the security fixes (NOVA-89/101/104/105/106/108,
> PRs #92/#111/#112/#115/#118) have since **merged** and are re-verified below.
> **Rule applied:** a claim is `PROVEN` only with a code/test/PR artifact at the
> revision above. Otherwise it is `PARTIAL`, `SPEC-ONLY`, `STUB`, or `IN-FLIGHT`.
> **Out of scope (already covered):** NOVA-94/102 (implementation), NOVA-89/90/101
> (security fixes), NOVA-92/96 (benchmark + Phase 10 QA). Recorded, not re-audited.

---

## 1. Executive summary

1. **The roadmap is now an *underclaim*, not an overclaim, for the 7 HIGH gap
   items.** On `1a1c875` the implementations have **merged**: masking + row
   access (`governance/router.py`), resource groups (`resource_groups/router.py`),
   variables + password policy (`variables/router.py`) and backup/recycle-bin
   (`backup/router.py`) are all present and registered in `main.py:25,29,39,45`;
   inverted index shipped as `indexes/router.py` and network policies are
   **DEFERRED** in `docs/gap-analysis.md §5` (no `NETWORK POLICY` object in
   StarRocks 4.1.4). The README still calls several of these "empty stub" —
   stale in the other direction.
2. **The README's own "only tick what the repo proves" rule is violated by a
   stale underclaim and a stale overclaim.** `external_catalogs` is marked
   `[ ] empty stub` (README:590, `roadmap-snowflake-parity.md:43`) but is fully
   implemented and registered (NOVA-62). Conversely `Phase 4 CTAS/export` is
   `[x]` while "no dedicated UI or end-to-end test yet" (README:570).
3. **The single largest production-grade gap is the SQL parse stage.** ANTLR4
   (60k-line generated parser) is vendored but on `main` wired only into
   `CREATE TASK` (`task_orchestration/ddl.py:47,136`). Every user query still
   parses via regex-on-text (`query/dialect/parser.py`), so the remaining NOVA-17
   defects stay live — the swap (NOVA-126/132) is on an **unmerged branch**, not
   on `main` at `1a1c875`. This blocks Phase 1 item #15 and every
   dialect-quality claim.
4. **`tables/` and `views/` DDL now run on the caller's connection — CLOSED
   (NOVA-89).** Both routers use `get_user_connection` and `check_identifier`
   (`tables/router.py:26,34,86,114`; `views/router.py:24,32,102,129,178`), so
   StarRocks RBAC — not the root pool — decides whether the operation is
   permitted. The allow-list module is `common/identifiers.py`. Same pattern in
   the new `indexes/router.py`. No open gap here.
5. **Credential/identity surface: two of the three named gaps are now closed.**
   `common/crypto.py` is **fail-closed — CLOSED (NOVA-108)**: `encrypt` raises
   `EncryptionError("refusing to store plaintext")` and `decrypt` refuses to
   return ciphertext; the entrypoints require explicit keys via
   `common/secret_keys.py`. `monitoring` `/queries/kill` is **CLOSED (NOVA-105)**:
   every endpoint is gated with `require_role(*READ_ROLES/*KILL_ROLES)`
   (`monitoring/router.py:26,53-54`). Still **open**: `SET ROLE` in the MySQL
   proxy is consumed locally and never audited (`proxy/executor.py:176-196`,
   `proxy/session.py:131-160`).

---

## 2. README `## Roadmap` reconciliation (Phase 1–11)

Rules: `PROVEN` = evidence at `1a1c875`; `PARTIAL` = code exists but stated
exit/completeness unmet; `STALE-UP` = marked `[ ]` but implemented;
`STALE-DOWN` = marked `[x]` but evidence contradicts.

| Phase | Claim | Verified state | Evidence |
|---|---|---|---|
| 1 Foundation & Auth | ✅ | **PROVEN** | `main.py`, `core/database.py:6`, `core/redis.py`, `common/sql_guard.py`, `common/secret_keys.py` (`require_configured_secrets`), `docker/init-nova.sql:50-390` |
| 2 Query Engine | 🔶 | **PARTIAL** | `@stage` parse state is contested in the README itself (`README:563`: ANTLR4 landed on an unmerged branch, with the regex parser still on `main`); NOVA-126/132 are **not** on `main` at `1a1c875`. `_normalize_default_schema_qualification` remains live (`query/service.py:1443,202,948`). `training_sql` claim at README:553 is **stale**: it routes through `_prepare_user_sql` → `guard_user_statement` (`ml_engine/service.py:148-153,717,725`) |
| 3 Object Browser | ✅ | **PROVEN** | `explorer/router.py`, `objects/router.py`, `frontend/src/features/database-explorer/index.tsx` |
| 4 Stage & Storage | 🔶 | **PARTIAL** | Stage CRUD + files proven (`stages/router.py:31-130`); `frontend/src/features/stages/` + `routes/_authenticated/stages/index.tsx` now exist. CTAS/export still `[ ]` in README with "no dedicated UI or e2e test". **Open:** stage-file RBAC still unenforced (`stages/service.py` — carries `database_name`/`schema_name` but no privilege check) |
| 5 Administration | 🔶 | **PARTIAL** | users/roles/functions/tasks/pipes proven. **resource_groups now IMPLEMENTED** (`resource_groups/{router,service,schemas}.py`, registered `main.py:39`) — README `[ ]` is **STALE-UP**. cluster monitor `[ ]` → still no backend module (`cluster/` is `__init__.py` only) |
| 6 Advanced Features | 🔶 | **STALE-UP + PARTIAL** | `external_catalogs` marked empty stub but implemented + registered (`main.py:27,124`) → **STALE-UP**. AI/ML/LLM proven. **backup, governance and variables now IMPLEMENTED and registered** (`main.py:25,29,45`); README `[ ]` is **STALE-UP** |
| 7 Frontend Pages | 🔶 | **PARTIAL** | Stage Manager now **exists** (`frontend/src/features/stages/` + route) → README `[ ]` **STALE-UP**; Admin Settings `/settings` now exists (`routes/_authenticated/settings/index.tsx`) → **STALE-UP**; Cluster Monitor page exists (`routes/_authenticated/monitoring/cluster.tsx`) but has no backend (see §6); Dashboards is static Home, not a builder |
| 8 MySQL Proxy | 🔶 | **PARTIAL** | Listener/handshake/COM_QUERY/`@stage`/user-vars/redaction proven (`proxy/connection.py`, `executor.py:309-331`; 29-test `test_mysql_proxy_cli.py`). No pool, no TLS, no prepared statements (`protocol.py:70-71`, `connection.py:386-400`) |
| 9 Task Orchestration | 9a/9b ✅, 9c `[ ]` | **PROVEN for 9a/9b** | `task_orchestration/` 19 files; `worker/`, `scheduler/`; PRs #37–#65. 9c stream providers not started (`README:663`) |
| 10 Agentic assistant | A–E `[x]`, F `[ ]` | **PROVEN A–E** | PRs #74/#77/#78/#81/#83; `assistant/` 15 files. 10-F benchmark = NOVA-92 (in_progress) → covered |
| 11 Migration Connector | (in docs 28, not README phases) | **PROVEN v1 read-only** | NOVA-85/PR #88; `migration/router.py`, execute gated out (`main.py:183-187`, `migration/engine.py:1-16`) |

**Net:** of 60 `[x]` marks, one is a stale overclaim (Phase 4 CTAS/export); of 23
`[ ]` marks, several are stale underclaims — `external_catalogs`,
`resource_groups`, `backup`, `governance`, `variables` are implemented and
registered on `main`, and the Stage Manager / Admin Settings pages now exist. The
remaining README staleness is a README problem, recorded here, not an open gap in
the audit's own sense.

---

## 3. Spec-only vs implemented inventory (docs 01–28, `docs/specs/*`, proposals)

| Doc | Feature | Code state on `main` | Distinction |
|---|---|---|---|
| 01–19 | core features | implemented | — |
| **20** data-governance | masking + row access + tagging + lineage | **IMPLEMENTED** masking + row access (`governance/router.py`, registered `main.py:29`); tagging/lineage **no code** | partial (2 of 4) |
| **21** backup-recovery | snapshot/restore/recycle | **IMPLEMENTED** (`backup/router.py`, registered `main.py:25`); runs on the caller's connection via `BackupAdmin` gate | merged |
| **22** variables-settings | variables + password policy | **IMPLEMENTED** (`variables/router.py`, registered `main.py:45`); Admin Settings UI at `/settings` | merged |
| **23** dashboards | charts/widgets | **SPEC-ONLY** (no PR); `dashboards/` empty; frontend `dashboard/` is static Home | no code |
| **24** advanced-indexes | inverted index / full-text | **IMPLEMENTED backend** (NOVA-111, `indexes/router.py`, registered `main.py:30`); UI tab deferred | API is the contract |
| **25** storage-volumes | shared-data volumes | **SPEC-ONLY** (no PR) | no code |
| **26** compaction-manager | manual compaction | **SPEC-ONLY** (no PR) | no code |
| **27** data-sharing | sharing/marketplace | **SPEC-ONLY**, deferred by roadmap §5 (#18) | no code |
| **28** migration-connector | assessment/dry-run | **IMPLEMENTED v1** (NOVA-85) | read-only by design |
| `specs/nova-23-*` | task orchestration | **IMPLEMENTED** 9a/9b; 9c not started | partial |
| `specs/nova-61-*` | agentic assistant | **IMPLEMENTED** A–E; F benchmark pending | partial |
| `specs/2026-06-18-backend-architecture-plan.md` | backend architecture | **superseded** (module tree is `app/modules/*`, not `services/`) | stale spec |
| `specs/2026-06-18-sidebar-draft.md` | sidebar | superseded by `sidebar-data.ts` | stale spec |
| `proposals/mlflow-integration.md` | MLflow registry (#13) | **SPEC-ONLY** (adopted R1, not implemented) | no code |
| `roadmap-snowflake-parity.md` | 22 items | Phase 0 #9 done (NOVA-62); #2 done (NOVA-51). §1 line 43/370 stale (`external_catalogs` empty) | partly stale |

Legend: **SPEC-ONLY** = doc exists, no code, no PR. **IN-FLIGHT** = code on an
open PR branch, not on `main`. **IMPLEMENTED** = merged on `main`.

**Note on NOVA-126/132 (`@stage` ANTLR swap):** the branch is *not* on `main` at
`1a1c875` — README:563 says so explicitly. It is the one substantial item still
in flight; it is recorded as in-flight, not implemented.

---

## 4. `docs/gap-analysis.md` — 7 HIGH re-verification

| # | Feature | Was | State on `1a1c875` | Covered by |
|---|---|---|---|---|
| 1 | Backup & Restore | HIGH | **CLOSED — implemented** (`backup/{router,service,schemas}.py`, registered `main.py:25`) | NOVA-94, PR #103 (merged) |
| 2 | Dynamic Data Masking | HIGH | **CLOSED — implemented** (`governance/router.py` masking endpoints, registered `main.py:29`) | NOVA-94, PR #100/#103 (merged) |
| 3 | Row Access Policies | HIGH | **CLOSED — implemented** (`governance/router.py` row-access endpoints) | NOVA-94, PR #100/#103 (merged) |
| 4 | Password Policies | HIGH | **CLOSED — implemented** (`variables/router.py` `/password-policy`, registered `main.py:45`) | PR #101/#102 (merged) |
| 5 | Network Policies | HIGH | **DEFERRED** — no code; engine 4.1.4 has no `NETWORK POLICY` object | `docs/gap-analysis.md §5` (explicitly deferred) |
| 6 | Inverted Index / full-text | HIGH | **CLOSED (backend)** — `indexes/router.py`, registered `main.py:30`; UI tab deferred | NOVA-111 |
| 7 | Session & Global Variables | HIGH | **CLOSED — implemented** (`variables/router.py`) | PR #101/#102 (merged) |

**Conclusion:** on `1a1c875`, 5 of the 7 HIGH items are implemented and merged
(#1–#4, #7), inverted index has shipped its backend (#6), and network policies
(#5) are explicitly DEFERRED with an engine-capability rationale — not an
unowned gap. No HIGH item is missing an owner.

---

## 5. End-to-end readiness matrix per layer

### 5.1 Backend (FastAPI) — `backend/app/`

| Area | Production-grade | Not production-grade | Evidence |
|---|---|---|---|
| Auth / sessions | StarRocks-native auth, JWT+Redis, ACCOUNTADMIN guard; **fail-closed required secrets** | ephemeral Fernet kills sessions on restart | `core/security.py`; `common/secret_keys.py:100-115` (`require_configured_secrets`); `common/sql_guard.py:70-115` |
| Query pipeline | guard + audit + redaction + `@stage` translate | `@stage` still regex on `main` (ANTLR swap NOVA-126/132 unmerged); position-regex normalizer | `query/sql_pipeline.py`; `query/dialect/parser.py`; `query/service.py:1443` |
| Objects/explorer | full tree, metadata, DDL | — | `objects/router.py`, `explorer/router.py` |
| Tables / Views | endpoints exist; **DDL runs on the caller's connection** | — (NOVA-89 closed the root-execution gap) | `tables/router.py:26,34,86,114`; `views/router.py:24,32,102,129,178`; `common/identifiers.py` |
| Stages | CRUD + files + secret_ref; **Stage Manager UI exists** | **no stage-file RBAC** | `stages/service.py`; `frontend/src/features/stages/` |
| Users/Roles | full CRUD, ACCOUNTADMIN immutable | audit coverage gaps (see below) | `users/router.py:44-391` |
| External catalogs | Iceberg+Hive GA API+UI | Paimon/JDBC/Delta absent (matches NOVA-62 scope) | `external_catalogs/service.py`; `main.py:27,124` |
| ML engine | train/predict **as the caller** (NOVA-104, 401 fail-closed); guard on `training_sql` | 8 sklearn algos only; forecast/anomaly not shipped | `ml_engine/router.py:43-55`; `service.py:163-211` |
| LLM/AI functions | UDF registration, masked keys | placeholder-UDF fallback | `llm_functions/service.py:372-377,546` |
| Assistant | loop, delegate-first tool, consent, redaction, skills | — (benchmark = NOVA-92) | `assistant/` 15 files; PRs #74–#83 |
| Task orchestration | 9a/9b full, delegate-first, reconciler | 9c streams not started | `task_orchestration/`; `README:663` |
| **Remaining stubs** | — | `cluster`, `dashboards`, `system` only | each `__init__.py` = 0 bytes; not registered `main.py:50-57`. **backup/governance/resource_groups/variables/indexes are no longer stubs** — implemented and registered (`main.py:25,29,30,39,45`) |
| Audit | 14 call sites in privileged paths | no audit in users/functions/stages/pipes/tables/views/external_catalogs | `common/audit.py:10-58` |
| Crypto | encrypted provider keys; **fail-closed** | — (NOVA-108 closed the fail-open) | `common/crypto.py:45-86` (`EncryptionError`, refuses plaintext/ciphertext) |
| Monitoring | **every endpoint role-gated** (NOVA-105) | — | `monitoring/router.py:26,53-54` (`require_role(*READ_ROLES/*KILL_ROLES)`) |
| Cross-cutting | — | `ml_intercept.py` dead code (ML_PREDICT never intercepted); `nova_system` swallows exceptions | `common/ml_intercept.py`; `common/nova_system.py:180-182` |

### 5.2 Engine / dialect — `backend/app/sql_dialect/` + `query/dialect/`

| Item | State | Evidence |
|---|---|---|
| ANTLR4 grammar vendored (4.1.4) | yes, generated Python committed | `sql_dialect/grammar/StarRocksParser.py` (60,620 lines), `pyproject.toml:25` |
| ANTLR wired into runtime | **only `CREATE TASK`** on `main`; the `@stage` parse swap (NOVA-126/132) is unmerged | `task_orchestration/ddl.py:47,136` |
| `@stage` parse | **regex-on-text** on `main` (ANTLR on the unmerged branch) | `query/dialect/parser.py` |
| Grammar drift CI check | script exists | `backend/scripts/check_grammar_drift.py` |
| Remaining NOVA-17 defects | **open as tracked, narrower than at first audit** — the `external_catalogs` stub, `default-schema` normalizer and `CREATE ML_MODEL ... FORECAST` sub-claims are closed; the parse swap itself is not on `main` | `README:563` |

### 5.3 MySQL proxy — `backend/app/proxy/`

| Item | State | Evidence |
|---|---|---|
| Listener 4406, handshake, COM_QUERY, auth relay | ✅ | `server.py:72-76`, `protocol.py:338-368`, `auth.py:204-258` |
| `@stage` translate + user vars + redaction + NOVA_SYSTEM hide | ✅ | `executor.py:203-213,191-192,309-331,233-273` |
| Audit | ✅ via QueryService | `query/service.py` audit sites |
| Prepared statements refused | ✅ `ER_NOT_SUPPORTED_YET` | `connection.py:386-400` |
| No connection pool | ❌ | `auth.py:146-199` |
| No TLS | ❌ (plaintext, nginx passthrough documented) | `protocol.py:70-71`; `README.md:146-151` |
| `SET ROLE` not audited | ❌ | `executor.py:176-196`; `session.py:131-160` |
| Type fidelity loss (int→BIGINT) | ❌ | `executor.py:93-140` |
| E2E test | ✅ 29 tests | `tests/integration/test_mysql_proxy_cli.py` |

### 5.4 Frontend — `frontend/src/`

| Item | State | Evidence |
|---|---|---|
| Workspaces, explorer, users/roles, ai-providers, ml-models, functions, monitoring, assistant, external-catalogs, migration, tasks-manager, task-graphs | ✅ implemented | `features/*`, `routes/_authenticated/*` |
| Stage Manager ✅, Admin Settings ✅, Cluster Monitor ⚠️ (page exists, no backend), Dashboards builder ❌ | partial | `features/stages/` + `routes/_authenticated/stages/index.tsx`; `features/admin-settings/` + `routes/_authenticated/settings/index.tsx`; `features/cluster/` + `routes/_authenticated/monitoring/cluster.tsx` (no `cluster/` backend); `features/dashboard/` is static Home |
| Dead code | ❌ `features/tasks/` demo (zero importers); `features/_archive/pipes/` unreachable while `/pipes` backend live | `tasks/data/tasks.ts:1-27`; `main.py` pipes router |
| Token storage | ❌ JWT in non-HttpOnly/Secure/SameSite cookie → XSS theft | `lib/cookies.ts:33`, `stores/auth-store.ts:24` |
| Test coverage | ✅ 45 test files; ❌ no threshold, `routes/**` excluded | `vite.config.ts:40-48` |

---

## 6. Ambiguous phases / claims needing a decision

1. **Cluster Monitor split.** README Phase 5 `[ ]` "cluster monitor (FE/BE/CN, health)"
   vs Phase 7 `[ ]` "Cluster Monitor page". The **frontend page now exists**
   (`features/cluster/` + `routes/_authenticated/monitoring/cluster.tsx`), but no
   backend module does (`cluster/` is `__init__.py` only) — a page with nothing
   to read. Decide: build one Phase 5 backend + keep one Phase 7 page, or drop.
2. **Phase 2 `@stage` still downgraded on `main`.** NOVA-17 accepted ANTLR as the
   fix; the swap (NOVA-126/132) is on an unmerged branch, so the runtime still
   uses regex at `1a1c875`. README:563's `training_sql` sub-claim is now stale
   and should be re-scoped to the remaining defects only.
3. **Phase 4 CTAS/export `[x]`.** The README itself downgraded this to `[ ]`
   ("no dedicated UI or e2e test") — the audit agrees; add the missing artifact
   or leave lowered.
4. **`external_catalogs` stale stub claim** in the README and
   `roadmap-snowflake-parity.md:43,370` — update to "implemented (NOVA-62)".
5. **BACKLOG — README staleness for the now-merged modules.** `resource_groups`,
   `backup`, `governance`, `variables`, `indexes` are implemented and registered
   (`main.py:25,29,30,39,45`) but the README still marks them `[ ] empty stub`.
   This is a README reconciliation task, not an open engineering gap.

---

## 7. Items already covered elsewhere (do not duplicate)

- **NOVA-94/102** — implementation of spec docs, now **merged**: PR #100
  (governance: masking + row access; resource groups), #101 (Admin Settings:
  variables + password policy), #102 (variables), #103 (backup + governance),
  plus NOVA-111 (indexes).
- **NOVA-89/90/101/104/105/106/108/118** — security fixes, now **merged** (DDL
  root escalation, internal ML endpoint, second-order SQLi in `tasks/service.py`,
  ML train/predict run-as-caller, monitoring RBAC, stage object-key traversal,
  crypto fail-closed, batch prediction run-as-caller).
- **NOVA-92/96** — Phase 10 benchmark + QA gate.
- **NOVA-97** — Phase 10 assistant security review.

## 8. Decisions needed from the team lead

1. **README reconciliation.** PRs #100–#103/#111 have merged and 5 of 7 HIGH gaps
   are closed, but the README still marks them "empty stub". Approve a README
   pass to mark them done (this document records the discrepancy; the README is a
   separate deliverable)?
2. **Commit to the ANTLR parse migration (#15) now** — i.e. merge the NOVA-126/132
   branch — or keep patching the regex defects first? This is the largest single
   remaining production-grade gap on `main`.
3. **Gap HIGH #5 (network policies) and #6 (inverted index)** — #6 has shipped its
   backend under NOVA-111; #5 is DEFERRED in `gap-analysis.md §5` for lack of an
   engine object. Confirm the deferral stands, or re-scope.
4. **Stage-file RBAC** — the one security gap this audit re-verified as still
   open (`stages/service.py`): promote to an issue, or accept the current scope?
