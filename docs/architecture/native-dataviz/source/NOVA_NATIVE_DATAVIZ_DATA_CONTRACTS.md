# NOVA NATIVE DATAVIZ — LOW-LEVEL CONTRACT APPENDIX
**Purpose:** remove implementation ambiguity for PostgreSQL transaction semantics, dataset/chart/dashboard DTOs and API/version rules. Companion to main spec. **Not an executable migration**: adapt to repository migration conventions, current actor/RLS funcs and version numbering, and verify against real PG17. Do not run on production by copy/paste.

## 1. Normative object hierarchy

```text
Workspace (authorization boundary)
  ├── Dataset resources (table/view/sql/semantic; immutable revisions)
  │    └── Semantic dimensions, metrics, relationships, declared grain
  ├── Chart artifacts (immutable definition revisions)
  │    └── One pinned dataset revision + typed chart encodings
  └── Dashboard artifacts (immutable definition revisions)
       ├── Pages (ordered, stable IDs)
       ├── Widget instances (stable per-dashboard IDs)
       │    └── Chart ID + chosen chart definition revision
       ├── Filter definitions + semantic cross-dataset mappings
       └── Action/drill definitions

Published dashboard revision -> exact Chart/ Dataset definition revisions
Live query -> current viewer principal + active role + Ranger + current source data
```

*Identity invariant*: `widget_id != chart_id`, dashboard reuse never duplicates chart documents; `artifact_id` persists across chat threads, users and pages while permissions still hold. Human and Neva operations have identical transaction rules.

## 2. PostgreSQL schema sketch with exact minimum types

The following is the baseline logical schema. Choose physical column names consistent with `backend/metadata_migrations`, but **preserve every constraint/behavior**. The migration must be additive, RLS-protected and epoch/version-aware.

```sql
-- Illustrative DDL sketch, not a ready migration.
CREATE TABLE nova_meta.viz_artifacts (
    artifact_id UUID PRIMARY KEY,
    workspace_id VARCHAR(64) NOT NULL,
    artifact_type VARCHAR(16) NOT NULL
      CHECK (artifact_type IN ('chart','dashboard')),
    owner_name VARCHAR(128) NOT NULL,
    title VARCHAR(256) NOT NULL,
    draft_revision BIGINT NOT NULL DEFAULT 0 CHECK (draft_revision >= 0),
    current_publication_id UUID NULL,
    deleted_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    metadata_revision BIGINT NOT NULL DEFAULT 1 CHECK (metadata_revision > 0)
);
CREATE INDEX viz_artifacts_lookup
  ON nova_meta.viz_artifacts (workspace_id, artifact_type, updated_at DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX viz_artifacts_owner
  ON nova_meta.viz_artifacts (owner_name, updated_at DESC);

CREATE TABLE nova_meta.viz_artifact_revisions (
    artifact_id UUID NOT NULL REFERENCES nova_meta.viz_artifacts(artifact_id),
    revision BIGINT NOT NULL CHECK (revision > 0),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 2),
    definition_json JSONB NOT NULL,
    definition_sha256 CHAR(64) NOT NULL,
    author_name VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (artifact_id, revision)
);

CREATE TABLE nova_meta.viz_datasets (
    dataset_id UUID PRIMARY KEY,
    workspace_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    dataset_kind VARCHAR(32) NOT NULL CHECK (dataset_kind IN
      ('table','view','sql','semantic_model','dashboard_local_metric_view')),
    current_revision BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE nova_meta.viz_dataset_revisions (
    dataset_id UUID NOT NULL REFERENCES nova_meta.viz_datasets(dataset_id),
    revision BIGINT NOT NULL CHECK (revision > 0),
    source_json JSONB NOT NULL,
    schema_json JSONB NOT NULL,
    parameter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    sql_digest CHAR(64),
    lineage_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_sha256 CHAR(64) NOT NULL,
    author_name VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(dataset_id, revision)
);

CREATE TABLE nova_meta.viz_acl_grants (
    resource_type VARCHAR(16) NOT NULL CHECK (resource_type IN ('chart','dashboard','dataset')),
    resource_id UUID NOT NULL,
    subject_type VARCHAR(8) NOT NULL CHECK (subject_type IN ('user','role')),
    subject_id VARCHAR(128) NOT NULL,
    permission VARCHAR(16) NOT NULL CHECK (permission IN
      ('view','interact','comment','edit','publish','manage','export')),
    granted_by VARCHAR(128) NOT NULL,
    expires_at TIMESTAMPTZ NULL,
    granted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(resource_type,resource_id,subject_type,subject_id,permission)
);
CREATE INDEX viz_acl_subject ON nova_meta.viz_acl_grants
 (subject_type,subject_id,resource_type,resource_id);

CREATE TABLE nova_meta.viz_operations (
    operation_id UUID PRIMARY KEY,
    artifact_id UUID NOT NULL REFERENCES nova_meta.viz_artifacts(artifact_id),
    actor_name VARCHAR(128) NOT NULL,
    actor_role VARCHAR(128) NOT NULL,
    agent_run_id VARCHAR(64) NULL,
    base_revision BIGINT NOT NULL CHECK(base_revision >= 0),
    new_revision BIGINT NOT NULL CHECK(new_revision > 0),
    op_kind VARCHAR(40) NOT NULL,
    patch_json JSONB NOT NULL,
    patch_sha256 CHAR(64) NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL,
    UNIQUE(artifact_id,new_revision)
);

CREATE TABLE nova_meta.viz_publications (
    publication_id UUID PRIMARY KEY,
    artifact_id UUID NOT NULL REFERENCES nova_meta.viz_artifacts(artifact_id),
    artifact_revision BIGINT NOT NULL,
    pinned_references_json JSONB NOT NULL,
    release_notes VARCHAR(2000),
    published_by VARCHAR(128) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY(artifact_id,artifact_revision)
      REFERENCES nova_meta.viz_artifact_revisions(artifact_id,revision)
);

CREATE TABLE nova_meta.viz_outbox (
    event_id UUID PRIMARY KEY,
    artifact_id UUID NOT NULL,
    operation_id UUID NULL,
    event_kind VARCHAR(64) NOT NULL,
    event_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    delivered_at TIMESTAMPTZ NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT outbox_operation_unique UNIQUE(operation_id,event_kind)
);
```

