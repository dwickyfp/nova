---
title: Views and materialized views
description: Save a query as a view, or store its results as a materialized view that refreshes on a schedule.
---

## What it is

- A **view** is a saved query with a name. It stores no data; each time you
  query the view, Nova runs the saved query.
- A **materialized view** stores the query's results like a table and
  **refreshes** them, either on a schedule or when you ask. Queries against it
  are fast because the work is already done.

## Before you start

- Your active role needs permission to create views in the database and to
  read the tables the view uses.
- The examples use the `customers` and `orders` tables from
  [Create databases and tables](/docs/sql/databases-and-tables).

## Create and use a view

```sql
CREATE VIEW customer_spend AS
SELECT c.customer_id, c.name, c.city, SUM(o.amount) AS total_spent
FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.name, c.city;

SELECT * FROM customer_spend ORDER BY total_spent DESC;
```

```text
customer_id  name   city     total_spent
3            Citra  Jakarta  310.25
1            Ana    Jakarta  205.00
2            Budi   Bandung  64.00
```

## Change or replace a view

```sql
ALTER VIEW customer_spend AS
SELECT c.customer_id, c.name, SUM(o.amount) AS total_spent, COUNT(*) AS orders
FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.name;

CREATE OR REPLACE VIEW jakarta_customers AS
SELECT * FROM customers WHERE city = 'Jakarta';

SHOW CREATE VIEW jakarta_customers;
```

## Create a materialized view

This one refreshes every hour:

```sql
CREATE MATERIALIZED VIEW city_revenue_mv
DISTRIBUTED BY HASH(city)
REFRESH ASYNC EVERY (INTERVAL 1 HOUR)
AS
SELECT c.city, SUM(o.amount) AS revenue, COUNT(*) AS orders
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
GROUP BY c.city;
```

Refresh it now and wait until it finishes, then read it:

```sql
REFRESH MATERIALIZED VIEW city_revenue_mv WITH SYNC MODE;
SELECT * FROM city_revenue_mv ORDER BY city;
```

```text
city     revenue  orders
Bandung  64.00    1
Jakarta  515.25   3
```

A materialized view that refreshes only when you ask:

```sql
CREATE MATERIALIZED VIEW orders_manual_mv
REFRESH MANUAL
AS SELECT order_date, SUM(amount) AS total FROM orders GROUP BY order_date;

REFRESH MATERIALIZED VIEW orders_manual_mv;
```

Without `WITH SYNC MODE`, `REFRESH` starts the refresh and returns at once.

Change the schedule:

```sql
ALTER MATERIALIZED VIEW city_revenue_mv REFRESH ASYNC EVERY (INTERVAL 1 DAY);
```

## Check materialized views

```sql
SHOW MATERIALIZED VIEWS WHERE NAME = 'city_revenue_mv';
```

The result includes `refresh_type`, `is_active`, `last_refresh_state`
(for example `SUCCESS`), and any `last_refresh_error_message`.

You can see the same details without SQL: open
[Database Explorer](/docs/data/database-explorer), expand the database, and
click the view under **Views** or **Materialized Views**.

## Remove a view

```sql
DROP VIEW jakarta_customers;
DROP MATERIALIZED VIEW orders_manual_mv;
```

Both ask you to confirm before they run.

## Limits and errors

- If your role may not read the view, the query fails with `Access denied`.
- `SHOW FULL TABLES` lists materialized views with the type `VIEW`.
