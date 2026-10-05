# Module 28: Intelligence Foundation

> Entity identities, AI Search, Semantic Views, and Feature Store provide the governed data and business meaning foundation of Nova, the Enterprise Intelligence OS.

---

## Concept/Overview

The Entity Registry records a source relation and one or more stable key columns. AI Search indexes, Semantic Views, and Feature Views use these identities. Their metadata lives in `NOVA_SYSTEM`; Search and Feature data projections live in managed StarRocks databases. Redis holds optional online features. AI provider credentials stay in the existing secret configuration path and never enter Intelligence metadata.

AI Search owns a versioned managed embedding definition. A version pins the embedding model ID, revision, and dimensions. The Search worker builds a separate projection, validates it, and leaves the active version available while a replacement is built. The reconciliation poller compares source fingerprints; unchanged content hashes reuse previous vectors. A failed build can be retried, then activated. The source write path does not call an embedding provider.

Semantic Views store versioned Ossie definitions. Validation compiles and executes verified queries through the caller's session, then compares active and candidate results without storing data rows in the validation report. Changed results require explicit acknowledgement before publish. Production queries use the active version unless a published version is pinned. Feature Views materialize timestamped columns in StarRocks; a Feature Group pins exact view versions. Point-in-time training data uses ASOF joins with label timestamps. Redis publication is optional; lookup falls back to governed offline data if Redis is unavailable.

