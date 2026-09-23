# Nova Intelligence Foundation: implementation and release plan

> Implementation map for the attached AI Search, Autonomous Embeddings, Semantic View 2.0, and Feature Store specification. A release is ready only when every gate below has executable evidence.

---

## Existing architecture to reuse

- `CONFIG_AI_PROVIDERS` and `CONFIG_AI_MODELS` hold provider and model registration. The assistant and AI SQL functions already consume them.
- `app/modules/agents/semantic/` contains Ossie IR, compiler, planner, authorization scope, literal resolution, and quality checks. Semantic View 2.0 must extend those modules.
- `app/modules/task_orchestration/` owns scheduled work, retries, and execution identity. Refresh jobs must use it.
- `app/modules/ml_engine/` owns ML registry and execution. Feature Store integrations must call this engine.
- `NOVA_SYSTEM` Primary Key tables are the metadata store. Data-plane projections belong in StarRocks tables; credentials remain in the existing provider handling path.
- `app/modules/assistant/` owns the bounded agent loop. New tools must be registered in its catalog and have evaluation trajectories.

## Sequence and release gates

| Gate | Implementation | Required evidence |
| --- | --- | --- |
| 0. Baseline | Record existing unit, integration, eval, frontend, and live-engine results. | Baseline command log, environment and pre-existing failures. |
| 1. Shared identities | Versioned entity registry with compound keys and ownership; references in semantic views, features, and search. | CRUD, referential, authorization, and migration tests. |
| 2. Embedding registry and service | Add model type, alias, pinned revision, dimensions, modality, metric; guarded provider adapter and bounded batches. | Model CRUD, migration, response-validation, credential-redaction, provider-failure, and UI tests. |
| 3. AI Search | Search index metadata and versions; projection lifecycle; lexical, semantic, and RRF hybrid retrieval; authorized source fetch; refresh/rebuild jobs. | StarRocks capability check; all lifecycle modes and filters; version swap and rollback; RBAC and failure injection. |
| 4. Semantic View 2.0 | Extend Ossie IR and compiler with shared entities, hierarchies, derived metrics, grain/additivity, verified queries, versioned publish, and deterministic API. | Compiler and dependency tests, SQL execution, regression validation, permissions, and Studio tests. |
| 5. Feature Store | Versioned feature views/groups, offline materialization, point-in-time joins, online backend with freshness and serving API, ML integration. | Temporal leakage tests, lifecycle, online/offline consistency, lineage, RBAC, and ML training scenario. |
| 6. Integration | Studio workflows, agent tools, audit, lineage, observability, documentation. | Cross-foundation entity scenario, agent trajectories, complete functional matrix, regression suite, and live deployment smoke test. |

Each gate includes happy path, validation error, authorization, and lifecycle coverage for every public operation. No gate is considered complete from unit tests alone. The final acceptance scenario in the attached specification must run against a real StarRocks deployment with an embedding provider and the configured online store.

## Current implementation state

Embedding model types, aliases, revisions, modality, dimensions, and metric metadata are registered through AI Providers. The embedding adapter validates vectors and supports bounded batches. Entity Registry persists compound identities with caller-scoped source checks and audit in StarRocks.

AI Search persists index definitions, pinned model versions, projection versions, sync state, query logs, and relevance evaluation runs. A Redis lock protects distributed builds. A poller resumes builds after restart and reconciles source changes by fingerprint, then swaps ready versions. Search supports lexical, semantic, and RRF hybrid retrieval, filters, source-row rechecks under the caller's role, and an Agent Studio tool. A live scenario covered create, all three query modes, filtered retrieval, manual rebuild and swap, relevance evaluation, automatic insertion/deletion reconciliation, unchanged-content reuse, provider failure, retry, and cleanup. FE vector and inverted-index flags are enabled in `docker/fe.conf` and in the current local FE runtime; the capability probe reported both available.

