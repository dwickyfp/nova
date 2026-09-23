# Module 28: Intelligence Foundation

> Entity identities, AI Search, Semantic Views, and Feature Store share Nova governance and StarRocks metadata.

---

## Concept/Overview

The Entity Registry records a source relation and one or more stable key columns. AI Search indexes, Semantic Views, and Feature Views use these identities. Their metadata lives in `NOVA_SYSTEM`; Search and Feature data projections live in managed StarRocks databases. Redis holds optional online features. AI provider credentials stay in the existing secret configuration path and never enter Intelligence metadata.

AI Search owns a versioned managed embedding definition. A version pins the embedding model ID, revision, and dimensions. The Search worker builds a separate projection, validates it, and leaves the active version available while a replacement is built. The reconciliation poller compares source fingerprints; unchanged content hashes reuse previous vectors. A failed build can be retried, then activated. The source write path does not call an embedding provider.

Semantic Views store versioned Ossie definitions. Validation compiles and executes verified queries through the caller's session, then compares active and candidate results without storing data rows in the validation report. Changed results require explicit acknowledgement before publish. Production queries use the active version unless a published version is pinned. Feature Views materialize timestamped columns in StarRocks; a Feature Group pins exact view versions. Point-in-time training data uses ASOF joins with label timestamps. Redis publication is optional; lookup falls back to governed offline data if Redis is unavailable.

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

Verified-query validation is bounded to 20 cases and 100 result rows per case. A report distinguishes `matched`, `sql_changed`, `result_changed`, `truncated`, `compile_failed`, and `execution_failed`. Publish a reviewed intentional change with `POST /semantic-views/{id}/versions/{version}/publish` and body `{"acknowledge_regressions":true}`. Validation and result comparison use the caller's StarRocks privileges and active role.

Feature Store routes are under `/features`. Create a View with `entity_id`, `source_relation`, `event_timestamp`, and `feature_columns`. Refresh creates a new READY version; activate changes the default. Create a Group with members `{ "view_name": "...", "version": 1 }`. Group versions can be added and activated. Lookup accepts an `entity_key`, optional `as_of`, and optional group `version`. `training-set` accepts a label relation, entity keys, event timestamp, and label columns. `materialize-online` accepts up to 100 entity keys per request. `train` uses Nova ML and stores the model-to-feature-version link.

## Nova UI

The main Nova sidebar exposes AI Search, Semantic Views, and Feature Store as console pages. The Entity tab is available from these pages, and Database Explorer lists Entities, Semantic Views, and Feature Views alongside tables and views within their database. Creation forms select source tables or views and their columns from authorized Explorer metadata. The Semantic Views page shows version validation and regression evidence, supports a new Ossie draft, and requires acknowledgement for reviewed changes before publish. AI Providers distinguishes LLM and embedding models and records alias, revision, dimensions, modality, and metric. Search build status and versions are visible in Nova; query supports lexical, semantic, hybrid, and structured filters.

## Sample Data and Access

With the bundled `NOVA_DEMO` and `NOVA_CATALOG` tables loaded, run `cd backend && uv run python -m app.modules.intelligence.examples.seed_intelligence`. Set `STARROCKS_FE_MYSQL_PORT=29030` when using the local Docker host mapping. Add `--embedding-alias nova.embedding.default` to also create a semantic Search Index using an existing active embedding model. The repeatable seed creates `nova_demo_customer` and `nova_demo_order` Entities, `nova_sales_360` Semantic View, `nova_demo_product_search` Search Index, `nova_demo_order_features` Feature View, `nova_demo_order_feature_group`, and a point-in-time training set over demo orders. The Semantic View has v1 active and a validated v2 whose deliberate revenue change appears in the regression report. The optional vector index is `nova_demo_product_semantic_search`.

The seed grants read access on the two demo source databases to `ACCOUNTADMIN` and assigns that role to `nova_admin`. Users must activate `ACCOUNTADMIN` for role-based management of Intelligence objects created by another owner. Source-row access still uses the caller's StarRocks session; the role does not bypass Ranger row policies. No provider credential is seeded or stored in Nova metadata.

## Implementation Notes

Migrations are `backend/migrations/20260923_intelligence_entities.sql`, `20260923_ai_embedding_models.sql`, `20260923_ai_search.sql`, `20260923_semantic_views.sql`, and `20260923_feature_store.sql`. Existing deployments must apply migrations before starting the updated API. The bootstrap SQL in `docker/init-nova.sql` carries matching new-install tables. StarRocks FE needs vector and inverted index support enabled for semantic and lexical projections; `docker/fe.conf` includes the flags. The Search capability probe fails clearly when the requested backend is unavailable.

Metadata and source access require the caller's identity and active role. Search rehydrates hits through that session. Feature and Semantic queries use the source relation under Ranger. Nove and Agent Studio both expose consent-gated `ai_search`, `semantic_view_query`, and `feature_lookup` tools. Nove answers product questions from packaged, cited reference knowledge without executing these data tools. The bounded assistant evaluation suite covers explanation routing and all three data tools.

Operational checks: inspect Search index versions and sync state when a build fails; use the retry route after restoring provider availability. Check embedding model revision/dimensions before rebuilding. Keep the previous active version until evaluation passes. If Redis is down, offline Feature lookup remains available. Audit events are written to `NOVA_SYSTEM.AUDIT.LOG` for lifecycle and query operations.

## Limitations

Search source reconciliation currently scans a bounded source set and runs from the API process. Large or rapidly changing sources need a dedicated durable worker and change stream. Feature refresh is manual; a scheduled refresh trigger is not yet connected to Nova Task Orchestration. Semantic result comparison is exact over a bounded snapshot and can report a change when source rows mutate between executions; it does not provide transactional snapshot isolation across versions. A full Ranger policy matrix and scale benchmark remain release gates. See [the acceptance plan](specs/nova-intelligence-foundation-plan.md) for the test evidence and remaining gates.
