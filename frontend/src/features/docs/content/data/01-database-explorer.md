---
title: Browse data in Database Explorer
description: Find catalogs, databases, tables, views, and stages, read their columns and definitions, preview rows, and add business metadata.
---

## What it is

**Database Explorer** shows everything your active role can see, as a tree:

- a **catalog** is a top-level source of data. **Nova Catalog** holds the
  tables stored in Nova; other catalogs read from connected databases such as
  PostgreSQL or Snowflake;
- each catalog holds **databases** (also called schemas);
- each database holds **Tables**, **Views**, **Materialized Views**,
  **Stages**, **Functions**, **Pipes**, **Tasks**, and Nova objects such as
  **Semantic Views** and **Entities**. Databases in other catalogs show only
  **Tables**, **Views**, and **Materialized Views**.

Nothing on this page changes your data, except creating a stage, adding a
catalog, and editing business metadata.

## Before you start

You only see objects your active role may access. To see more, switch role
from your account menu (**Switch Role**).

## Find an object

1. In the left menu, under **Data Management**, click **Database Explorer**.
2. Expand a catalog, then a database, then a group such as **Tables**.
3. Click an object to see its details on the right.

To find something quickly:

- type in **Filter tree** at the top of the tree to narrow what is shown;
- or type in **Search the catalog** at the top of the page, then pick a result.
  Type `tag:pii` (or `tag:domain=sales`) to find objects with a tag.

The **...** button (**Explorer actions**) at the top right has **Refresh**,
**Copy qualified name** (for example `default_catalog.DOCS_QA_SQL.orders`),
and **Manage connections**.

## Table details

Click a table to open two tabs:

- **Information**: the table's model (for example **Primary Key**), row
  count, distribution, properties, every column with its type, the full
  **Table DDL** (the `CREATE TABLE` statement), and **Business metadata**.
- **Preview Data**: the first 10 rows. Click **Refresh** to load them again.

Views and materialized views show their definition (the query behind them).
Materialized views also show their refresh type, whether they are active,
the last refresh state, and the **Last Refresh Error** if one failed.

Clicking a database shows a summary of everything in it; clicking a catalog
lists its databases.

## Business metadata

Under **Information**, the **Business metadata** section lets you describe a
table for other people. It does not change who can read the data; access
rules still decide that.

| Field | How to change it |
| --- | --- |
| **Description** | Click **Edit**, type, then **Save**. |
| **Owner** | Click **Edit**, type a role name, then **Save**. |
| **Tags** | Type `key` or `key=value` (for example `domain=sales`) in the tag box and press Enter. Click a tag's remove button to delete it. |
| Column details | Click the edit button next to a column to set its **Classification** (**PUBLIC**, **INTERNAL**, **CONFIDENTIAL**, or **RESTRICTED**), **Description**, and **Tags**. |

**Lineage** lists the tables this table was filled from and the tables filled
from it. Successful `INSERT ... SELECT` and `CREATE TABLE ... AS SELECT` queries
in Nova appear after the next catalog refresh. You see a relationship only
when your active role can access both tables. For example, after

```sql
INSERT INTO orders_archive SELECT * FROM orders WHERE order_date < '2026-05-04';
```

the lineage of `orders_archive` shows `default_catalog.DOCS_QA_SQL.orders`
upstream. **Used by** lists Semantic Views and Entities built on the table.

## Stages in the tree

Each database can have **Stages**: named folders of files. Click a stage to
see its files, upload new ones, and delete old ones. Right-click the
**Stages** group and choose **Create Stage** to add one. See
[Stages](/docs/data/stages).

## Catalogs

Click a catalog to see its source, its databases, and when its metadata was
last synced.

- **Add catalog** (top of the page, or on a catalog) connects a new source.
  See [Connections and external catalogs](/docs/data/connections-and-catalogs).
- **Refresh metadata** re-reads the list of databases, tables, and columns
  from the source. It is shown to security administrators.
- **Drop catalog** removes a catalog that was added through Nova.

## Limits and errors

- **No objects found** in the tree means your role sees nothing that matches
  the filter.
- `<name> is not listed for your role in <database>` after a search means the
  object exists but your active role cannot see it.
- A catalog whose source cannot be reached shows a sync error and no
  databases. Ask the owner of the connection to check its address and
  credentials.