Semantic View 2.0 persists versioned Ossie definitions, validation reports, verified-query regression results, and active aliases. The deterministic compiler handles derived arithmetic metrics, numeric dependency checks, and hierarchy validation. Validation executes bounded verified queries as the caller, compares active and candidate result rows, and requires explicit acknowledgement before publishing changed results. A live scenario covered create, validate, publish, query, second draft, result-regression validation, rejected unacknowledged publish, acknowledged version swap, and drop. A dedicated agent tool is consent-gated.

Feature Store persists versioned Feature Views, groups, PIT training plans, and ML model links. Offline StarRocks projections, Redis online materialization, freshness checks at lookup, caller-governed serving, multiple ASOF joins, and a training path through Nova's existing ML engine are implemented. Live scenarios proved no future leakage, multi-view joins, batch online materialization, refresh/activation, and actual ML training with pinned lineage. A dedicated agent tool is consent-gated. The main Nova console has AI Search, Semantic Views, and Feature Store pages; the Entity tab and Database Explorer expose the corresponding schema-scoped Nova metadata objects.

This is not yet the full 100% acceptance claim. Each Search Index owns a managed embedding definition and pinned version; a reusable standalone definition is not exposed. Remaining release work includes scheduled Feature View refresh, broader per-operation and failure-injection coverage, source mutation consistency during long builds, and full deployment validation with Ranger policies. Semantic result comparison lacks a transactionally consistent snapshot across both executions. These are release gates, not cosmetic follow-ups.

The StarRocks 4.1 vector index is a beta feature available only on shared-nothing clusters. Search index creation must check this capability and fail clearly on unsupported deployments. StarRocks 4.1 supports ASOF JOIN with one DATE/DATETIME temporal condition, which the Feature Store training compiler can use after validating entity equality and timestamp types.

## Production acceptance checklist

1. Run all added tests, relevant existing unit/integration/eval tests, frontend tests, type checking, and lint with zero unexpected failures.
2. Run the end-to-end acceptance scenario with StarRocks, Redis, provider access, and online serving backend; record versions and outputs.
3. Verify that every introduced API, worker transition, agent tool, and version change has a corresponding executable test in a functional matrix.
4. Inspect source for incomplete paths, validate migration replay and rollback behavior, and audit RBAC, credential handling, and audit logging.
5. Publish the release report with exact commands, pass counts, coverage, failures, and genuine limitations. A 100% functional claim requires every matrix entry to pass.

## Validation snapshot (through 2026-09-24)

