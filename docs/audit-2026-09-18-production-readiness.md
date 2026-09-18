# Nova End-to-End Audit — Production-Grade Readiness (NOVA-103)

> **Type:** analysis / gap audit — no production code changed.
> **Auditor:** Nova DW Research & Roadmap.
> **Revision audited:** `2e851d9` (`origin/main`, 2026-09-18 21:37 +0700).
> **Open PRs considered:** #100, #101, #102, #103 (NOVA-94/102 in flight).
> **Rule applied:** a claim is `PROVEN` only with a code/test/PR artifact at the
> revision above. Otherwise it is `PARTIAL`, `SPEC-ONLY`, `STUB`, or `IN-FLIGHT`.
> **Out of scope (already covered):** NOVA-94/102 (implementation), NOVA-89/90/101
> (security fixes), NOVA-92/96 (benchmark + Phase 10 QA). Recorded, not re-audited.

---

## 1. Executive summary

1. **The roadmap is now an *underclaim*, not an overclaim, for the 7 HIGH gap
   items.** All 7 (`docs/gap-analysis.md`) remain unimplemented on `main`, but
   PRs #100/#101/#102/#103 close 5 of them (masking, row access, resource
   groups, variables, backup/recycle-bin). README Phase 6 still calls them
   "empty stub" — stale in the other direction.
2. **The README's own "only tick what the repo proves" rule is violated by a
   stale underclaim and a stale overclaim.** `external_catalogs` is marked
   `[ ] empty stub` (README:590, `roadmap-snowflake-parity.md:43`) but is fully
   implemented and registered (NOVA-62). Conversely `Phase 4 CTAS/export` is
   `[x]` while "no dedicated UI or end-to-end test yet" (README:570).
3. **The single largest production-grade gap is the SQL parse stage.** ANTLR4
   (60k-line generated parser) is vendored but wired only into `CREATE TASK`
   (`task_orchestration/ddl.py:47,136`). Every user query still parses via
   regex-on-text (`query/dialect/parser.py:76,159`), so the 8 NOVA-17 defects
   remain live. This blocks Phase 1 item #15 and every dialect-quality claim.
4. **`tables/` and `views/` DDL execute on the root/system connection**
   (`tables/router.py:101,153,170`; `views/router.py:65,95,113`), bypassing
   StarRocks RBAC — plausibly the same class of defect NOVA-89 closed for other
   routers; verify against NOVA-89's scope.
5. **Credential-leak surface shrank but two gaps remain**: `common/crypto.py`
   fails **open** (`:47` plaintext, `:64` ciphertext on error), and `SET ROLE`
   in the MySQL proxy is consumed locally and never audited
   (`proxy/executor.py:176-196`, `proxy/session.py:131-160`).

---

## 2. README `## Roadmap` reconciliation (Phase 1–11)

Rules: `PROVEN` = evidence at `2e851d9`; `PARTIAL` = code exists but stated
exit/completeness unmet; `STALE-UP` = marked `[ ]` but implemented;
`STALE-DOWN` = marked `[x]` but evidence contradicts.

| Phase | Claim | Verified state | Evidence |
|---|---|---|---|
| 1 Foundation & Auth | ✅ | **PROVEN** | `main.py`, `core/database.py:6`, `core/redis.py`, `common/sql_guard.py`, `docker/init-nova.sql:50-390` |
| 2 Query Engine | 🔶 | **PARTIAL** | `@stage` still regex (`query/dialect/parser.py:76,159`); `_normalize_default_schema_qualification` live (`query/service.py:1443,202,948`). `training_sql` claim at README:553 is **stale**: it now routes through `_prepare_user_sql` → `guard_user_statement` (`ml_engine/service.py:148-153,717,725`) |
| 3 Object Browser | ✅ | **PROVEN** | `explorer/router.py`, `objects/router.py`, `frontend/src/features/database-explorer/index.tsx` |
| 4 Stage & Storage | 🔶 | **PARTIAL** | Stage CRUD + files proven (`stages/router.py:31-130`). `CTAS/export` `[x]` but self-noted "no dedicated UI or e2e test" (`README:570`) → **STALE-DOWN**. Stage RBAC still unenforced (`stages/service.py`); no Stage Manager page |
| 5 Administration | 🔶 | **PARTIAL** | users/roles/functions/tasks/pipes proven. resource_groups `[ ]` → closed by PR #100/#102; cluster monitor `[ ]` → no backend module anywhere (see §5) |
| 6 Advanced Features | 🔶 | **STALE-UP + PARTIAL** | `external_catalogs` marked empty stub (`README:590`) but implemented + registered (`main.py:174-178`, NOVA-62) → **STALE-UP**. AI/ML/LLM proven. backup/governance/variables `[ ]` → PRs #100/#103 close them |
| 7 Frontend Pages | 🔶 | **PARTIAL** | Non-Stage Manager page absent; Cluster Monitor page absent (only 6 monitoring pages); Dashboards is static Home, not a builder. Admin Settings `[ ]` → PR #101 ships `/settings` |
| 8 MySQL Proxy | 🔶 | **PARTIAL** | Listener/handshake/COM_QUERY/`@stage`/user-vars/redaction proven (`proxy/connection.py`, `executor.py:309-331`; 29-test `test_mysql_proxy_cli.py`). No pool, no TLS, no prepared statements (`protocol.py:70-71`, `connection.py:386-400`) |
| 9 Task Orchestration | 9a/9b ✅, 9c `[ ]` | **PROVEN for 9a/9b** | `task_orchestration/` 19 files; `worker/`, `scheduler/`; PRs #37–#65. 9c stream providers not started (`README:663`) |
| 10 Agentic assistant | A–E `[x]`, F `[ ]` | **PROVEN A–E** | PRs #74/#77/#78/#81/#83; `assistant/` 15 files. 10-F benchmark = NOVA-92 (in_progress) → covered |
| 11 Migration Connector | (in docs 28, not README phases) | **PROVEN v1 read-only** | NOVA-85/PR #88; `migration/router.py`, execute gated out (`main.py:183-187`, `migration/engine.py:1-16`) |

