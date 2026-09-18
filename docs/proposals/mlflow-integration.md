# MLflow Integration Proposal — Nova ML Registry

> **Status:** adopted by decision R1 (Team Lead, autopilot `288b503a`, 2026-09-18).
> Nova **stops building a native ML registry**; MLflow becomes the registry.
> **Scope of this document:** the integration only. The phased roadmap that
> places this work is `docs/roadmap-snowflake-parity.md` §2 Phase 3, item #13.
> **Verification date:** 2026-09-18. MLflow latest release at that date:
> `v3.16.1` (2026-09-17). Claims not verified mark themselves.

---

## 1. Why MLflow wins

The comparison below was made against what Nova actually ships today, verified
by reading the code rather than the documentation.

| Function | Nova today | MLflow | Verdict |
|---|---|---|---|
| Run / metric / parameter tracking | partial | full | **MLflow** |
| Model registry + version + alias | tables exist | yes + stages/aliases | **tie**, but two sources of truth |
| Model serving / deployment | no | yes | **MLflow** |
| Model signature & input schema | no | yes | **MLflow** |
| Artifact / object-store backend | Nova stages only | yes, S3-compatible | **overlap** |
| Lineage run → model → endpoint | no | yes | **MLflow** |
| Coupled to the engine that runs `ML_PREDICT` | yes, direct | no — needs a bridge | **Nova** |
| Zero new dependencies | yes | JVM + its own DB + artifact store | **Nova** |
| RBAC | StarRocks | MLflow's own | **Nova** (MLflow = second identity) |

Nova loses four of nine functional rows and wins only the rows it already owns.
Building the missing four — serving, signatures, lineage, full tracking — is a
large project with no architectural payoff. Hence: embed, do not rebuild.

**What this changes about matrix Row 13.** Row 13 was recorded as `SHIPPED`,
`NOVA_NATIVE + WRAP`. That is an overclaim: what shipped is `ai_query` plus 8
sklearn algorithms, and `model_type` accepts only `classification|regression`
(`backend/app/modules/ml_engine/schemas.py:15-19`). Row 13 is re-worded
`PARTIAL → EMBED_OPEN_SOURCE (MLflow)`.

---

## 2. Binding guardrails

These are reproduced verbatim from the Team Lead decision and are not advisory.

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

---

## 3. Architecture

