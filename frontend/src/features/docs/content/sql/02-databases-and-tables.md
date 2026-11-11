---
title: Create databases and tables
description: Create a database, choose the right kind of table, add partitions, and change or remove tables with SQL.
---

## What it is

A **database** groups related tables. A **table** stores rows of data in named
columns, and every column has a **type** (number, text, date, and so on).

When you create a table in Nova you also choose:

- a **key type**, which decides what happens when two rows share the same key;
- how rows are **distributed** across the cluster (needed for every table);
- optionally, **partitions**, which split a large table by date or value so
  queries read only the parts they need.

## Before you start

- Your active role needs permission to create databases or tables. If you get
  `Access denied`, ask an administrator.
- Run the examples in a SQL file in **Workspaces**. See
  [Use the SQL worksheet](/docs/sql/sql-workspace).

## Create a database

```sql
CREATE DATABASE IF NOT EXISTS DOCS_QA_SQL;
USE DOCS_QA_SQL;
```

`IF NOT EXISTS` makes the statement safe to run twice. `USE` makes it the
current database for the tab, so the following statements can use short
table names.

## Choose a table type

| Key type | When two rows have the same key | Good for |
| --- | --- | --- |
| `DUPLICATE KEY` | Both rows are kept. | Logs, events, any data you only add to. |
| `PRIMARY KEY` | The newer row replaces the older one. Supports `UPDATE`, `DELETE`, and `MERGE INTO`. | Records that change: orders, customers, stock levels. |
| `UNIQUE KEY` | The newer row replaces the older one. | Simple "latest value wins" tables. |
| `AGGREGATE KEY` | Value columns are combined (for example summed). | Pre-summarized totals. |

> **Tip:** If you are unsure, use `PRIMARY KEY` for data you will edit and
> `DUPLICATE KEY` for data you only add.

## Examples

A duplicate-key table:

```sql
CREATE TABLE customers (
  customer_id INT NOT NULL,
  name VARCHAR(100),
  city VARCHAR(50),
  signup_date DATE
)
DUPLICATE KEY (customer_id)
DISTRIBUTED BY HASH (customer_id);
```

A primary-key table with a default value:

```sql
CREATE TABLE orders (
  order_id BIGINT NOT NULL,
  customer_id INT NOT NULL,
  order_date DATE NOT NULL,
  amount DECIMAL(10, 2),
  status VARCHAR(20) DEFAULT 'new'
)
PRIMARY KEY (order_id)
DISTRIBUTED BY HASH (order_id);
```

An aggregate table that adds up revenue per day and city:

```sql
CREATE TABLE daily_sales (
  sale_date DATE NOT NULL,
  city VARCHAR(50) NOT NULL,
  revenue DECIMAL(12, 2) SUM DEFAULT '0',
  orders BIGINT SUM DEFAULT '0'
)
AGGREGATE KEY (sale_date, city)
DISTRIBUTED BY HASH (city);

INSERT INTO daily_sales VALUES
  ('2026-05-03', 'Jakarta', 80.50, 1),
  ('2026-05-03', 'Jakarta', 20.00, 1),
  ('2026-05-03', 'Bandung', 42.00, 1);

SELECT * FROM daily_sales ORDER BY city;
```

```text
sale_date   city     revenue  orders
2026-05-03  Bandung  42.00    1
2026-05-03  Jakarta  100.50   2
```

The two Jakarta rows were combined into one.

A unique-key table, where a second insert with the same key replaces the
first:

```sql
CREATE TABLE products (
  product_id INT NOT NULL,
  name VARCHAR(100),
  price DECIMAL(10, 2)
)
UNIQUE KEY (product_id)
DISTRIBUTED BY HASH (product_id);

INSERT INTO products VALUES (1, 'Mug', 9.90), (2, 'Tote bag', 14.50);
INSERT INTO products VALUES (1, 'Mug (large)', 11.90);
SELECT * FROM products ORDER BY product_id;
```

```text
product_id  name         price
1           Mug (large)  11.90
2           Tote bag     14.50
```

## Partitions

A table partitioned by day, with partitions created automatically as data
arrives, and a JSON column:

```sql
CREATE TABLE events (
  event_time DATETIME NOT NULL,
  event_type VARCHAR(30),
  payload JSON
)
DUPLICATE KEY (event_time)
PARTITION BY date_trunc('day', event_time)
DISTRIBUTED BY RANDOM;
```

A table with fixed date ranges, a set number of buckets, and a table property:

```sql
CREATE TABLE monthly_targets (
  month_start DATE NOT NULL,
  city VARCHAR(50) NOT NULL,
  target DECIMAL(12, 2)
)
DUPLICATE KEY (month_start, city)
PARTITION BY RANGE (month_start) (
  PARTITION p2026h1 VALUES LESS THAN ('2026-07-01'),
  PARTITION p2026h2 VALUES LESS THAN ('2027-01-01')
)
DISTRIBUTED BY HASH (city) BUCKETS 4
PROPERTIES ('replication_num' = '1');
```

List a table's partitions:

```sql
SHOW PARTITIONS FROM monthly_targets;
```

## Copy a table

```sql
-- Same columns and settings, no rows
CREATE TABLE orders_archive LIKE orders;

-- New table filled from a query
CREATE TABLE big_orders AS
SELECT order_id, amount FROM orders WHERE amount > 100;
```

## Look at what exists

```sql
SHOW DATABASES LIKE 'DOCS_QA%';
SHOW TABLES;
SHOW TABLES LIKE 'order%';
SHOW FULL TABLES;            -- also says whether each one is a table or a view
DESCRIBE customers;          -- columns, types, and keys
SHOW COLUMNS FROM orders;
SHOW CREATE TABLE orders;    -- the full CREATE statement
SELECT TABLE_NAME, TABLE_TYPE
FROM information_schema.tables
WHERE TABLE_SCHEMA = 'DOCS_QA_SQL';
```

You can also browse all of this without SQL in
[Database Explorer](/docs/data/database-explorer).

## Change a table

```sql
ALTER TABLE customers ADD COLUMN email VARCHAR(200) COMMENT 'Contact email';
ALTER TABLE customers COMMENT = 'People who bought from us';
ALTER TABLE big_orders RENAME large_orders;
SHOW ALTER TABLE COLUMN;     -- progress of column changes
```

Removing a column asks you to confirm first:

```sql
ALTER TABLE customers DROP COLUMN email;
```

## Remove tables and databases

Only run this cleanup when you have finished the SQL guides. The next guides
reuse these tables; dropping the database removes all of them.

```sql
TRUNCATE TABLE orders_archive;   -- delete every row, keep the table
DROP TABLE large_orders;         -- delete the table
DROP DATABASE DOCS_QA_SQL;       -- delete the database and everything in it
```

Each of these asks you to confirm in the worksheet before it runs.

## Limits and errors

| Message | What it means |
| --- | --- |
| `table customers does not support update` | `UPDATE` works only on `PRIMARY KEY` tables. Use a primary-key table for data you edit. |
| `Access denied ...` | Your active role cannot create or change this object. |
| `Unknown table ...` | Check the name and the selected database. |
