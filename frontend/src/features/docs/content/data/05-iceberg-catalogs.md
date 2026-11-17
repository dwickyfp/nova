---
title: Iceberg catalogs
description: Connect an Iceberg REST catalog, then query, create, change and time-travel its tables from Nova with the same access policies as your other data.
---

## What it is

**Apache Iceberg** is an open table format: a table is a set of data files in
object storage plus metadata that records every change as a **snapshot**. An
**Iceberg REST catalog** is the service that keeps the list of namespaces and
tables.

In Nova you add an Iceberg REST catalog as a **connection** and build a
**catalog** from it. Its namespaces appear as databases and its tables as
`catalog.database.table`. You can read and write them from the SQL worksheet,
join them with Nova tables, look at older snapshots (time travel), and read the
table's own history.

## Before you start

- Adding the connection and the catalog needs a security administrator role,
  such as `ACCOUNTADMIN` or `SECURITYADMIN`.
- You need:
  - the **catalog URI**: the address of the REST catalog service, as the
    query engine reaches it, for example `http://iceberg-rest:8181`;
  - an **access key ID** and **secret access key** for the object storage that
    holds the table files. Use a key limited to the warehouse location;
  - for self-hosted S3-compatible storage, the **storage endpoint** and
    **storage region**.
- REST catalogs that require a sign-in token (OAuth2) are not supported yet.
- A new catalog is private: nobody can read its tables until an access policy
  grants it. See [Permissions](#permissions).

## Add an Iceberg catalog

1. Open **Database Explorer** and click **Add catalog**.
2. Choose **Iceberg**.
3. Type a **Catalog name**, for example `lake`.
4. Under **Connection**, choose **New Iceberg connection**, then fill in:
   - **Connection name**, for example `lake_rest`;
   - **Catalog URI**;
   - **Warehouse** (optional; leave empty when the catalog service sets it);
   - **Storage endpoint** and **Storage region** (for self-hosted storage);
   - **Access key ID** and **Secret access key**.
5. Click **Add catalog**.

Nova stores the secret key encrypted and never shows it again. It is sent only
to the query engine, which uses it to read and write the table files.
`SHOW CREATE CATALOG lake` shows the keys as `***`.

The catalog then appears in Database Explorer. Its tables are listed as
**Iceberg table** and its views as **Iceberg view**.

To add only the connection, use **Add connection** on the **Connections** page
and choose **Iceberg**.

### With SQL

Keys are never typed into SQL. Create the connection with `SECRET =
'nova://<connection name>'`, enter the keys on the **Connections** page
(**Enter credentials** in the connection's actions menu), then create the
catalog:

```sql
CREATE CONNECTION lake_rest
  TYPE = iceberg
  ENDPOINT = 'http://iceberg-rest:8181?s3_endpoint=http://object-store:9000&s3_region=us-east-1'
  SECRET = 'nova://lake_rest';
CREATE FOREIGN CATALOG lake USING CONNECTION lake_rest COMMENT 'Iceberg lake';
SHOW CATALOGS LIKE 'lake';
```

If your administrator keeps the keys in a secret store, `SECRET` can name that
secret instead (for example `aws://<secret name>`), holding the access key ID
and secret access key.

The `ENDPOINT` is the catalog URI followed by optional settings:

| Setting | Meaning |
| --- | --- |
| `warehouse` | The warehouse location or name, when the catalog service does not set it. |
| `s3_endpoint` | The storage service address, for self-hosted S3-compatible storage. Nova then uses path-style addressing. |
| `s3_region` | The storage region. Default `us-east-1`. |
| `s3_path_style` | `true` or `false`, to override path-style addressing. |

## Permissions

Access is decided by the same access policies as every other catalog. As an
administrator, open **Access control** → **Data access** and grant a role the
actions it needs on the catalog's databases and tables (for example `SELECT`,
or `SELECT` and `INSERT`). See [Data access](/docs/access-control/data-access).

For example, with a role that has `SELECT` only:

```sql
SELECT id, customer FROM lake.sales.orders ORDER BY id;
INSERT INTO lake.sales.orders VALUES (9, 'eko', 1.00, '2026-11-18');
```

The `SELECT` returns the rows; the `INSERT` fails with:

```text
Access denied; you need (at least one of) the INSERT privilege(s) on TABLE orders for this operation.
```

Before the policy is granted, the `SELECT` fails with `Access denied` too, and
Database Explorer does not show the database.

## Create databases and tables

```sql
CREATE DATABASE lake.sales;
CREATE TABLE lake.sales.orders (
  id INT,
  customer STRING,
  amount DECIMAL(10, 2),
  dt DATE
) PARTITION BY (dt);
SHOW CREATE TABLE lake.sales.orders;
```

`PARTITION BY` takes columns or partition transforms. Hidden partitioning
stores the transform, so queries filter on the column itself:

```sql
CREATE TABLE lake.sales.events (
  action VARCHAR(20),
  id INT,
  dt DATE
) PARTITION BY bucket(id, 10), year(dt);
```

Column defaults need Iceberg format version 3:

```sql
CREATE TABLE lake.sales.customers (
  id INT,
  name STRING,
  status STRING DEFAULT 'active'
) PROPERTIES ("format-version" = "3");
ALTER TABLE lake.sales.customers ADD COLUMN score DOUBLE DEFAULT "50.5";
```

Create a table from a query, and change the partitioning later (partition
evolution; existing files keep their old layout):

```sql
CREATE TABLE lake.sales.orders_copy PARTITION BY (dt) AS SELECT * FROM lake.sales.orders;
ALTER TABLE lake.sales.events ADD PARTITION COLUMN month(dt);
ALTER TABLE lake.sales.events DROP PARTITION COLUMN year(dt);
```

## Write data

```sql
INSERT INTO lake.sales.orders VALUES
  (1, 'ani', 10.50, '2026-11-16'),
  (2, 'budi', 20.00, '2026-11-16'),
  (3, 'citra', 7.25, '2026-11-17');
SELECT dt, COUNT(*) AS orders, SUM(amount) AS total
FROM lake.sales.orders GROUP BY dt ORDER BY dt;
```

```text
dt          orders  total
2026-11-16  2       30.50
2026-11-17  1       7.25
```

`INSERT OVERWRITE` replaces a whole partition. Nova asks you to confirm it:

```sql
INSERT OVERWRITE lake.sales.orders PARTITION (dt = '2026-11-16') SELECT 4, 'dewi', 5.00;
SELECT id, customer, dt FROM lake.sales.orders ORDER BY id;
```

```text
id  customer  dt
3   citra     2026-11-17
4   dewi      2026-11-16
```

`DELETE` and `TRUNCATE TABLE` remove rows and also ask you to confirm:

```sql
DELETE FROM lake.sales.orders WHERE id = 3;
TRUNCATE TABLE lake.sales.orders_copy;
```

`DELETE` takes a `WHERE` condition with comparisons, `IN`, `LIKE`, `BETWEEN`,
`IS NULL` and subqueries (`IN (SELECT ...)`, `EXISTS (...)`). A table alias
after the table name is not accepted: refer to the table by name in the
subquery.

## Views

```sql
CREATE VIEW lake.sales.daily_totals AS
SELECT dt, SUM(amount) AS total FROM lake.sales.orders GROUP BY dt;
SELECT * FROM lake.sales.daily_totals ORDER BY dt;
```

A view made by another engine (for example Spark) can get a StarRocks version
of its query with `ALTER VIEW ... ADD DIALECT <query>`, and
`ALTER VIEW ... MODIFY DIALECT <query>` replaces it. A view created in Nova
already has one, so `ADD DIALECT` on it fails with
`Cannot add multiple queries for dialect starrocks`.

## Time travel

Every write makes a new snapshot. Read an older one by snapshot ID, by time,
or by branch or tag name.

```sql
SELECT snapshot_id, committed_at, operation FROM lake.sales.`orders$snapshots`;
SELECT * FROM lake.sales.orders FOR VERSION AS OF 8135386679561445604;
SELECT * FROM lake.sales.orders FOR TIMESTAMP AS OF '2026-11-16 23:17:09';
SELECT * FROM lake.sales.orders FOR TIMESTAMP AS OF now();
```

- `FOR` is optional: `VERSION AS OF ...` works too.
- The time is read in your session time zone. A time before the table's first
  snapshot fails with `Invalid temporal version`.

### Branches and tags

A **tag** names a snapshot; a **branch** is a line of snapshots you can write
to without changing the main table.

```sql
ALTER TABLE lake.sales.orders CREATE TAG `before-cleanup`
  AS OF VERSION 8135386679561445604 RETAIN 7 DAYS;
ALTER TABLE lake.sales.orders CREATE BRANCH `test-branch`
  AS OF VERSION 8135386679561445604 RETAIN 7 DAYS
  WITH SNAPSHOT RETENTION 2 SNAPSHOTS;
INSERT INTO lake.sales.orders FOR VERSION AS OF `test-branch`
  SELECT 9, 'eko', 1.00, '2026-11-18';
SELECT * FROM lake.sales.orders VERSION AS OF 'test-branch';
SELECT * FROM lake.sales.orders VERSION AS OF 'before-cleanup';
SELECT name, type, snapshot_id FROM lake.sales.`orders$refs`;
ALTER TABLE lake.sales.orders DROP BRANCH `test-branch`;
ALTER TABLE lake.sales.orders DROP TAG `before-cleanup`;
```

`WITH SNAPSHOT RETENTION` takes `<n> SNAPSHOTS`, `<n> DAYS` (or `HOURS`,
`MINUTES`), or both. A table named with a reserved word such as `order` must
be written in backticks.

## Metadata tables

Add `$<name>` to the table name, inside the backticks, to read its metadata:

| Table | What it shows |
| --- | --- |
| `orders$snapshots` | Each snapshot: ID, parent, time, operation and summary. |
| `orders$history` | When each snapshot became current. |
| `orders$refs` | Branches and tags. |
| `orders$partitions` | Each partition with its row and file counts. |
| `orders$files` | Each data file with its row count and size. |
| `orders$manifests` | The manifest files. |
| `orders$metadata_log_entries` | The table's metadata files over time. |

```sql
SELECT partition_value, record_count FROM lake.sales.`orders$partitions`;
SELECT COUNT(*) FROM lake.sales.`orders$history`;
```

Tables with format version 3 also have the row-lineage columns `_row_id` and
`_last_updated_sequence_number`:

```sql
SELECT _row_id, _last_updated_sequence_number, * FROM lake.sales.customers;
```

## Maintenance procedures

Run them with `ALTER TABLE ... EXECUTE`. They change the table's history, so
use them with care.

| Procedure | What it does |
| --- | --- |
| `rollback_to_snapshot(<snapshot_id>)` | Makes an older snapshot current again. |
| `cherrypick_snapshot(<snapshot_id>)` | Applies the changes of one snapshot onto the current one. |
| `fast_forward('<branch>', '<to branch>')` | Moves a branch forward to another branch's head. |
| `expire_snapshots(older_than = '<time>')` | Removes snapshots older than the time. A second number keeps at least that many. |
| `remove_orphan_files(older_than = '<time>')` | Deletes files no snapshot uses. |
| `rewrite_manifests()` | Rewrites manifest files for faster planning. |
| `rewrite_data_files(min_file_size_bytes = <bytes>) [WHERE <partition filter>]` | Merges small data files (compaction). |

```sql
ALTER TABLE lake.sales.orders EXECUTE rollback_to_snapshot(8135386679561445604);
ALTER TABLE lake.sales.orders EXECUTE expire_snapshots(older_than = '2023-12-17 00:14:38');
ALTER TABLE lake.sales.orders EXECUTE rewrite_manifests();
ALTER TABLE lake.sales.orders EXECUTE rewrite_data_files(min_file_size_bytes = 134217728) WHERE dt = '2026-11-16';
```

`rewrite_data_files` returns the number of files it read and wrote. Write
argument names without quotes: `"min_file_size_bytes" = 134217728` is read
as a comparison and fails with `Argument 'rewrite_all' must be a constant`.

## Keep it up to date

The query engine caches table metadata. When another tool (Spark, Flink, or a
second catalog) changes a table, refresh it:

```sql
REFRESH EXTERNAL TABLE lake.sales.orders;
```

To update the list of databases and tables Nova shows in Database Explorer and
search, click **Refresh metadata** on the catalog, or run:

```sql
REFRESH CATALOG lake;
```

## Limits and errors

- REST catalogs only. Hive Metastore, AWS Glue and JDBC-backed Iceberg
  catalogs cannot be added.
- REST catalogs that require OAuth2 sign-in, and storage keys handed out by
  the catalog service, are not supported yet.
- Only metadata-cache settings can be changed on the catalog afterwards; the
  address, storage and keys come from the connection.
- `DROP TABLE ... FORCE` removes the table from the catalog. Depending on the
  catalog service, its files may stay in storage.

| Message | Meaning |
| --- | --- |
| `Access denied; you need ... privilege(s)` | Your role has no access policy for this table. Ask an administrator. |
| `Invalid temporal version [...]` | No snapshot existed at that time. |
| `Column 'snapshot_id' cannot be resolved` | Write the metadata table inside the backticks: `` `orders$snapshots` ``. |
| `Property '...' cannot be set on an Iceberg catalog` | The address, storage and keys come from the connection; only cache settings can be changed. |
| `connector create failed ... Failed to load rest catalog` | The query engine cannot reach the catalog URI. Check the address as the engine sees it. |