Semantic Views are also the single semantic object selected in Agent Studio. An agent stores `semantic_view_ids`; an unmanifested agent's natural-language `semantic_query` resolves active View versions under the caller's current role and source privileges. A manifested release resolves pinned published versions/fingerprints and keeps authorization live. See [governed Studio releases](arch-15-governed-studio.md#release-manifests-and-promotion). Nove can query an active View without an Agent Studio binding. The older Agent Studio semantic-model tables are retained as migration input and for saved-binding compatibility, not for authoring a second semantic catalog.

Publishing a new View version changes the agent access fingerprint. Bound agents must verify access again before their next run, so a new source or formula is checked against the roles allowed to use that agent.

## Intelligence lifecycle

The [governed Studio architecture](arch-15-governed-studio.md) extends this
lifecycle with chat-originated comparisons, registered scenarios, an Action
ledger, scoped context/usage, and Mission links. Current Action adapters create
and verify a governed monitor/schedule or an internal Studio automation.
Verification establishes configuration, not a business intervention or causal
effect. New controls default to
disabled; see [migration and acceptance](governed-studio-operations.md).

The Intelligence Engine extends these owners with Context Graph references,
semantic monitors, News, investigations, decisions and outcome evaluation.
Its implementation and acceptance status are tracked in
[the delivery matrix](benchmarks/business-intelligence/delivery.md). Local
deterministic tests and ordinary StarRocks integration do not establish patched-FE
Ranger acceptance or live-provider improvement.

Lifecycle APIs use `/api/v1/intelligence`. Graph nodes reference published
Semantic View versions or immutable knowledge and decision revisions. Graph
traversal checks every endpoint under the viewer's current access. Opening a
shared decision rechecks its stored query evidence with the viewer's principal,
active role and current session. A role match cannot substitute for that check.

Monitors pin a semantic plan, observation period, baseline, sample threshold and
materiality threshold. News records deduplicated observations; investigation
records exact arithmetic contributions and separately labelled associations.
Decision options retain their assumptions, numerical method, uncertainty and
evidence before selection. Approval applies to the exact decision and policy
revision. Inventory transfers and rollbacks remain recommendations; these APIs
do not execute the external business action.

Outcome evaluation distinguishes an open window, missing observations, complete
observations, cancellation and supersession. Complete outcomes can produce
private inferred knowledge with a pinned outcome revision. Outcome learning
cannot publish a Semantic View or verify a shared business calculation.
Effectiveness reports keep forecast error, coverage, policy compliance, timing
and attribution separate. Monetary aggregates require the same metric, currency
and semantic version.

Apply `backend/migrations/20261001_intelligence_engine.sql` before deploying
the compatible API and workers, then deploy Studio surfaces. New schedules are
disabled by default. Enabling monitoring or outcome schedules requires the
current principal's authorized role execution binding; the default cadence is
15 minutes. Disabling a schedule stops new scheduled work and preserves its
history. See the [learning and operating guide](benchmarks/business-intelligence/operations.md)
for review, recovery and evaluation boundaries.

## News editions

A manager switches News on per Semantic View (`PUT /semantic-views/{id}/news`).
The switch stores what the edition covers (up to three additive metrics, a
record count metric, up to three slice dimensions, a time dimension, thresholds
and cadence) and schedules the allowlisted `intelligence.newsroom` handler under
the execution account bound to the chosen role. Enabling requires managing the
view, holding that role or a task administration role, and an existing execution
binding; administration roles cannot be the publishing role.

Each cycle runs one daily-grain query per metric and dimension, covering the
last complete local day and the same weekday of the baseline weeks. Detection is
the same matched-weekday median/MAD rule monitors use, applied per slice value.
A story stores its figures, its series and access proofs: digests of exactly the
rows it reports. Narrative text starts as deterministic wording. When the view
allows it, the default model rewrites at most three stories per cycle; figures
are placeholders the server fills, a structural check rejects digits and other
segments' names, and a second model call must return a clean verdict on stated
causes and spelled-out quantities. A rejected draft keeps the deterministic text.

Reading (`GET /intelligence/newspaper`, `GET /intelligence/stories/{id}`) first
requires the per-user News entitlement, which an administrator sets under Users
& Roles and which is off by default. The reader then runs every proof query of
the edition under their own active role. A story is returned only when each of
its proofs is reproduced exactly, so Ranger row filters and masks decide
visibility in the engine. A reader with partial access sees slice stories they
can reproduce and never a total. Unavailable, unauthorized and revised stories
all return 404. No story data is cached across requests.

The fixture, labels and offline benchmark are described in
[workspace/news_demo](../workspace/news_demo/README.md).

## Operations

API examples below use the authenticated `/api/v1` base. Create a shared identity before a Feature View:

```http
POST /api/v1/entities
Content-Type: application/json

{"name":"customer","database":"COMMERCE","relation":"COMMERCE.CUSTOMERS","key_columns":["customer_id"]}
```

Create and query a Search Index:

```http
POST /api/v1/ai/search
Content-Type: application/json

{"name":"product_search","source_relation":"COMMERCE.PRODUCTS","key_columns":["id"],"content_columns":["title","description"],"filter_columns":["category"],"model_alias":"nova.embedding.default"}

POST /api/v1/ai/search/product_search/query
Content-Type: application/json

{"query":"lightweight waterproof running shoe","mode":"HYBRID","top_k":20,"filters":{"category":"running"}}
```

`LEXICAL` does not require a model. `SEMANTIC` and `HYBRID` require a configured embedding alias. Retrieval uses deterministic reciprocal rank fusion with a configurable `rrf_k` in the query. Search results are rechecked against the source through the caller's StarRocks session before content is returned.

Search lifecycle routes include `GET /ai/search`, `GET /ai/search/{name}`, `POST /ai/search/{name}/rebuild`, `POST /ai/search/{name}/versions/{version}/retry`, `POST /ai/search/{name}/versions/{version}/activate`, `POST /ai/search/{name}/evaluate`, and `DELETE /ai/search/{name}`. Rebuild may specify another `model_alias`; only activation changes the serving version. Evaluation persists Precision@K, Recall@K, MRR, NDCG, zero-result rate, and latency summaries.

Semantic View lifecycle routes are under `/semantic-views`: create, list, describe, add version, validate, publish, query, deprecate, and delete. Definitions use Ossie YAML. Query accepts metric and dimension names; generated SQL remains deterministic. This release does not add a SQL DDL grammar for these objects.

`POST /semantic-views/{id}/versions/{version}/preview` plans a natural-language question and returns the plan and compiled SQL without executing it. `GET /semantic-views/{id}/versions/{version}/quality` exposes validation, lint, quality score, and verified-query evidence. Adding a verified query through `POST /semantic-views/{id}/versions/{version}/verified-queries` creates a new draft version; the active definition is never edited in place. Validate and publish that draft to make it available to agents.

Verified-query validation is bounded to 20 cases and 100 result rows per case. A report distinguishes `matched`, `sql_changed`, `result_changed`, `truncated`, `compile_failed`, and `execution_failed`. Publish a reviewed intentional change with `POST /semantic-views/{id}/versions/{version}/publish` and body `{"acknowledge_regressions":true}`. Validation and result comparison use the caller's StarRocks privileges and active role.

Feature Store routes are under `/features`. Create a View with `entity_id`, `source_relation`, `event_timestamp`, and `feature_columns`. Refresh creates a new READY version; activate changes the default. Create a Group with members `{ "view_name": "...", "version": 1 }`. Group versions can be added and activated. Lookup accepts an `entity_key`, optional `as_of`, and optional group `version`. `training-set` accepts a label relation, entity keys, event timestamp, and label columns. `materialize-online` accepts up to 100 entity keys per request. `train` uses Nova ML and stores the model-to-feature-version link.

## Nova UI

The main Nova sidebar exposes AI Search, Semantic Views, and Feature Store as console pages. The Entity tab is available from these pages, and Database Explorer lists Entities, Semantic Views, and Feature Views alongside tables and views within their database. Creation forms select source tables or views and their columns from authorized Explorer metadata. The single Semantic Views page includes a detailed view of ownership and scope, active and draft versions, datasets and fields, metrics, relationships, hierarchies, filters, verified queries, validation and regression evidence, quality findings, preview SQL, and the Ossie definition. It supports a new Ossie draft and requires acknowledgement for reviewed changes before publish. The former Agent Studio Semantic route redirects here, and the agent configuration picker lists active Views. AI Providers distinguishes LLM and embedding models and records alias, revision, dimensions, modality, and metric. Search build status and versions are visible in Nova; query supports lexical, semantic, hybrid, and structured filters.

## Sample Data and Access

With the bundled `NOVA_DEMO` and `NOVA_CATALOG` tables loaded, run `cd backend && uv run python -m app.modules.intelligence.examples.seed_intelligence`. Set `STARROCKS_FE_MYSQL_PORT=29030` when using the local Docker host mapping. Add `--embedding-alias nova.embedding.default` to also create a semantic Search Index using an existing active embedding model. The repeatable seed creates `nova_demo_customer` and `nova_demo_order` Entities, `nova_sales_360` Semantic View, `nova_demo_product_search` Search Index, `nova_demo_order_features` Feature View, `nova_demo_order_feature_group`, and a point-in-time training set over demo orders. The Semantic View has v1 active and a validated v2 whose deliberate revenue change appears in the regression report. The optional vector index is `nova_demo_product_semantic_search`.

The seed grants read access on the two demo source databases to `ACCOUNTADMIN` and assigns that role to `nova_admin`. Users must activate `ACCOUNTADMIN` for role-based management of Intelligence objects created by another owner. Source-row access still uses the caller's StarRocks session; the role does not bypass Ranger row policies. No provider credential is seeded or stored in Nova metadata.

## Implementation Notes

Migrations are `backend/migrations/20260923_intelligence_entities.sql`, `20260923_ai_embedding_models.sql`, `20260923_ai_search.sql`, `20260923_semantic_views.sql`, `20260923_feature_store.sql`, and `20260924_agent_semantic_view_binding.sql`. Existing deployments must apply migrations before starting the updated API. The runtime Semantic View schema check adds the `visibility` column to existing tables. The bootstrap SQL in `docker/init-nova.sql` carries matching new-install tables. StarRocks FE needs vector and inverted index support enabled for semantic and lexical projections; `docker/fe.conf` includes the flags. The Search capability probe fails clearly when the requested backend is unavailable.

At startup, `semantic_migration.py` copies legacy Agent Studio definitions and verified queries into Semantic View versions with the original IDs. Imported Views are private to their owners; a shared agent can use one only when its role grant and bound View ID are verified, and every query still checks the caller's source access. The agent binding backfill fills `semantic_view_ids` only when it is SQL NULL, preserving an explicit empty list. An unsupported or invalid legacy Ossie definition remains a private, review-only draft with its original content visible; the owner must add a valid 0.1.1 version before validation and publication. Legacy authoring routes are deprecated so new edits go through versioned Semantic Views.

Metadata and source access require the caller's identity and active role. Search rehydrates hits through that session. Feature and Semantic queries use the source relation under Ranger. Agent Studio's `semantic_query` answers natural-language questions against bound active Views or a release's pinned published versions; `semantic_view_query` takes explicit metrics and dimensions. Both use the same View catalog. Nove can use the structured View tool directly. The assistant also exposes consent-gated `ai_search` and `feature_lookup`. Nove answers product questions from packaged, cited reference knowledge without executing these data tools. The bounded assistant evaluation suite covers explanation routing and the data tools.

Operational checks: inspect Search index versions and sync state when a build fails; use the retry route after restoring provider availability. Check embedding model revision/dimensions before rebuilding. Keep the previous active version until evaluation passes. If Redis is down, offline Feature lookup remains available. Audit events are written to `NOVA_SYSTEM.AUDIT.LOG` for lifecycle and query operations.

## Limitations

Search source reconciliation currently scans a bounded source set and runs from the API process. Large or rapidly changing sources need a dedicated durable worker and change stream. Feature refresh is manual; a scheduled refresh trigger is not yet connected to Nova Task Orchestration. Semantic result comparison is exact over a bounded snapshot and can report a change when source rows mutate between executions; it does not provide transactional snapshot isolation across versions. A full Ranger policy matrix and scale benchmark remain release gates. See [the acceptance plan](specs/nova-intelligence-foundation-plan.md) for the test evidence and remaining gates.