**Implementation notes:** dataset revision/storage may already have a suitable approved owner in current metadata—reuse/extend if possible instead of creating duplicate tables. The sketch includes only core fields; `viz_exports`, metrics, legacy mappings and outbox claim/fencing columns must be defined per production spec. Add strict Pydantic validation and size bounds for every JSONB payload, real FKs for all stable reference identities where cardinality allows, and RLS read/write for every table. `viz_acl_grants` uses polymorphic resource IDs and requires service validation against real resource existence/type (or normalized resource registry FK); never assume an unchecked UUID is a valid ACL target.

## 3. RLS and authorizer invariants

- Use the **current** `MetadataDB` actor transaction, never an independent psycopg pool or free `SET` command from HTTP input.
- `read/publication/share/edit` authorization checks happen inside service transaction with actor/role/workspace binding. PG policies must constrain base rows and projections; `FORCE ROW LEVEL SECURITY` as appropriate.
- A user grant targets the exact principal; a role grant targets only the **currently active role**, not every assigned role.
- `share view` is NOT `SELECT table`. Data query always runs as viewer through StarRocks/Ranger, including previews, filter options, exports and Neva tools.
- `publish` permissions require edit; `manage` may share; `export` separate; all checks are centrally implemented in one action policy evaluator and a matching PG isolation layer.
- The operation response must never return raw service credentials, encrypted credential bytes, storage keys or hidden dataset SQL to unauthorized users.

## 4. Atomic operation algorithm

```text
BEGIN (actor-bound PG transaction)
  1. insert operation reservation/idempotency identity or find existing receipt
     - if committed same digest => return prior committed receipt
     - if same operation ID different digest => conflict/error
     - if in-flight result unknown => verify/await deterministic outcome
  2. SELECT artifact current state FOR UPDATE
  3. check action ACL and all referenced resources under actor context
  4. compare expected_revision against current draft_revision; else 409
  5. compile typed patch, validate resulting canonical widget/page/filter doc
  6. INSERT immutable revision and canonical SHA-256
  7. UPDATE artifact draft_revision (CAS) and updated_at
  8. INSERT durable operation receipt and one outbox event
COMMIT
return {artifact_id,operation_id,new_revision,digest,committed_at}
```

Do not auto-retry a non-idempotent submit with a new operation ID on uncertain commit. A retry with the same operation ID checks receipt and exact digest. If a `SELECT FOR UPDATE` already serializes the edit, optimistic revision check must still be honored so stale clients cannot silently overwrite changes.

**Published mutation:** a `publish` operation validates all pinned chart/dataset references and ACL, inserts immutable publication, and updates `current_publication_id` within a PG transaction. The existing published pointer remains valid if commit fails. Live viewer query results are not written to the publication record except via an explicitly authorized static export.

## 5. API request/response examples

### 5.1 Dataset SQL create

```json
{
  "workspace_id": "workspace-analytics",
  "kind": "sql",
  "title": "Orders by Category",
  "sql": "SELECT order_date, product_category, total_amount FROM sales.orders WHERE order_date >= :date_from",
  "parameters": [{"id":"date_from","type":"date","required":true,"default":"2026-01-01"}],
  "expected_workspace_revision": 5
}
```

This is not an invitation to interpolate `date_from`. Always compile typed bind arguments. Server determines `owner_name`, active role, source identity and revision; do not trust user-supplied authority metadata.

