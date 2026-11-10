# Module 10: External Catalogs

> Manage external data source connections: Hive, Iceberg, Paimon, JDBC, Hudi, Delta Lake, Elasticsearch.

> **Implementation status.** Nova creates catalogs from a registered
> *connection* to one of three sources: **StarRocks** (another cluster),
> **PostgreSQL**, and **Snowflake** (see [Connections and foreign catalogs](#connections-and-foreign-catalogs)).
> The built-in engine is `default_catalog` and is registered at bootstrap.
> Catalogs are added from Database Explorer (**Add catalog**) and connections
> are managed on the **Connections** page (`/connections`). Iceberg, Hive, and
> MySQL catalogs made earlier (or in raw SQL) are still synced, listed,
> queried, altered, and dropped, but Nova no longer creates them. Delta Lake,
> Paimon, Hudi, Elasticsearch, Unified, and the other JDBC sources are **not
> exposed**; the tables below describe the engine's capabilities, not Nova's
> surface.

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
| Refresh metadata | `REFRESH EXTERNAL TABLE <name>` (4.1.4 has no `REFRESH CATALOG` statement) |

Nova adds its own statements for catalogs built from connections. They are Nova
grammar (`backend/app/sql_frontend/catalog_ddl.py`); the engine never sees them.

| Action | Nova SQL |
|--------|----------|
| Register a source | `CREATE CONNECTION [IF NOT EXISTS] <name> TYPE = starrocks\|postgres\|snowflake ENDPOINT = '<endpoint>' SECRET = '<reference>' [COMMENT = '...']` |
| Remove a source | `DROP CONNECTION [IF EXISTS] <name>` (refused while a catalog uses it) |
| List / describe | `SHOW CONNECTIONS`, `DESC[RIBE] CONNECTION <name>` |
| Create a catalog | `CREATE FOREIGN CATALOG [IF NOT EXISTS] <name> USING CONNECTION <conn> [COMMENT '...'] [PROPERTIES ('jdbc_...' = '...')]` |
| Refresh metadata | `REFRESH CATALOG <name> [SCHEMA <s> [TABLE <t>]]` |
| Grant on a foreign table | `GRANT SELECT ON TABLE <catalog>.<schema>.<table> TO ROLE <role>` |

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

Catalogs are listed and added in **Database Explorer**. **Add catalog** opens
one dialog: pick StarRocks, PostgreSQL, or Snowflake, name the catalog, and
either reuse a connection of that type or register a new one with its address
and credentials (entered once, never shown again). The old
`/external-catalogs` page redirects to `/connections`.

A source catalog is a data provider: its databases show only **Tables**,
**Views**, and **Materialized Views**. Stages, pipes, tasks, functions,
entities, semantic views, and feature views are Nova's own objects and appear
under Nova's catalog only. Each relation is placed by the kind its source
reports (for Snowflake: table, transient, dynamic, Iceberg, external, hybrid,
and event tables; views, secure views, materialized views), and its detail page
shows its columns and a preview read through `catalog.schema.relation`.

The **Connections** page lists each connection by name, type, credential status
(stored in Nova, secret store, or missing), the catalogs built from it, and its
owner.

```
┌─ Connections ──────────────────────────────────── [⟳] [+ Add connection] ┐
│  Name         Type        Credentials      Catalogs   Owner          ⋯  │
│  shop_pg      PostgreSQL  Stored in Nova   pg_shop    nova_admin     ⋯  │
│  sales_sf     Snowflake   Stored in Nova   sf_sales   nova_admin     ⋯  │
└──────────────────────────────────────────────────────────────────────────┘
```

Selecting a catalog in Database Explorer shows its engine `SHOW CREATE CATALOG`
statement with all credential values redacted to `***`.

---

## Implementation Notes

- **Modules:** catalogs are owned by Nova Catalog, `backend/app/modules/catalog/`
  (`/api/v1/catalog`, see `docs/03-catalog-explorer.md`). Each source type is one
  `CatalogSource` subclass in `sources/` (endpoint, JDBC catalog, metadata
  reader); `connectors.py` builds `ALTER`/`DROP` for every catalog type.
  `backend/app/modules/external_catalogs/` keeps `/api/v1/external-catalogs` as
  an alias with the same response shape; it lists only external catalogs the
  caller can see.
- **Ranger authorizes catalogs.** Every `CREATE / ALTER / DROP` statement is
  issued on the caller's connection through the shared `QueryService` pipeline,
  so the patched FE asks Ranger (`create external catalog` on `system`, and
  `alter`/`drop` on the `catalog` resource) whether the caller's active role may
  manage catalogs. Ranger policies on `catalog/database/table/column` also govern
  reads of external tables, including row filters and masks (verified for JDBC
  and Iceberg tables; see `docs/proposals/nova-catalog-standalone.md` §6.1).
  Nova does not implement catalog permissions.
- **Engine values (4.1.4):** the Iceberg metastore selector is
  `iceberg.catalog.type`; the accepted values are `hive` (Hive Metastore),
  `hadoop` (filesystem warehouse) and `rest`. The docs' `hms` alias is **not**
  accepted by the engine — Nova maps the UI's "Hive Metastore" to `hive`.
- **Catalog store:** `NOVA_SYSTEM.CATALOG_CATALOGS` stores the catalog name,
  type, metastore URI, **storage connection name**, comment, non-secret
  properties, origin (`nova` or `engine`), and last sync. No credential-bearing
  column exists, and a credential-named property is dropped on write and on
  read. On startup, rows from the earlier `CONFIG_EXTERNAL_CATALOGS` table are
  copied once; that table is no longer written. The engine catalog remains the
  source of truth for the catalog itself.
- **Catalog tree:** `GET /api/v1/explorer/catalogs` lists external catalogs with
  their databases, and `GET /api/v1/explorer/databases/{db}?catalog=<name>` lists
  the external tables so they appear in the Database Explorer tree. Both list
  through the caller's session; `GET /external-catalogs/{name}/databases/{db}/tables`
  does too (it used to read through the system pool).
- **Querying external tables** uses the normal SQL Workspace path
  (`POST /api/v1/query/execute`); the MySQL protocol proxy is untouched.

---

## Connections and foreign catalogs

A **connection** (`NOVA_SYSTEM.CATALOG_CONNECTIONS`) records a source's type,
host, port, database, non-secret driver options, owner, comment, and a
**secret reference**. A **foreign catalog** is a StarRocks JDBC catalog built
from a connection, so every query against `catalog.schema.table` goes through
the normal Nova SQL path and Ranger authorizes it, including row filters and
masks.

- **Who can manage them.** Every connection operation (create, list, describe,
  enter credentials, drop) and `CREATE FOREIGN CATALOG` require an active
  security-admin role (`ACCOUNTADMIN`, `SECURITYADMIN`). Refusals are audited
  (`AUDIT_LOG`, `event_type = CATALOG`). The check runs before any credential is
  resolved. `CREATE EXTERNAL CATALOG` then runs as the caller, so Ranger also
  has to allow it.
- **Credentials.** `SECRET` is a reference, never a value:
  - `aws://<name>` or a Secrets Manager ARN, resolved through
    `app/storage/secrets.py` when the catalog is created. The secret holds
    `{"username": ..., "password": ...}`.
  - `nova://<connection>`, for credentials entered in the console or through
    `PUT /api/v1/catalog/connections/{name}/credentials`. They are stored
    Fernet-encrypted in `CATALOG_CONNECTION_CREDENTIALS` and never read back.

  A bare value such as `user:password` is refused as a reference, and an
  endpoint with `user@`, a URL scheme, or a credential option is refused.
- **Sources.** One `CatalogSource` subclass per type
  (`backend/app/modules/catalog/sources/`), listed in `SOURCES`:

  | Type | Endpoint | Credential | Driver |
  |------|----------|------------|--------|
  | `starrocks` | `host[:port]` (FE query port, default 9030) | user + password | MariaDB Connector/J 3.3.2 |
  | `postgres` | `host[:port]/database[?sslmode=...]` | user + password | PostgreSQL 42.7.12 |
  | `snowflake` | `account/DATABASE?warehouse=WH[&role=ROLE]` | user + programmatic access token | Snowflake JDBC 3.28.0 |

  The FE and BE images carry the drivers in `/opt/starrocks/drivers`; Nova sets
  `driver_url` to that `file://` path, so nothing is downloaded at create time.
  A Snowflake catalog's URI adds `JDBC_QUERY_RESULT_FORMAT=JSON` and Nova's
  `TIMEZONE`; its token travels as the JDBC `password`, which `SHOW CREATE
  CATALOG` never prints. A caller can only add `jdbc_*` tuning properties:
  `driver_url`, `driver_class`, `jdbc_uri`, `user`, and `password` always come
  from Nova. To add a source, subclass `CatalogSource` and list it in `SOURCES`.
- **Object kinds.** StarRocks reports every JDBC relation as a table, so each
  catalog sync asks the source which relations are views and materialized
  views: Postgres `pg_class.relkind`, Snowflake `SHOW TABLES/VIEWS/...` (no
  warehouse needed), StarRocks `information_schema`. The sync resolves the
  connection's credential for that read, keeps only kinds, and keeps the last
  known kinds when the source cannot be reached.
- **Grants.** `GRANT/REVOKE ... ON TABLE catalog.schema.table` compiles a Ranger
  policy for that catalog; a two-part name keeps `default_catalog`. A new
  catalog is open to nobody but `ACCOUNTADMIN` until a policy grants it: the
  Ranger bootstrap's role policies name `default_catalog` only.
- **Refresh.** `REFRESH CATALOG` syncs the catalog into the Nova Catalog store
  (security admin only). With `SCHEMA ... TABLE ...` it first runs
  `REFRESH EXTERNAL TABLE` on the engine as the caller.
- **REST.** `GET/POST /api/v1/catalog/connections`,
  `GET/DELETE /api/v1/catalog/connections/{name}`,
  `PUT /api/v1/catalog/connections/{name}/credentials`,
  `POST /api/v1/catalog/foreign-catalogs`. No response carries an endpoint, a
  reference, or a credential.
- **Performance.** JDBC pushdown on 4.1.4 sends simple comparison filters and
  `LIMIT` to the source; aggregation, joins, functions on columns, and
  `ORDER BY ... LIMIT` run in StarRocks after a scan
  (`docs/proposals/nova-catalog-standalone.md` §6.1, S2). Masks are applied in
  StarRocks, so the connection's account should be read-only and as narrow as
  possible.

- **Writing into Nova.** A foreign catalog is a read-only source. Copy or
  synchronise its data into Nova Primary Key tables with `INSERT ... SELECT`,
  `CREATE TABLE ... AS SELECT`, or `MERGE INTO nova_table USING catalog.schema.table
  ON ... WHEN [NOT] MATCHED ...` ([MERGE INTO](02-sql-worksheet.md#merge-into)).
  A MERGE reads the source through the caller's Ranger policies like any query;
  lineage records the external table as the source of the Nova table.
- **Writing to Postgres and Snowflake from a notebook (write-back).** The
  engine cannot write through a JDBC catalog (StarRocks 4.1.4 refuses INSERT,
  UPDATE and DELETE), and SQL keeps the catalog read-only. A Nova Notebook can
  write with `con.write(...)` and `con.merge_into(...)`, which Nova carries out
  itself ([notebook architecture](arch-20-nova-notebook.md#catalogs-and-writes-novaduck)):
  - a security admin gives the connection a **write credential** (Catalog →
    Connections → ⋯ → Notebook writes, or
    `PUT /api/v1/catalog/connections/{name}/write-credentials`). It is stored
    encrypted in `CATALOG_CONNECTION_WRITE_CREDENTIALS`, separate from the read
    credential the catalog uses, and never returned. Use an account limited to
    the tables notebooks may change;
  - each write is checked against Ranger for the caller's active role on the
    exact `catalog.schema.table` before the credential is read: INSERT to
    append, INSERT and UPDATE to merge, DELETE to overwrite or delete matches;
  - the write is one transaction on the source and is audited
    (`catalog_writeback`) with its row counts.
- **Catalog metadata.** Foreign-catalog tables, views, and materialized views
  are Nova catalog objects: they can be described, owned, tagged, classified,
  searched, and appear in lineage, the same as Nova tables.

## Credential security

The invariant (AGENTS.md §2) is that storage credentials never appear in an API
response, log, `NOVA_SYSTEM` row, audit row, or frontend state. Catalog
credentials flow like `@stage` credentials:

1. **One resolver.** `resolve_storage_credentials()` (the same function `@stage`
   uses) resolves the access/secret keys from the named storage connection. Nova
   never invents a second credential mechanism, and a request can never supply a
   secret — `ExternalCatalogAlter` and the foreign-catalog request reject any
   property whose name looks like a credential.
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
4. **JDBC catalogs** carry `user` and `password` properties. The password is
   redacted like the other credential properties (regression test:
   `backend/tests/unit/test_catalog_sources.py`), and the connection credential
   is resolved only inside the request that creates the catalog and, for the
   metadata read, inside the control-plane sync.
