---
title: Connections and external catalogs
description: Connect a StarRocks, PostgreSQL, Snowflake or Iceberg REST source, turn it into a catalog, and query its tables next to your Nova data; connect Kafka for continuous loads.
---

## What it is

- A **connection** stores how to reach another database: its type, address,
  and the account Nova signs in with. The password is stored encrypted and is
  never shown again.
- An **external catalog** is built from a connection. Its schemas and tables
  appear in [Database Explorer](/docs/data/database-explorer) and can be
  queried as `catalog.schema.table`.

Nova can build catalogs from **StarRocks**, **PostgreSQL**, **Snowflake** and
**Iceberg** (REST catalog) connections. A **Kafka** connection backs no
catalog: [routine loads](/docs/sql/routine-load-from-kafka) read topics from it
into Nova tables. Iceberg has its own page:
[Iceberg catalogs](/docs/data/iceberg-catalogs).

## Before you start

- Managing connections needs a security administrator role, such as
  `ACCOUNTADMIN` or `SECURITYADMIN`. With another role, the **Connections**
  page says **Connections need a security admin role**.
- The other database must be reachable from Nova, and you need an account on
  it. Use an account that can only read.
- New catalogs are private: nobody can read their tables until an access
  policy grants it. See [Data access](/docs/access-control/data-access).

## See your connections

1. Open **Database Explorer**.
2. Click the **...** button (**Explorer actions**) and choose **Manage
   connections**.

The **Connections** page lists each connection with its **Name**, **Type**,
**Credentials** (**Stored in Nova**, **Secret store**, **Not needed** for a
Kafka cluster without sign-in, or **Missing**; plus **Writes allowed** when
notebooks may write to it), **Catalogs** built from it (for Kafka, the roles
that may load from it), and **Owner**. Each row's actions menu has:

| Action | What it does |
| --- | --- |
| **Create catalog** | Builds a new catalog from this connection. |
| **Roles that can load** | (Kafka) The roles that may create routine loads from it. Removing a role pauses its running loads within about a minute. |
| **Enter credentials** | Replaces the stored username and password. |
| **Notebook writes** | (PostgreSQL and Snowflake) Sets a separate account that notebooks use to write to this source. |
| **Remove** | Deletes the connection after you confirm. |

## Add a source and its catalog in one step

1. In **Database Explorer**, click **Add catalog**.
2. Choose the source type: **StarRocks**, **PostgreSQL**, **Snowflake**, or
   **Iceberg**.
3. Type a **Catalog name**, for example `sales_pg`.
4. Under **Connection**, pick an existing connection, or choose
   **New PostgreSQL connection** (or the type you picked) and fill in:
   - **Connection name**;
   - the address: for StarRocks, **FE host** and **Query port**; for
     PostgreSQL, **Host**, **Port**, and **Database**; for Snowflake,
     **Account**, **Database**, **Warehouse**, and optionally **Role**; for
     Iceberg, **Catalog URI** and optionally **Warehouse**, **Storage
     endpoint** and **Storage region**;
   - the account: **Username** and **Password** (for Snowflake, **User** and a
     **Programmatic access token**; for Iceberg, the storage **Access key ID**
     and **Secret access key**).
5. Optionally add a comment, then click **Add catalog**.

To add only a connection, click **Add connection** on the Connections page.
Under **Credentials** you can **Enter them now** or **Use a secret store
reference** (the name of a secret your administrator set up, never the
password itself). A Kafka connection with the `PLAINTEXT` or `SSL` security
protocol asks for no credentials.

## Query an external catalog

```sql
SHOW CATALOGS;
SELECT * FROM pg_shop.public.customers LIMIT 2;
```

```text
id  name
1   Ani
2   Budi
```

Join it with a Nova table:

```sql
SELECT COUNT(*)
FROM pg_shop.public.customers c
JOIN DOCS_QA_SQL.orders o ON o.customer_id = c.id;
```

External catalogs list tables, views, and materialized views. Iceberg tables
and views are labeled **Iceberg table** and **Iceberg view**.

## Keep a catalog up to date

Nova reads each catalog's list of schemas, tables, and columns and keeps it
for search and browsing. When tables are added or changed in the source:

- click **Refresh metadata** on the catalog in Database Explorer; or
- run `REFRESH CATALOG` in a worksheet:

```sql
REFRESH CATALOG pg_shop SCHEMA public TABLE customers;
```

```text
Catalog  Status  Databases  Relations  Error
pg_shop  ok      1          6          NULL
```

## Manage with SQL

`SHOW CONNECTIONS`, `DESCRIBE CONNECTION`, `CREATE CONNECTION`,
`DROP CONNECTION`, and `CREATE FOREIGN CATALOG` do the same from SQL. See
[Nova SQL reference](/docs/sql/nova-sql-reference#connections-and-external-catalogs).

## Limits and errors

- MySQL, Hive and other database types cannot be added as connections.
- A Kafka connection cannot be removed while a routine load uses it: stop the
  load first.
- If a catalog's metadata refresh fails, its databases do not appear in
  Database Explorer. The most common cause is a changed password or an account
  that was removed on the source: use **Enter credentials** to update it.
- A sync error that says `the error was withheld because it held credentials`
  hides the source's message to avoid showing secrets. Check the address and
  account on the source side.
- Removing a connection does not remove the data in the source database.