### 5.2 Chart create

```json
{
  "dataset_id": "d6b28c66-8418-4be6-98a8-dce7ab57f2ee",
  "dataset_revision": 3,
  "definition": {
    "schema_version": 2,
    "visual": "bar",
    "encoding": {
      "x": {"field_id":"product_category","semantic_role":"dimension"},
      "y": [{"field_id":"total_amount","semantic_role":"measure","aggregation":"sum"}],
      "color": null
    },
    "filters": null,
    "formatting": {"number_format":"currency","currency":"IDR"}
  }
}
```

Chart create validates `visual`/encoding discriminated schema, dataset field/type and current user privileges before save.

### 5.3 Batch dashboard query

```json
{
  "dashboard_id": "349fe8c7-c872-4be2-bf4c-8e9e5a4411fb",
  "publication_id": "published-ref-or-null-for-authorized-draft",
  "page_id": "page-1",
  "widget_ids": ["widget-revenue","widget-orders"],
  "filters": {
    "type":"and",
    "children":[
      {"type":"predicate","field_id":"region","op":"eq","value":"Jakarta","source":"global"}
    ]
  },
  "parameters": {"date_from":"2026-01-01"},
  "request_generation": "client-opaque-17"
}
```

Server **must not** trust `widget_ids` as permission or query targets: resolve from authorized immutable dashboard definition, reject nonmembers, map filter field IDs via approved semantic mappings, bind values, and execute through viewer's active role/Ranger. Return per-widget results/errors, freshness, query IDs and request generation for stale cancellation.

### 5.4 Share mutation

```json
{
  "grants": [
    {"subject_type":"role","subject_id":"SalesViewer","permissions":["view","interact"]},
    {"subject_type":"user","subject_id":"analyst@example.com","permissions":["view","interact","edit"]}
  ],
  "expected_grants_revision": 4
}
```

`subject_id` is a directory/principal identifier resolved server-side; JSON is illustrative. Only owner/manager can change grants. Changed grants invalidate affected viewer/WebSocket/query caches and are audit logged. Share alone does not grant StarRocks SELECT.

## 6. Filter AST and safe compilation

```text
Filter AST ::= Predicate | And[Filter AST] | Or[Filter AST] | Not[Filter AST]
Predicate ::= field_id, source_scope, operator, typed value(s), stage
operator ::= eq | neq | in | not_in | lt | lte | gt | gte |
             is_null | is_not_null | between | like_literal | relative_date
stage ::= row | aggregate | post_aggregate
```

- `Not` only over expressions whose SQL `NULL` behavior is explicitly implemented and tested.
- Cross-filter source must emit actual dimension member keys, not aggregated metric value.
- Field ID to physical source expression mapping is resolved from current authorized dataset revision. No interpolation of client-provided arbitrary column names.
- `relative_date` must be normalized with one server `as_of`, timezone, calendar unit and tested half-open interval.
- `in` list bounds, SQL bind arguments and path quotas prevent parameter explosion. Numeric decimals stay decimal through wire encoding and server aggregation.

## 7. GridStack layout invariants

Canonical dashboard page: `columns=12`; each widget `x,y,w,h` integers; `0<=x<12`, `y>=0`, `1<=w<=12`, `x+w<=12`, `h>=1` plus minimum dimensions per widget. All widgets have unique IDs per dashboard; no overlap after GridStack normalization; max widget/page count in centralized config. Layout changes commit as a **complete normalized result** on reflow, including moved neighbors. Do not persist HTML, CSS strings, custom JS, DOM refs or plugin-generated payloads.

Responsive derivation at 6/1 columns must not update persisted canonical 12 columns. Read mode disables drag/resizing; Agent mode calls exact same server layout operation while animating an independent visual cursor.

## 8. Model/schema compatibility and migration policy

- Legacy `WorksheetSpec.version=1`, `DashboardSpec.version=1`, `bi_object_id`, `bi_dataset_id` stay readable until their conversion has validation evidence. New writes use v2 only.
- Migrating legacy chart/dataset metadata to native UUIDs produces mapping receipts; old IDs cannot be treated as interchangeable UUIDs.
- All unsupported filters, custom charts, SQL or layout positions become explicit conversion blockers, preserving a safe backup, not visually approximate silent outputs.
- Production migration must verify all legacy assets and direct link mapping; code removal and cutover are separate release WPs.

## 9. Security-specific negative API requirements

Every mutation and query endpoint must reject: unknown/forged actor; ungranted active role; another workspace's artifact; referenced nonmember chart; role assigned but inactive; hidden dataset; outdated expected revision; duplicate operation ID with a different digest; expired grant; unsafe SQL; oversized filters/widgets/HTML; denied export. Response detail must not echo hidden SQL, data rows, credential strings or object keys.

**Status:** normative target contracts. No migrations, APIs or compatibility tests have been executed by preparing this file.
