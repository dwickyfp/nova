# SELECT, joins, CTEs, windows and expressions

Keywords: sql-select, select, join, cte, with, window, ranking, row_number, tanggal, date, agregasi, json, array, subquery, union, pivot.

Nova uses StarRocks SQL. SELECT clauses follow FROM, WHERE, GROUP BY, HAVING,
ORDER BY, LIMIT. Use single quotes for strings and backticks for identifiers.
Two-part names are database.table; Nova normalizes database.default.table to
database.table. Three-part native names are catalog.database.table. Confirm
names and types with DESCRIBE and SHOW CREATE TABLE when executing real queries.

```sql
WITH totals AS (
  SELECT customer_id, SUM(amount) AS revenue
  FROM analytics.orders
  WHERE order_date >= '2026-01-01' AND order_date < '2027-01-01'
  GROUP BY customer_id
)
SELECT c.customer_name, t.revenue
FROM totals t JOIN analytics.customers c ON c.customer_id = t.customer_id
ORDER BY t.revenue DESC LIMIT 10;
```

A LEFT JOIN preserves left rows; filters on the right table in WHERE can remove
unmatched rows. Use ON for right-side restrictions when preserving unmatched rows.
Window functions operate after grouping and keep rows. Filter window results
through a subquery for broad compatibility:

```sql
SELECT customer_id, order_id, amount
FROM (
  SELECT customer_id, order_id, amount,
         ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount DESC, order_id) AS rn
  FROM analytics.orders
) ranked WHERE rn <= 3;
```

Dates: DATE_TRUNC('month', order_date), DATE_ADD(order_date, INTERVAL 7 DAY),
DATEDIFF(end_date, start_date), CURRENT_DATE(), NOW(). Prefer half-open timestamp
ranges to avoid losing the last day's rows. DATE_FORMAT uses MySQL patterns
such as '%Y-%m'. Timestamp timezone follows the active engine session.
Use CAST(x AS DECIMAL(18,2)), COALESCE(x, 0), NULLIF(denominator, 0), CASE WHEN
... THEN ... ELSE ... END. COUNT(*) counts rows; COUNT(column) excludes NULL.
SUM and AVG ignore NULL; an empty input may produce NULL, not zero.

JSON: PARSE_JSON(text), JSON_QUERY(value, '$.field'), GET_JSON_STRING(text,
'$.field'); choose the function matching the input type. Arrays support
ARRAY_LENGTH, ARRAY_CONTAINS and UNNEST. Distinct values use SELECT DISTINCT;
UNION removes duplicates while UNION ALL preserves them. Use EXISTS for
semi-join intent and NOT EXISTS when NULL makes NOT IN misleading.

Do not assume Snowflake TABLE(GENERATOR()), $1 file columns, ::VARIANT or
warehouse commands. Do not promise recursive CTE support without checking the
installed engine. For a long query, EXPLAIN is read-only; EXPLAIN ANALYZE
executes its supported inner statement and can be expensive.

Implementation references: app/sql_dialect/grammar/StarRocks.g4;
https://docs.starrocks.io/docs/sql-reference/sql-statements/table_bucket_part_index/SELECT/;
https://docs.starrocks.io/docs/sql-reference/sql-functions/Window_function/.
