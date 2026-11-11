---
title: Query data with SELECT
description: Filter, sort, join, group, and summarize data with SELECT, including CTEs, window functions, and common functions.
---

## What it is

`SELECT` reads data. It never changes anything, so you can experiment freely.
The examples on this page use two small tables, `customers` and `orders`,
created in [Create databases and tables](/docs/sql/databases-and-tables).

## Before you start

- Your active role needs read access to the tables you query.
- Choose the database in the worksheet, or run `USE DOCS_QA_SQL;` first.
- After creating the tables, run both examples under
  [Insert rows](/docs/sql/changing-data#insert-rows). Run them once: inserting
  the customers again adds duplicate rows. The examples below assume that
  you have not yet run the update and delete examples.

## Filter and sort

```sql
SELECT name, city
FROM customers
WHERE city = 'Jakarta'
ORDER BY name;
```

```text
name   city
Ana    Jakarta
Citra  Jakarta
```

Skip rows and limit how many come back:

```sql
SELECT order_id FROM orders ORDER BY amount DESC LIMIT 2 OFFSET 1;
```

## Join tables

An inner join keeps rows that match in both tables. `GROUP BY` and `HAVING`
summarize the result:

```sql
SELECT c.name, COUNT(*) AS order_count, SUM(o.amount) AS total_spent
FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.name
HAVING SUM(o.amount) > 50
ORDER BY total_spent DESC;
```

```text
name   order_count  total_spent
Citra  1            310.25
Ana    2            200.50
```

A left join keeps every customer, with empty values where nothing matches:

```sql
SELECT c.name, o.order_id
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id AND o.amount > 100
ORDER BY c.name;
```

```text
name   order_id
Ana    101
Budi   NULL
Citra  104
Dewi   NULL
```

You can also join a table to a table in a connected source by using its full
name, `catalog.database.table`. See
[Connections and external catalogs](/docs/data/connections-and-catalogs).

## Subqueries, CTEs, and UNION

A **CTE** (`WITH ...`) names a query so you can use it like a table:

```sql
WITH spend AS (
  SELECT customer_id, SUM(amount) AS total
  FROM orders
  GROUP BY customer_id
)
SELECT c.city, AVG(s.total) AS avg_spend
FROM spend s
JOIN customers c USING (customer_id)
GROUP BY c.city
ORDER BY c.city;
```

```sql
SELECT name FROM customers
WHERE customer_id IN (SELECT customer_id FROM orders WHERE amount > 100)
ORDER BY name;

SELECT city FROM customers UNION SELECT 'Medan' ORDER BY city;
```

## Window functions

A **window function** computes a value for each row from related rows,
without collapsing them into groups:

```sql
SELECT order_id, customer_id, amount,
  RANK() OVER (ORDER BY amount DESC) AS amount_rank,
  SUM(amount) OVER (PARTITION BY customer_id ORDER BY order_date) AS running_total
FROM orders
ORDER BY order_id;
```

```text
order_id  customer_id  amount  amount_rank  running_total
101       1            120.00  2            120.00
102       1            80.50   3            200.50
103       2            42.00   4            42.00
104       3            310.25  1            310.25
105       4            15.00   5            15.00
```

`LAG` reads the previous row:

```sql
SELECT order_id, amount,
  LAG(amount) OVER (ORDER BY order_date, order_id) AS previous_amount
FROM orders
ORDER BY order_date, order_id;
```

## Conditions

```sql
SELECT order_id,
  CASE WHEN amount >= 100 THEN 'large'
       WHEN amount >= 40 THEN 'medium'
       ELSE 'small' END AS size
FROM orders
ORDER BY order_id;
```

## Common functions

| Kind | Examples that ran in Nova |
| --- | --- |
| Text | `UPPER(name)`, `LOWER(city)`, `CONCAT(name, ' (', city, ')')`, `LENGTH(name)`, `SUBSTR(name, 1, 2)`, `REPLACE(city, 'a', 'A')` |
| Missing values | `COALESCE(NULL, city)`, `IFNULL(NULL, 'x')` |
| Numbers | `ROUND(310.256, 2)` gives `310.26`, `CAST('42' AS INT)` |
| Dates | `DATE_TRUNC('month', order_date)`, `DATE_FORMAT(order_date, '%Y-%m')`, `DATEDIFF('2026-06-01', order_date)`, `YEAR(order_date)`, `NOW()`, `CURRENT_DATE()` |
| Aggregates | `COUNT(*)`, `COUNT(DISTINCT city)`, `SUM`, `AVG`, `MIN`, `MAX`, `GROUP_CONCAT(name ORDER BY name SEPARATOR ', ')` |
| JSON | `payload->'user'`, `get_json_string(payload, '$.user')`, `parse_json('{"a": 1}')` |

```sql
SELECT city, GROUP_CONCAT(name ORDER BY name SEPARATOR ', ')
FROM customers
GROUP BY city
ORDER BY city;
```

```text
city      group_concat(...)
Bandung   Budi
Jakarta   Ana, Citra
Surabaya  Dewi
```

Read fields from a JSON column:

```sql
SELECT event_type,
  payload->'user' AS user_json,
  get_json_string(payload, '$.user') AS user_name
FROM events
ORDER BY event_time;
```

```text
event_type  user_json  user_name
login       "ana"      ana
purchase    "budi"     budi
```

## Date and time values

Write dates as `DATE '2026-05-01'` or just `'2026-05-01'`. Nova also accepts
the standard `TIMESTAMP '...'` form and treats it as a date and time:

```sql
SELECT TIMESTAMP '2026-05-01 10:00:00' AS ts;
```

## Query files and other sources

- Read files from a stage as if they were tables:
  [Query files in a stage](/docs/sql/query-files-in-stages).
- Read tables from connected databases:
  [Connections and external catalogs](/docs/data/connections-and-catalogs).

## Limits and errors

- The worksheet shows at most 500 rows per query. See
  [Use the SQL worksheet](/docs/sql/sql-workspace#read-the-results).
- `Unknown table` means the table is not in the current database: check the
  database picker or use the full `database.table` name.
- `Access denied` means your active role may not read the table or one of its
  columns.
