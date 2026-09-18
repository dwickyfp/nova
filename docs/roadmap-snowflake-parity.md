# Nova Roadmap — Snowflake Parity, Phased Execution, and Open-Source Integration

> **Status:** approved for execution by the Team Lead (autopilot `288b503a`) on 2026-09-18.
> **Owner:** Nova DW Research & Roadmap. Decisions are recorded; this document is the
> artifact the team executes against.
> **Matrix source:** NOVA-49 *Nova Snowflake Capability Matrix* (canonical tracker).
> **Engine pin at the time of writing:** `starrocks/fe-ubuntu:4.1.1` — unchanged by this document.
> **Every factual claim below was verified against a primary source on 2026-09-18.** Claims that
> could not be verified are marked `[BELUM TERVERIFIKASI]` and are never load-bearing.

---

## 0. What this document decides

Three open decisions were answered by the Team Lead and are now binding. They are
reproduced here because every phase below depends on them.

| # | Decision | Consequence for this roadmap |
|---|---|---|
| **R1** | **MLflow is adopted as the ML registry.** Nova stops building a native registry. | Row 13 is re-worded `PARTIAL → EMBED_OPEN_SOURCE (MLflow)`. Phase 3 carries the integration. Full proposal in §3. |
| **R2** | **The engine pin stays on tag `4.1.1` through Phase 9b.** The bump is a separate, sequenced task. | Engine bump (#2 / NOVA-51) is Phase 0 but must not touch the 9b critical path. It pins to a **commit SHA**, not a tag. |
| **R3** | **Phase 0 = #2 + #9. #9 leads Phase 1 as a parallel track.** #9 is not a hard predecessor of #3/#4. | The dependency table in §2 encodes this: #9 depends on nothing, and #3/#4/#7/#16/#15/#12 do not depend on #9. |

**Principle that survives every decision above:** the data format stays open.
`EMBED_OPEN_SOURCE` is approved for components whose data can leave again
(MLflow, Trino, OpenMetadata). Engines that lock data in (Snowflake, Databricks
SQL, BigQuery, Redshift) are not integration candidates.

---

## 1. Corrections applied to the matrix

The matrix in NOVA-49 was written from a 2026-09-18 radar run. Three rows were
found to be wrong or stale and are corrected here. The canonical matrix issue
should be updated to match.

| Matrix row | Was recorded as | Verified value (2026-09-18) | Source |
|---|---|---|---|
| Engine line head (#2) | `4.1.3`; "no 4.2 exists" | **`4.1.4` exists.** `4.1.4` was tagged but has no GitHub release page yet (`releases/tags/4.1.4` → 404). `4.1.3` is the newest *released* page. | `api.github.com/repos/StarRocks/starrocks/tags`; `.../releases?per_page=40` |
| Grammar drift | not recorded | `StarRocks.g4` line count moves **4.1.1 = 3,309 → 4.1.3 = 3,318 → `branch-4.1` = 3,331**. `StarRocksLex.g4` gains `SKIP_KW` at 4.1.3. **No `FINALIZE`, `CRON`, `OVERLAP_POLICY`, or `ALLOW_OVERLAPPING` token exists in any of them.** `submitTaskStatement` is byte-identical across all three. | raw `StarRocks.g4` / `StarRocksLex.g4` at tags `4.1.1`, `4.1.3`, `branch-4.1`, diffed locally |
| Row 13 (ML registry / AI SQL) | `SHIPPED`, `NOVA_NATIVE + WRAP` | **Overclaim.** Shipped today is `ai_query` plus 8 sklearn algorithms. `model_type` accepts only `classification\|regression` (`backend/app/modules/ml_engine/schemas.py:15-19`). No distributed training, no GPU, no model serving, no model signature, no cross-run lineage. | repo read at `9bb2a87` |
| Row 9 (external catalogs) | `MISSING`, empty stub | **Confirmed.** `backend/app/modules/external_catalogs/` contains only `__init__.py`. No Iceberg / Delta / Hive / Paimon / JDBC code path exists. The only route to open table formats today is `FILES()` over a stage. | repo read at `9bb2a87` |

### What the grammar-drift finding does and does not change

- **Does not change:** the 9b patch itself. `taskClause*` is still the extension
  point and `submitTaskStatement` is unchanged between 4.1.1 and `branch-4.1`.
  The in-flight NOVA-54 work on the `4.1.1` baseline remains valid.
- **Does change:** the bump procedure. Because the grammar moves between patch
  tags, NOVA-51 must record the **commit SHA** it re-vendors from, and the CI
  drift check must compare against that SHA. A moving tag is not an acceptable
  pin. See §2, Phase 0.

---

## 2. Phased roadmap — the 22 remaining items

Item numbers are the matrix rows in NOVA-49. **Two `SHIPPED` rows are excluded:**
row 22 (Snowpipe elastic channels / Routine Load) and row 14 (MySQL wire
protocol). That is 24 matrix rows minus 2, leaving 22 items scheduled below.

Legend — **Strategy:** `WRAP_STARROCKS` (engine already has it; Nova exposes it)
· `COMPOSE_STARROCKS` (engine plus an adjacent component) · `NOVA_NATIVE`
(Nova builds it) · `EMBED_OPEN_SOURCE` (adopt an external component) ·
`ENGINE_EXTENSION` (patch the engine grammar) · `DEFER` (not scheduled).
**Effort:** S / M / L. **Priority:** P0 / P1 / P2 / P3.

### Phase 0 — Foundation (runs alongside the 9b critical path)

Purpose: remove the two conditions that make later phases expensive — an engine
behind on security and grammar, and no route to open table formats.

| Item | Capability | Strategy | Effort | Prio | Depends on | Exit criteria (measurable) |
|---|---|---|---|---|---|---|
| **#2** | Engine version currency (`4.1.1` → `4.1.4`) | `WRAP_STARROCKS` | S | P1 | 9b stages 1–4 merged | Bump PR re-vendors the grammar from a **recorded commit SHA**; CI drift check passes against that SHA; `information_schema.task_runs` read path holds the predicate-escape fix; full L3 task-reconciler suite green on the new image. Tracked as **NOVA-51** — scope unchanged. |
| **#9** | External catalogs (Iceberg, Delta, Hive, Paimon, JDBC) | `WRAP_STARROCKS` | M | P1 | nothing | At least **Iceberg + Hive** catalog create/alter/drop through Nova API and UI; a query against an external table returns rows; catalog credentials stay out of every API response, log, and `NOVA_SYSTEM` row; external table appears in the catalog tree. |

**Sequencing constraint (R2):** #2 must not start before 9b stages 1–4 have
merged. Its own QA runs on its own head. Do **not** roll the bump into a 9b PR.

**Revisit trigger (R3):** if the #9 spike shows Iceberg catalog support is gated
on an engine fix that only `4.1.3+` carries, then #9 waits behind NOVA-51 and
this document is amended. The spike must state which engine fix, with a link.

### Phase 1 — Core parity (starts in parallel with Phase 0)

Purpose: close the gaps where the engine **already ships the feature** and only
Nova is missing the surface. This is the highest ratio of user-visible value to
engineering risk: no new component, no new dependency, no open decision.

| Item | Capability | Strategy | Effort | Prio | Depends on | Exit criteria (measurable) |
|---|---|---|---|---|---|---|
| **#3** | Dynamic data masking | `WRAP_STARROCKS` | M | P1 | nothing | `CREATE MASKING POLICY` CRUD through Nova; column binding via `ALTER TABLE … SET MASKING POLICY`; a low-privilege user querying the bound column receives masked values and an admin receives raw values, both asserted by test; every policy change writes `NOVA_SYSTEM.AUDIT_LOG`. |
| **#4** | Row access policies | `WRAP_STARROCKS` | M | P1 | nothing | Policy CRUD; table binding; two users with different roles see different row sets on the same table, asserted by test; write path audited. |
| **#7** | Backup / restore / time travel | `WRAP_STARROCKS` | M | P1 | nothing | `BACKUP SNAPSHOT` / `RESTORE SNAPSHOT` driven from Nova; snapshot list; recycle-bin browser with recovery; a dropped table is recovered and its row count matches pre-drop, asserted by an L3 test. |
| **#16** | Resource groups / warehouses | `WRAP_STARROCKS` | M | P1 | nothing | Resource group CRUD + classifier configuration; a query routed to a group is attributed to it in `SHOW` output, asserted by test; the group's quota is enforced by the engine, not simulated by Nova. |
| **#15** | Stage-native `@stage` file access | `NOVA_NATIVE` | M | P0 | nothing | All 8 open defects from NOVA-17 closed; `@stage` parse is grammar-based, not regex; the `ml_engine` `training_sql` path runs through the guard, the `@stage` translator, credential injection, and redaction; regression tests for each defect. |
| **#12** | Task DAG / cron / streams | `NOVA_NATIVE` | L | P0 | nothing | **9a is already complete and merged.** Remaining: 9b (grammar + lowering + graph UI, NOVA-54, in flight) and 9c (stream providers). Phase 1 closes 9b; 9c follows. |

**Why #3/#4/#7/#16 can start immediately:** all four are `WRAP_STARROCKS` over
statements the engine already accepts, with no dependency on #9 or on the engine
bump. Serialising them behind #9 would add calendar time without removing risk.

### Phase 2 — Governance

Purpose: build the Nova-native layers that have no engine primitive. These
depend on Phase 1 surfaces because they attach to the objects those phases
create (tags on columns, lineage on the audit trail).

| Item | Capability | Strategy | Effort | Prio | Depends on | Exit criteria (measurable) |
|---|---|---|---|---|---|---|
| **#5** | Object tagging | `NOVA_NATIVE` | M | P2 | #3 or #4 | Tag CRUD on table/column/schema; tag-based search returns the tagged objects; a tag can drive a masking policy without a second binding step; `CONFIG_OBJECT_TAGS` moves from unused to live. |
| **#6** | Data lineage | `NOVA_NATIVE` | L | P2 | #3, #4 | Table→table edges derived from the audit trail; a DAG view renders them; column-level lineage for at least direct `INSERT … SELECT`; "what breaks if I drop this" returns the dependent set; `LINEAGE_LOAD_HISTORY` moves from unused to live. |
| **#21** | Per-user quotas / cost attribution | `WRAP_STARROCKS` | M | P2 | #16 | Cost per user and per query, derived from audit + resource-group attribution; the existing query-cost page reads the real attribution; a query's cost is reproducible from stored data. |
| **#8** | Session / global variables browser | `WRAP_STARROCKS` | S | P2 | nothing | `SHOW VARIABLES` / `SHOW GLOBAL VARIABLES` browse, search, paginate; session-vs-global toggle; `SET` with validation; `variables/` stub replaced by a real module. |

### Phase 3 — Open-source integration

Purpose: this is where the "one complete ecosystem" goal lands. Everything here
is an `EMBED_OPEN_SOURCE` or `COMPOSE_STARROCKS` decision and every component
must keep Nova's data in an open format.

| Item | Capability | Strategy | Effort | Prio | Depends on | Exit criteria (measurable) |
|---|---|---|---|---|---|---|
| **#13** | ML registry / AI SQL functions | `EMBED_OPEN_SOURCE` (MLflow) | L | P1 | #9 (for artifact-format parity), Phase 2 audit | Full proposal in §3. `CREATE ML_MODEL` resolves a version through an **MLflow model URI**; `CONFIG_*` holds only the pointer; MLflow runs as a sidecar; artifacts land in the stage S3/MinIO; zero credential in any Nova store or log. |
| **#10** | Inverted index / full-text search | `COMPOSE_STARROCKS` | M | P3 | #11 (shared-data) | On a shared-data cluster: index create/alter/drop; `MATCH_ANY`/`MATCH_ALL` with relevance scoring; the feature is **absent with an explicit reason** on shared-nothing rather than silently failing. |
| **#17** | Cluster monitor | `WRAP_STARROCKS` | M | P2 | nothing | FE/BE/CN node inventory, health, and per-node metrics; alert threshold configuration; the existing metrics-only endpoint becomes a real monitor page. |
| **#11** | Storage volumes (shared-data) | `WRAP_STARROCKS` | M | P3 | nothing | `CREATE STORAGE VOLUME` CRUD; volume-to-database binding; default volume management; volume credentials never appear in an API response or log. |

**Trino / federated query** is deliberately **not** in Phase 3 v1. Rationale in
§4: federation is a second query engine with its own optimizer and cost model,
and the open-format need it would address is already served by #9. It is a
`DEFER` candidate, not a rejected idea.

### Phase 4 — Advanced parity and scale

Purpose: items that are real Snowflake features but low Nova value today, or
that require a threat-model change.

| Item | Capability | Strategy | Effort | Prio | Depends on | Notes |
|---|---|---|---|---|---|---|
| **#1** | External secret providers | `NOVA_NATIVE` | M | P1 | nothing | Already has its own home: **NOVA-50**. Scope unchanged by this document. |
| **#18** | Data sharing / marketplace | `DEFER` | L | P3 | #9 | `docs/27-data-sharing.md` describes it; no engine primitive. Revisit after #9 proves an open-catalog path. |
| **#19** | `PERIOD` data type | `DEFER` | M | P3 | nothing | Low Nova value; recorded, not scheduled. |
| **#23** | Zero-copy clone | `DEFER` | L | P3 | nothing | `CREATE TABLE LIKE` is not a clone. No engine primitive to wrap. |
| **#24** | Data movement policies | `DEFER` | — | P3 | nothing | Out of Nova's threat model. `NOT_APPLICABLE`. |
| **#20** | Agent platform (CoCo / Cortex) | `DEFER` | — | P3 | nothing | Out of Nova product scope. `NOT_APPLICABLE`. |

### Dependency summary

```
Phase 0  #2 (NOVA-51) ──── requires 9b stages 1–4 merged (R2)
         #9 (external catalogs) ── no predecessor

Phase 1  #3 ─┐
         #4 ─┼─ no predecessor, parallel with Phase 0
         #7 ─┤
         #16 ─┘
         #15 ─ no predecessor
         #12 ─ 9a done; 9b (NOVA-54) in flight; 9c follows

Phase 2  #8 ─ no predecessor
         #21 ── #16
         #5 ── #3 or #4
         #6 ── #3, #4

Phase 3  #17 ─ no predecessor
         #11 ─ no predecessor
         #13 ── #9
         #10 ── #11

Phase 4  #1 (NOVA-50) ─ no predecessor
         #18 ── #9   (DEFER)
```

The only hard edges are #21→#16, #5→#3/#4, #6→#3/#4, #13→#9, and #10→#11.
Everything else is parallelisable.

---

## 3. MLflow integration proposal

**Decision R1 (binding):** MLflow is adopted as the ML registry; Nova stops
building a native registry.

### 3.1 Guardrails (verbatim from the Team Lead decision — these are binding)

- MLflow is **control plane only**; StarRocks stays the compute plane.
  `CREATE ML_MODEL` / `ML_PREDICT` / `AI_*` keep executing in the engine.
- Model version/alias resolve to an **MLflow model URI**; `CONFIG_*` in
  `NOVA_SYSTEM` holds only a pointer. **One owner per fact** — MLflow owns
  version + alias, Nova owns the pointer. No dual registry.
- Artifacts go to the **same S3/MinIO as stages**, never into `NOVA_SYSTEM`.
  The MLflow backend connection string must never land in a system table, API
  response, log, or UI — same rule as every other credential.
- Run it as a **sidecar** (JVM + its own DB), not inside the FastAPI request
  path — same pattern as `nova-scheduler`/`nova-worker` (D9.2).
- Accepted trade-off, stated explicitly: a second identity (MLflow RBAC) and a
  second datastore. This is the same category of cost D9.2 already accepted,
  not a new one.

### 3.2 Architecture

```
        ┌──────────────────────── Nova ────────────────────────┐
        │  FastAPI  ── ml_engine router ──┐                    │
        │  (request path)                 │ pointer only       │
        └─────────────────────────────────┼────────────────────┘
                                          │
   NOVA_SYSTEM.CONFIG_ML_*  ◄─────────────┘   (stores: model name,
     pointer rows only                         mlflow_model_uri, no secret)

        ┌──────────── MLflow sidecar (control plane) ──────────┐
        │  tracking server + registry + model serving          │
        │  its own DB (backend store)                          │
        └───────────────┬──────────────────────────────────────┘
                        │ artifacts            │ model URI resolved at
                        ▼                      ▼ execution time
        ┌── S3 / MinIO (same bucket as stages) ──┐   ┌── StarRocks ──┐
        │  mlflow artifact root                   │   │ compute plane │
        └─────────────────────────────────────────┘   │ ML_PREDICT    │
                                                      │ AI_COMPLETE   │
                                                      └───────────────┘
```

- **Control plane (MLflow).** Tracking server, model registry, model serving.
  Runs as its own process with its own datastore. Never called from inside a
  StarRocks query.
- **Compute plane (StarRocks).** `CREATE ML_MODEL`, `ML_PREDICT`, and the
  `AI_*` functions execute where they execute today. Nova does not move
  inference into Python.
- **The bridge.** `NOVA_SYSTEM.CONFIG_ML_*` holds a pointer row per Nova model:
  model name, `mlflow_model_uri`, and the Nova-side metadata needed to render
  the UI. It never holds a credential, an artifact, or a version number —
  version and alias live in MLflow, and only in MLflow.
- **Artifacts.** The MLflow artifact root is the same S3/MinIO that backs Nova
  stages. That is what keeps the "open format" principle intact: the artifact
  is retrievable without MLflow.
- **Existing tables.** `NOVA_SYSTEM.ML_MODELS`, `ML_MODEL_VERSIONS`, and
  `ML_MODEL_ALIASES` (`docker/init-nova.sql:265-301`) currently duplicate what
  MLflow owns. v1 keeps `ML_MODELS` as the pointer table and **stops writing**
  version/alias rows there. The version and alias tables are left in place,
  unwritten, and dropped in a later migration once no reader remains.

### 3.3 Credentials and identity

- The MLflow backend store connection string and the artifact-store credentials
  are supplied to the sidecar through environment/secret references, never
  through `NOVA_SYSTEM` and never through an API response.
- MLflow's own RBAC is a **second identity** alongside StarRocks. v1 does
  **not** attempt to unify them. The Nova UI talks to MLflow through the Nova
  backend; the MLflow UI is not exposed to end users in v1. This is stated as an
  accepted limitation, not an oversight.
- Nova's existing invariant holds unchanged: no credential in UI state, API
  response, log, or system table.

### 3.4 Acceptance criteria (objectively testable)

1. `CREATE ML_MODEL` through Nova registers the model in MLflow and writes
   exactly **one** pointer row in `NOVA_SYSTEM`. A second `CREATE` for the same
   name updates the pointer, it does not create a second row.
2. `ML_PREDICT` against a model version resolves the **MLflow model URI** at
   execution time: bumping the alias in MLflow changes what `ML_PREDICT`
   returns **without any Nova-side write**, asserted by test.
3. `SELECT * FROM NOVA_SYSTEM.ML_MODEL_VERSIONS` and `…ML_MODEL_ALIASES`
   return **zero Nova-written rows** after a model is registered through the
   MLflow path.
4. The model artifact exists in the **stage S3/MinIO bucket** and is
   downloadable with the stage credentials alone.
5. **Credential scan:** `grep` across API responses, application logs, and every
   `NOVA_SYSTEM` table for the MLflow backend connection string and the artifact
   secrets returns no match, over a full create→predict→delete cycle.
6. Killing the MLflow sidecar leaves Nova's core SQL path **fully functional**;
   only ML-registry endpoints degrade, with an explicit error. The FastAPI
   process must not block on MLflow being up.
7. Two Nova users with different StarRocks roles see StarRocks-enforced
   behaviour on `ML_PREDICT` unchanged from today (delegate-first RBAC is not
   weakened by the MLflow adoption).
8. `ruff` + `mypy` + unit tests pass; an L3 test covers criteria 1–2 against a
   live sidecar.

### 3.5 Estimate

**L**, split into stages, each its own PR:

| Stage | Content | Size |
|---|---|---|
| 1 | Sidecar bring-up (MLflow + backend store + artifact root), deployment docs, readiness probe, health degradation | M |
| 2 | Pointer model: `CONFIG_ML_*` schema, repository, API; stop writing version/alias rows | M |
| 3 | Engine bridge: resolve model URI from `CONFIG_ML_*`, wire `CREATE ML_MODEL` / `ML_PREDICT` | M |
| 4 | UI: model list, version/alias view (read-through to MLflow), artifact link | M |
| 5 | L3 acceptance tests for §3.4, credential scan, docs | S |

Stage 1 and Stage 2 can run in parallel. Stage 3 depends on both.

### 3.6 Risks

| Risk | Mitigation |
|---|---|
| Two registries drift | Enforced by "one owner per fact" (R1): MLflow owns version + alias, Nova owns the pointer. Acceptance criterion 3 makes the drift mechanically detectable. |
| Second identity (MLflow RBAC) confuses users | MLflow UI is not exposed in v1; all access is through the Nova backend, which authenticates to MLflow with a service identity. |
| Artifact store diverges from stage storage | Acceptance criterion 4 pins the artifact root to the stage bucket by test. |
| MLflow is a fast-moving project (weekly releases at v3.16.1 at the time of writing) | Pin the exact MLflow version in the sidecar image; upgrades are their own reviewed change. |
| Sidecar outage breaks Nova | Acceptance criterion 6: the core SQL path must not depend on MLflow liveness. |

---

## 4. Out of scope for v1

These are explicitly **not** in v1. Each is a decision, not an omission.

| Out of scope | Why | Revisit when |
|---|---|---|
| **Trino / federated query** | A second query engine brings its own optimizer, cost model, and failure modes. The open-format need it would serve is already met by #9 (external catalogs). Adding it in v1 would double the query surface for no parity gain. | A concrete source that #9 cannot reach is required in production. |
| **Databricks SQL, BigQuery, Redshift as integration targets** | They lock data in. Contradicts the open-format principle. | Not planned. |
| **Unifying MLflow RBAC with StarRocks RBAC** | A cross-system identity model is a security project, not an integration project. | An approved identity/federation design exists. |
| **MLflow UI exposed to end users** | v1 routes all access through the Nova backend. | The RBAC unification above lands. |
| **Distributed / GPU training inside Nova** | MLflow covers tracking and serving; training compute is a separate concern with no current requirement. | A training-scale requirement is stated. |
| **Inverted index on shared-nothing** | The engine feature is shared-data only. | Nova moves to shared-data as a deployment mode. |
| **A Nova-native ML registry** | Replaced by R1. | Not planned — this is the decision that R1 closed. |

## 5. Items marked `DEFER`

| Item | Capability | Revisit trigger |
|---|---|---|
| #18 | Data sharing / marketplace | #9 proves an open-catalog path that can carry a share. |
| #19 | `PERIOD` data type | A real Nova use case appears. |
| #23 | Zero-copy clone | The engine ships a clone primitive. |
| #24 | Data movement policies | Out of threat model — `NOT_APPLICABLE`. |
| #20 | Agent platform (CoCo / Cortex) | Out of product scope — `NOT_APPLICABLE`. |
| Trino federation | Federated query | A source #9 cannot reach is required. |

`NOT_APPLICABLE` rows are not "later"; they are "not Nova's problem". They stay
in the matrix for completeness and should not be scheduled.

---

## 6. Provenance

Verification date for every claim in this document: **2026-09-18**.

| Claim | Source |
|---|---|
| StarRocks tags `4.1.0 … 4.1.4`; `4.1.4` has no release page | `api.github.com/repos/StarRocks/starrocks/tags`, `.../releases?per_page=40`, `.../releases/tags/4.1.4` |
| Grammar line counts and the absence of `FINALIZE` / `CRON` / `OVERLAP_POLICY` / `ALLOW_OVERLAPPING`; `submitTaskStatement` identical across tags | raw `StarRocks.g4` and `StarRocksLex.g4` at `4.1.1`, `4.1.3`, `branch-4.1`, diffed locally |
| MLflow latest release `v3.16.1`, 2026-09-17 | `github.com/mlflow/mlflow/releases` |
| Engine pin is `4.1.1` | `docker/docker-compose-engine.yml:3`, `:122`; `README.md:543` |
| `external_catalogs/` is an empty stub | repo read at `9bb2a87` |
| `model_type` is `classification\|regression` only | `backend/app/modules/ml_engine/schemas.py:15-19` |
| ML tables and their columns | `docker/init-nova.sql:265-301` |
| Grammar foundation merged at `4.1.1` (`095657f`); 9b in flight | `git log` at the task checkout |

**Not verified, and therefore not asserted anywhere above:**

- Any runtime or performance claim about MLflow — the sidecar has not been run.
- Whether `4.1.4` is a full release or a tag-only cut.
- Whether `VisibleVersion` moves on an MV refresh; where `enable_task_history_archive`
  stores its archive (carried over from the NOVA-23 design doc, still open).
