# Module 10: External Catalogs

> Manage external data source connections: Hive, Iceberg, Paimon, JDBC, Hudi, Delta Lake, Elasticsearch.

> **Implementation status (NOVA-62, Phase 0 #9).** Nova ships **Iceberg and Hive**
> catalog management (both GA on the pinned engine, StarRocks 4.1.4) through
> `POST/GET/PATCH/DELETE /api/v1/external-catalogs` and the **External Catalogs**
> page. Delta Lake, Paimon, JDBC and Unified are **not exposed** in this release;
> the tables below describe the engine's capabilities, not Nova's surface.
> The catalog manager's list row shows the catalog type, metastore, and storage
> connection; the detail pane shows the engine DDL with every credential value
> redacted.

---

## Supported Catalog Types

| Type | `type` param | Features |
|------|-------------|----------|
| **Hive** | `hive` | Query, INSERT, CREATE TABLE, DROP TABLE, REFRESH, partition evolution |
| **Iceberg** | `iceberg` | Query, INSERT, DELETE (v4.1), CREATE TABLE, DROP TABLE, time-travel, VARIANT type, incremental MV |
| **Hudi** | `hudi` | Query, INSERT, REFRESH |
| **Delta Lake** | `deltalake` | Query, INSERT |
| **Paimon** | `paimon` | Query, branch/tag/version/timestamp time-travel, complex types, views, TRUNCATE |
| **JDBC** | `jdbc` | Query MySQL, PostgreSQL, Oracle, SQL Server, ClickHouse, etc. |
| **Elasticsearch** | `elasticsearch` | Query |
| **Unified** | `unified` | Query across Hive/Iceberg/Hudi/Delta Lake/Paimon/Kudu |

---

## Metastore Options

| Metastore | Supported For |
|-----------|--------------|
| Hive Metastore (HMS) | Hive, Iceberg, Hudi, Delta Lake, Paimon, Unified |
| AWS Glue | Hive, Iceberg, Hudi, Delta Lake, Unified |
| REST | Iceberg |
| JDBC | Iceberg |
| Filesystem | Paimon |

---

## Storage Credential Params

All external catalogs that access object storage need `StorageCredentialParams`:

| Provider | Params |
|----------|--------|
| AWS S3 (IAM user) | `aws.s3.access_key`, `aws.s3.secret_key`, `aws.s3.region` |
| AWS S3 (IAM role) | `aws.s3.use_instance_profile`, `aws.s3.iam_role_arn` |
| AWS S3 (Instance Profile) | `aws.s3.use_instance_profile=true` |
| MinIO | `aws.s3.endpoint`, `aws.s3.access_key`, `aws.s3.secret_key`, `aws.s3.enable_path_style_access` |
| Azure Blob | `azure.blob.storage_account`, `azure.blob.shared_key` |
| Azure ADLS2 | `azure.adls2.storage_account`, `azure.adls2.shared_key` |
| GCS | `gcp.gcs.service_account_email`, `gcp.gcs.service_account_private_key` |

---

## Catalog Operations

| Action | SQL |
|--------|-----|
| Create catalog | `CREATE EXTERNAL CATALOG <name> PROPERTIES (...)` |
| List catalogs | `SHOW CATALOGS` |
| Show create | `SHOW CREATE CATALOG <name>` |
| Switch catalog | `USE <catalog>` |
| Alter catalog | `ALTER CATALOG <name> SET (...)` |
| Drop catalog | `DROP CATALOG <name>` |
| Refresh metadata | `REFRESH EXTERNAL TABLE <name>` / `REFRESH CATALOG <name>` |

---

## Iceberg Special Features (v4.1)

| Feature | Description |
|---------|-------------|
| Native DELETE | `DELETE FROM iceberg_table WHERE ...` (position delete files) |
| TRUNCATE | `TRUNCATE TABLE iceberg_table` |
| VARIANT Type | Schema-on-read semi-structured data |
| v3 Support | Default values, row lineage |
| Incremental MV | `REFRESH_MODE = "INCREMENTAL"` for append-only tables |
| Time Travel | Read historical snapshots |
| Maintenance | `rewrite_manifests`, `expire_snapshots`, `remove_orphan_files` |
| `$properties` | Query table properties via metadata table |

## Paimon Special Features (v4.1)

| Feature | Description |
|---------|-------------|
| Time Travel | Query by branch, tag, version, or timestamp |
| Complex Types | ARRAY, MAP, STRUCT |
| Views | Paimon views |
| TRUNCATE | `TRUNCATE TABLE paimon_table` |

## JDBC Catalog Features

| Feature | Description |
|---------|-------------|
| Database metadata cache | Cached for performance |
| Custom schema resolver | Custom type mappings |
| Oracle mapping | Improved NUMBER, DATE, TIMESTAMP mapping |
| PostgreSQL mapping | Improved type mapping |
| SQL Server | MV refresh support (v4.1.4 fix) |

---

## Catalog Manager UI

The **External Catalogs** page lists Nova-managed catalogs and offers an
**Add Catalog** dialog. The dialog takes a name, type (Iceberg / Hive), metastore
type (Hive Metastore or Iceberg REST), metastore URI, and a **storage connection
name**. It has no credential field: the storage secret is resolved on the server
from `nova.yaml` / env.

```
┌─ External Catalogs ─────────────────────────────────────┐
│                                                          │
│  [+ Add Catalog]                    [Refresh]            │
│                                                          │
│  Name             Type      Metastore                 ⋯  │
│  iceberg_lake     Iceberg   thrift://hms:9083         ⋯  │
│  hive_warehouse   Hive      thrift://hms:9083         ⋯  │
└──────────────────────────────────────────────────────────┘
```

Selecting a catalog shows its engine `SHOW CREATE CATALOG` statement with all
credential values redacted to `***`.

### Create Catalog dialog (as implemented)

```
┌─ Create External Catalog ───────────────────────────────┐
│                                                          │
│  Name: [iceberg_lake                          ]         │
│  Type: [Iceberg ▼]   Metastore: [Hive Metastore ▼]      │
│                                                          │
│  ── Metastore ──                                         │
│  HMS URI / REST URI: [thrift://hms:9083       ]         │
│                                                          │
│  ── Storage ──                                           │
│  Storage connection: [production              ]         │
│  (secret lives in Nova config — never entered here)     │
│                                                          │
│  Comment: [Iceberg data lake                   ]         │
│                                                          │
│  [Cancel]  [Create]                                      │
└──────────────────────────────────────────────────────────┘
```

---

## Implementation Notes

- **Modules:** `backend/app/modules/external_catalogs/` — `router.py`, `schemas.py`,
  `service.py`, `repository.py`. The router is registered at
  `/api/v1/external-catalogs`.
- **RBAC is StarRocks-native.** Every `CREATE / ALTER / DROP` statement is issued
  on the caller's connection through the shared `QueryService` pipeline, so the
  engine's SYSTEM privilege (`CREATE EXTERNAL CATALOG ON SYSTEM`) and per-catalog
  grants (`ALTER`/`DROP ON CATALOG`) decide who may manage catalogs. Nova does not
  implement catalog permissions.
- **Engine values (4.1.4):** the Iceberg metastore selector is
  `iceberg.catalog.type`; the accepted values are `hive` (Hive Metastore),
  `hadoop` (filesystem warehouse) and `rest`. The docs' `hms` alias is **not**
  accepted by the engine — Nova maps the UI's "Hive Metastore" to `hive`.
- **Metadata mirror:** `NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS` stores only the
  catalog name, type, metastore URI, **storage connection name**, comment, and
  non-secret properties. No credential-bearing column exists; the engine catalog
  remains the source of truth for the catalog itself.
- **Catalog tree:** `GET /api/v1/explorer/catalogs` lists external catalogs with
  their databases, and `GET /api/v1/explorer/databases/{db}?catalog=<name>` lists
  the external tables so they appear in the Database Explorer tree.
- **Querying external tables** uses the normal SQL Workspace path
  (`POST /api/v1/query/execute`); the MySQL protocol proxy is untouched.

---

## Credential security

The invariant (AGENTS.md §2) is that storage credentials never appear in an API
response, log, `NOVA_SYSTEM` row, audit row, or frontend state. Catalog
credentials flow like `@stage` credentials:

1. **One resolver.** `resolve_storage_credentials()` (the same function `@stage`
   uses) resolves the access/secret keys from the named storage connection. Nova
   never invents a second credential mechanism, and a request can never supply a
   secret — `ExternalCatalogCreate`/`Alter` reject any property whose name looks
   like a credential.
2. **Redaction is the guarantee, not the engine.** StarRocks 4.1.4 masks most
   secret keys in `SHOW CREATE CATALOG` itself, but a live probe on the pinned
   engine found it returns **`aws.s3.session_token`** and
   **`hive.metastore.password` in full**, and its own mask (`AK******01`) still
   exposes two leading and two trailing characters. `redact_sql_credentials`
   therefore covers the whole catalog credential family
   (`access_key`, `secret_key`, `session_token`, `shared_key`, `private_key`,
   `password`, `credential`, …) and the API returns only the redacted form.
3. **Audit** records the redacted statement (`NOVA_SYSTEM.AUDIT_LOG.rewritten_sql`)
   because catalog DDL runs through `QueryService`; the engine receives the real
   credentials, the audit row and the response never do.