```
        ┌──────────────────────── Nova ────────────────────────┐
        │  FastAPI  ── ml_engine router ──┐                    │
        │  (request path)                 │ pointer only       │
        └─────────────────────────────────┼────────────────────┘
                                          │
   NOVA_SYSTEM.CONFIG_ML_*  ◄─────────────┘   (model name + mlflow_model_uri
                                               + UI metadata; no secret)

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

### Control plane — MLflow

Tracking server, model registry, model serving. Runs as its own process with its
own datastore, outside the FastAPI request path. It is never invoked from inside
a StarRocks query.

### Compute plane — StarRocks

`CREATE ML_MODEL`, `ML_PREDICT`, and the `AI_*` functions execute where they
execute today. Inference does not move into Python.

### The bridge — a pointer, not a copy

`NOVA_SYSTEM.CONFIG_ML_*` holds one row per Nova model: model name,
`mlflow_model_uri`, and the metadata the UI needs to render. It never holds a
credential, an artifact, or a version number. Version and alias live in MLflow,
and only in MLflow.

At execution time Nova resolves the pointer to the MLflow model URI, and
StarRocks runs the prediction. Because the resolution happens at execution time,
promoting an alias in MLflow changes what `ML_PREDICT` returns without any
Nova-side write. That property is asserted by an acceptance test (§5, criterion 2).

### Existing tables

`NOVA_SYSTEM.ML_MODELS`, `ML_MODEL_VERSIONS`, and `ML_MODEL_ALIASES`
(`docker/init-nova.sql:265-301`) duplicate what MLflow owns.

- v1: `ML_MODELS` becomes the pointer table; **version and alias rows stop being
  written**.
- The two now-unwritten tables are left in place so no reader breaks.
- A later migration drops them once no reader remains. That migration is not
  part of this proposal.

---

## 4. Credentials and identity

- The MLflow backend-store connection string and artifact-store credentials are
  supplied to the sidecar through secret references. They never pass through
  `NOVA_SYSTEM`, never appear in an API response, and never reach a log.
- MLflow's RBAC is a **second identity** alongside StarRocks. v1 does not unify
  them. The Nova UI reaches MLflow through the Nova backend; the MLflow UI is
  not exposed to end users. This is an accepted limitation and it is named here
  so it is not discovered later as a surprise.
- Nova's existing invariant is unchanged: no credential in UI state, API
  response, log, or system table.

---

## 5. Acceptance criteria (objectively testable)

1. `CREATE ML_MODEL` through Nova registers the model in MLflow and writes
   exactly **one** pointer row in `NOVA_SYSTEM`. A second `CREATE` for the same
   name updates the pointer; it does not create a second row.
2. `ML_PREDICT` against a model version resolves the **MLflow model URI** at
   execution time: bumping the alias in MLflow changes what `ML_PREDICT` returns
   **without any Nova-side write**, asserted by test.
3. `SELECT * FROM NOVA_SYSTEM.ML_MODEL_VERSIONS` and `…ML_MODEL_ALIASES` return
   **zero Nova-written rows** after a model is registered through the MLflow path.
4. The model artifact exists in the **stage S3/MinIO bucket** and is
   downloadable with the stage credentials alone.
5. **Credential scan:** a search across API responses, application logs, and
   every `NOVA_SYSTEM` table for the MLflow backend connection string and the
   artifact secrets returns no match, over a full create→predict→delete cycle.
6. Killing the MLflow sidecar leaves Nova's core SQL path **fully functional**.
   Only ML-registry endpoints degrade, with an explicit error. FastAPI must not
   block on MLflow being up.
7. Two Nova users with different StarRocks roles see StarRocks-enforced
   behaviour on `ML_PREDICT` unchanged from today. Delegate-first RBAC is not
   weakened by the MLflow adoption.
8. `ruff` + `mypy` + unit tests pass; an L3 test covers criteria 1–2 against a
   live sidecar.

---

## 6. Staging and estimate

**Overall: L.** Each stage is its own PR.

| Stage | Content | Size | Depends on |
|---|---|---|---|
| 1 | Sidecar bring-up: MLflow + backend store + artifact root, deployment docs, readiness probe, health degradation | M | — |
| 2 | Pointer model: `CONFIG_ML_*` schema, repository, API; stop writing version/alias rows | M | — |
| 3 | Engine bridge: resolve model URI from `CONFIG_ML_*`; wire `CREATE ML_MODEL` / `ML_PREDICT` | M | 1, 2 |
| 4 | UI: model list, version/alias view (read-through to MLflow), artifact link | M | 2 |
| 5 | L3 acceptance tests for §5, credential scan, documentation | S | 1–4 |

Stages 1 and 2 run in parallel. Stage 3 needs both. Stage 4 needs stage 2 only.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Two registries drift | "One owner per fact" (guardrails). Acceptance criterion 3 makes the drift mechanically detectable. |
| A second identity confuses users | MLflow UI is not exposed in v1; the Nova backend authenticates to MLflow with a service identity. |
| Artifact store diverges from stage storage | Acceptance criterion 4 pins the artifact root to the stage bucket by test. |
| MLflow moves fast (weekly releases at `v3.16.1` on 2026-09-17) | Pin the exact MLflow version in the sidecar image; upgrades are their own reviewed change. |
| Sidecar outage breaks Nova | Acceptance criterion 6: the core SQL path must not depend on MLflow liveness. |
| Artifact/metric volume grows the stage bucket | Same lifecycle tooling as stage files; no separate mechanism in v1. |

---

## 8. Out of scope for v1

| Out of scope | Why | Revisit when |
|---|---|---|
| Unifying MLflow RBAC with StarRocks RBAC | A cross-system identity model is a security project, not an integration project. | An approved identity/federation design exists. |
| MLflow UI exposed to end users | v1 routes all access through the Nova backend. | The unification above lands. |
| Distributed / GPU training inside Nova | MLflow covers tracking and serving; training compute is a separate concern with no current requirement. | A training-scale requirement is stated. |
| Migrating existing Nova-native models into MLflow | v1 stops writing new version/alias rows; it does not rewrite history. | A migration is requested. |
| A Nova-native registry | Replaced by decision R1. | Not planned. |

---

## 9. Provenance

| Claim | Source |
|---|---|
| MLflow latest release `v3.16.1`, published 2026-09-17 | `github.com/mlflow/mlflow/releases` |
| `model_type` accepts only `classification\|regression`; 8 sklearn algorithms | `backend/app/modules/ml_engine/schemas.py:15-19` |
| ML table definitions | `docker/init-nova.sql:265-301` |
| Engine pin is `4.1.4` (NOVA-51, commit `4a9848e`); external catalogs still a stub | `docker/docker-compose-engine.yml:122,159`; repo read at `9bb2a87` |
| Sidecar pattern already accepted (`nova-scheduler` / `nova-worker`) | `docs/specs/nova-23-task-orchestration-design.md` D9.2 |

**Not verified, and therefore not asserted:** any runtime, throughput, or
latency characteristic of MLflow. The sidecar has not been run.
