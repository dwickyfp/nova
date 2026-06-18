# Module 10: External Catalogs

> Manage external data source connections: Hive, Iceberg, Paimon, JDBC, Hudi, Delta Lake, Elasticsearch.

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
| SQL Server | MV refresh support (v4.1.1 fix) |

---

## Catalog Manager UI

### Catalog List

```
┌─ External Catalogs ─────────────────────────────────────┐
│                                                          │
│  [+ Add Catalog]                                         │
│                                                          │
│  Name             Type      Metastore   Status           │
│  iceberg_lake     Iceberg   HMS         🟢 Connected    │
│  hive_warehouse   Hive      Glue        🟢 Connected    │
│  paimon_staging   Paimon    Filesystem  🟢 Connected    │
│  pg_analytics     JDBC      PostgreSQL  🟢 Connected    │
│  es_logs          ES        REST        🟡 Slow          │
└──────────────────────────────────────────────────────────┘
```

### Add Catalog Form

```
┌─ Create External Catalog ───────────────────────────────┐
│                                                          │
│  Name: [iceberg_lake                          ]         │
│  Type: [Iceberg ▼]                                       │
│  Comment: [Iceberg data lake                   ]         │
│                                                          │
│  ── Metastore ──                                         │
│  Metastore type: [Hive Metastore ▼]                     │
│  HMS URI: [thrift://hms:9083                 ]          │
│                                                          │
│  ── Storage ──                                           │
│  Storage type: [MinIO ▼]                                │
│  Endpoint: [http://minio:9000                ]          │
│  Access Key: [****                               ] 🔒   │
│  Secret Key: [****                               ] 🔒   │
│  Path style: [✓]                                         │
│                                                          │
│  [Test Connection]  [Create Catalog]                     │
└──────────────────────────────────────────────────────────┘
```