- Final full backend unit suite after the restricted-user guard, pure-domain tests, and agent filter support: 3,749/3,749 passed with loopback socket access. An earlier sandboxed run had seven socket-bind failures caused by macOS sandbox policy; the unrestricted rerun passed. No unit regression remains in that run.
- Agent harness: 44/44 scenarios, 145/145 checks, including consent trajectories for AI Search, Semantic View, and Feature Lookup.
- Frontend browser suite (2026-09-24): 97/97 files, 759/759 tests; lint (zero errors) and production build passed.
- Backend unit suite (2026-09-24): 3,762/3,762 tests passed. Focused additions cover Semantic View result regression, stale-baseline publish rejection, ACCOUNTADMIN management, exact DECIMAL cache serialization, and Redis client recreation across event loops.
- Live StarRocks: Entity Registry create/read/list/deprecate with caller-scoped source probe, ASOF temporal join, HNSW vector retrieval, and GIN lexical retrieval passed on the local 4.1.4 shared-nothing stack.
- Live Redis: 1/1 online feature lifecycle scenario passed.
- A registered external embedding provider was tested on 2026-09-23 after the model was configured. Two provider vectors had the pinned 1,536 dimensions and finite values; an invalid revision was rejected. The repeatable `tests/integration/test_embedding_projection_live.py` retrieved the expected document through vector and lexical queries. The embedding adapter has 100% statement and branch coverage in focused unit tests.
- Focused branch coverage after the final pure-logic tests: point-in-time compiler 100% statements/branches; retrieval fusion and relevance 100% statements/branches (`23 passed`). This does not imply 100% service or API coverage.
- Repeatable live tests previously passed `test_ai_search_live.py`, `test_semantic_view_live.py`, `test_feature_store_live.py`, and `test_feature_ml_live.py` on local StarRocks 4.1 with Redis and the configured embedding provider. The ML training path previously passed with 40 rows, selected `orders_7d`, and saved a model-to-feature lineage link. On 2026-09-24, an isolated eight-file Intelligence run passed 8/9 cases; `test_feature_ml_live.py` timed out during artifact upload. A separate retry with a 180-second balanced ML budget also timed out during registration. The live ML result is therefore currently unstable and remains a release gate.
- The idempotent sample seeder ran against local StarRocks and created two Entities, two Search Indexes (one semantic), a two-version Semantic View, Feature View, Feature Group, and point-in-time training set; it granted demo source access to `ACCOUNTADMIN` and assigned the role to `nova_admin`.
- A real restricted StarRocks user could not read Search, Feature Group, or Semantic View content; `test_intelligence_rbac_live.py` passed after the Feature read path was made to return a non-disclosing 404. This does not substitute for a Ranger-enabled policy matrix.
- A separate live Search test started the real poller, inserted a source row, and observed autonomous reconciliation and version activation without calling the reconcile method; it passed in 41.15 seconds.
- The running API's OpenAPI document contained `/api/v1/entities`, `/api/v1/ai/search`, `/api/v1/semantic-views`, `/api/v1/features/views`, and `/api/v1/features/groups/{name}/train`.

## Functional evidence matrix

| Function | Executable evidence | Current result |
| --- | --- | --- |
| Embedding registry, alias, dimensions, revision guard | `test_ai_embedding_registry.py`, `test_embedding_service.py`, `test_embedding_projection_live.py` | PASS |
| Entity CRUD and caller source check | `test_intelligence_entities.py`, `test_entity_registry_live.py` | PASS |
| Search lexical, semantic, hybrid, filters, RRF | `test_intelligence_retrieval.py`, `test_ai_search_live.py` | PASS |
| Search version rebuild, activation, autonomous sync, content reuse, retry | `test_ai_search_live.py`, `test_intelligence_lifecycle_guards.py` | PASS |
| Search evaluation metrics | `test_intelligence_retrieval.py`, `test_ai_search_live.py` | PASS |
| Semantic validate, result regression, acknowledgement, deterministic query, version swap | `test_semantic_view_extensions.py`, `test_semantic_view_regression.py`, `test_semantic_view_live.py` | PASS |
| Feature View and Group lifecycle, online serving, Redis fallback | `test_feature_store_live.py`, `test_online_features_live.py`, `test_feature_store_degradation.py` | PASS |
| PIT multi-view ASOF join and no future leakage | `test_feature_pit.py`, `test_feature_store_live.py` | PASS |
| Nova ML training and pinned feature lineage | `test_feature_ml_integration.py`, `test_feature_ml_live.py` | UNIT PASS; LIVE TIMEOUT ON 2026-09-24 |
| Agent tool consent and evidence paths | `test_agent_ai_search_tool.py`, `test_agent_intelligence_view_tools.py`, `tests.eval.report` | PASS |
| Nova console creation, search query, feature training call | `intelligence-page.test.tsx` | PASS |
| Restricted caller across Search, Feature, Semantic | `test_intelligence_rbac_live.py` | PASS |
| Dedicated durable embedding jobs, scheduled Feature refresh, full Ranger policy matrix, scale smoke | No complete executable scenario | RELEASE GATE OPEN |

The matrix distinguishes executed behavior from work that still needs implementation or deployment evidence. Passing the existing suites does not close the open release gates.