**Net:** of 60 `[x]` marks, one is a stale overclaim (Phase 4 CTAS/export); of 23
`[ ]` marks, one is a stale underclaim (`external_catalogs`), and four more are
in flight via NOVA-94/102 rather than absent.

---

## 3. Spec-only vs implemented inventory (docs 01–28, `docs/specs/*`, proposals)

| Doc | Feature | Code state on `main` | Distinction |
|---|---|---|---|
| 01–19 | core features | implemented | — |
| **20** data-governance | masking + row access + tagging + lineage | **IN-FLIGHT (PR #100/#103)** masking + row access only; tagging/lineage **no code** | partial (2 of 4) |
| **21** backup-recovery | snapshot/restore/recycle | **IN-FLIGHT (PR #103)** on owner-connection; not merged | no code on `main` |
| **22** variables-settings | variables + password policy | **IN-FLIGHT (PR #102/#101)** | no code on `main` |
| **23** dashboards | charts/widgets | **SPEC-ONLY** (no PR); `dashboards/` empty; frontend `dashboard/` is static Home | no code |
| **24** advanced-indexes | inverted index / full-text | **SPEC-ONLY** (no PR) | no code |
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

---

## 4. `docs/gap-analysis.md` — 7 HIGH re-verification

| # | Feature | Was | State on `2e851d9` | Covered by |
|---|---|---|---|---|
| 1 | Backup & Restore | HIGH | **still unimplemented on `main`** (`backup/__init__.py` = 0 bytes) | PR #103 (NOVA-94) |
| 2 | Dynamic Data Masking | HIGH | **still unimplemented** (`governance/` empty) | PR #100/#103 (NOVA-94) |
| 3 | Row Access Policies | HIGH | **still unimplemented** | PR #100/#103 (NOVA-94) |
| 4 | Password Policies | HIGH | **still unimplemented** (`variables/` empty) | PR #101/#102 |
| 5 | Network Policies | HIGH | **still unimplemented**, no code/PR | — **unowned** |
| 6 | Inverted Index / full-text | HIGH | **still unimplemented**, no code/PR | — (roadmap #10, P3) |
| 7 | Session & Global Variables | HIGH | **still unimplemented** (`variables/` empty) | PR #101/#102 |

**Conclusion:** all 7 HIGH are still valid on `main` — none may be marked done.
5 are being closed by NOVA-94/102; **#5 network policies and #6 inverted index
have no owner** and should be promoted or explicitly deferred.

---

## 5. End-to-end readiness matrix per layer

### 5.1 Backend (FastAPI) — `backend/app/`

| Area | Production-grade | Not production-grade | Evidence |
|---|---|---|---|
| Auth / sessions | StarRocks-native auth, JWT+Redis, ACCOUNTADMIN guard | `SECRET_KEY`/`FERNET_KEY` defaults; ephemeral Fernet kills sessions on restart | `core/security.py:12,40-44,91-92`; `common/sql_guard.py:70-115` |
| Query pipeline | guard + audit + redaction + `@stage` translate | regex parser (8 NOVA-17 defects); position-regex normalizer | `query/sql_pipeline.py`; `query/dialect/parser.py:76,159`; `query/service.py:1443` |
| Objects/explorer | full tree, metadata, DDL | — | `objects/router.py`, `explorer/router.py` |
| Tables / Views | endpoints exist | **DDL runs as root/system, not caller** | `tables/router.py:101,153,170`; `views/router.py:65,95,113` |
| Stages | CRUD + files + secret_ref | **no stage-file RBAC**; no Stage Manager UI | `stages/service.py`; `README:571` |
| Users/Roles | full CRUD, ACCOUNTADMIN immutable | audit coverage gaps (see below) | `users/router.py:44-391` |
| External catalogs | Iceberg+Hive GA API+UI | Paimon/JDBC/Delta absent (matches NOVA-62 scope) | `external_catalogs/service.py`; `main.py:174-178` |
| ML engine | train/predict, guard on `training_sql` | 8 sklearn algos only; forecast/anomaly not shipped | `ml_engine/service.py:148-153`; `schemas.py:15-19` |
| LLM/AI functions | UDF registration, masked keys | placeholder-UDF fallback | `llm_functions/service.py:372-377,546` |
| Assistant | loop, delegate-first tool, consent, redaction, skills | — (benchmark = NOVA-92) | `assistant/` 15 files; PRs #74–#83 |
| Task orchestration | 9a/9b full, delegate-first, reconciler | 9c streams not started | `task_orchestration/`; `README:663` |
| **7 stubs** | — | backup, cluster, dashboards, governance, resource_groups, system, variables | each `__init__.py` = 0 bytes; not registered `main.py:49-55` |
| Audit | 14 call sites in privileged paths | no audit in users/functions/stages/pipes/tables/views/external_catalogs | `common/audit.py:10-58` |
| Crypto | encrypted provider keys | `crypto.py` **fails open** | `common/crypto.py:47,64` |
| Cross-cutting | — | `ml_intercept.py` dead code (ML_PREDICT never intercepted); `nova_system` swallows exceptions | `common/ml_intercept.py`; `common/nova_system.py:180-182` |

### 5.2 Engine / dialect — `backend/app/sql_dialect/` + `query/dialect/`

| Item | State | Evidence |
|---|---|---|
| ANTLR4 grammar vendored (4.1.4) | yes, generated Python committed | `sql_dialect/grammar/StarRocksParser.py` (60,620 lines), `pyproject.toml:25` |
| ANTLR wired into runtime | **only `CREATE TASK`** | `task_orchestration/ddl.py:47,136` |
| `@stage` parse | **regex-on-text** | `query/dialect/parser.py:76,159` |
| Grammar drift CI check | script exists | `backend/scripts/check_grammar_drift.py` |
| 8 NOVA-17 defects | **open** | `README:553` |

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
| Stage Manager, Cluster Monitor, Dashboards builder, Admin Settings | ❌ absent (Admin Settings in PR #101) | `sidebar-data.ts`; no route |
| Dead code | ❌ `features/tasks/` demo (zero importers); `features/_archive/pipes/` unreachable while `/pipes` backend live | `tasks/data/tasks.ts:1-27`; `main.py:171` |
| Token storage | ❌ JWT in non-HttpOnly/Secure/SameSite cookie → XSS theft | `lib/cookies.ts:33`, `stores/auth-store.ts:24` |
| Test coverage | ✅ 45 test files; ❌ no threshold, `routes/**` excluded | `vite.config.ts:40-48` |

---

## 6. Ambiguous phases / claims needing a decision

1. **Cluster Monitor split.** README Phase 5 `[ ]` "cluster monitor (FE/BE/CN, health)"
   vs Phase 7 `[ ]` "Cluster Monitor page"; neither has a backend module
   (`cluster/__init__.py` = 0 bytes). A frontend-only branch
   (`nova-94-cluster-monitor`) exists with **no backend** — a page with nothing
   to read. Decide: one Phase 5 backend + one Phase 7 page, or drop.
2. **Phase 2 `@stage` still downgraded.** NOVA-17 accepted ANTLR as the fix, but
   the runtime still uses regex; note README:553's `training_sql` sub-claim is now
   stale and should be re-scoped to the remaining defects only.
3. **Phase 4 CTAS/export `[x]`.** Self-admits no UI and no e2e test — either
   downgrade to `[ ]` or add the missing artifact.
4. **`external_catalogs` stale stub claim** in `README:590` and
   `roadmap-snowflake-parity.md:43,370` — update to "implemented (NOVA-62)".
5. **Gap HIGH #5 (network policies) and #6 (inverted index)** have no code and no
   owner PR — promote to issues or explicitly defer.

---

## 7. Items already covered elsewhere (do not duplicate)

- **NOVA-94/102** — implementation of spec docs: PR #100 (governance: masking +
  row access; resource groups), #101 (Admin Settings: variables + password
  policy), #102 (variables), #103 (backup + governance). In flight.
- **NOVA-89/90/101** — security fixes (DDL root escalation, internal ML endpoint,
  second-order SQLi in `tasks/service.py`).
- **NOVA-92/96** — Phase 10 benchmark + QA gate.
- **NOVA-97** — Phase 10 assistant security review.

## 8. Decisions needed from the team lead

1. **Ship or drop Phase 11?** PRs #100–#103 close 5 of 7 HIGH gaps but are
   unmerged; the README still says "empty stub". Merge them and mark Phase 5/6
   accordingly, or hold?
2. **Commit to the ANTLR parse migration (#15) now**, or patch the 8 regex
   defects first? This is the biggest single production-grade gap.
3. **Own gap HIGH #5 (network policies) and #6 (inverted index)** — create
   issues, or explicitly move them to `DEFER` in `gap-analysis.md`?
4. **`tables`/`views` root execution** — is this inside NOVA-89's scope, or a
   new security issue? (Coordinate with Nova Security Engineer's parallel audit.)
