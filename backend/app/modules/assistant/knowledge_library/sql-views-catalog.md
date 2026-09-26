# Databases, catalogs, views and materialized views

Keywords: sql-views-catalog, database, schema, catalog, view, materialized view, mv, refresh, external, iceberg, hive.

StarRocks has catalogs containing databases containing tables/views. Nova's
displayed default schema is normalized by its query service. Use db.table in
the default catalog and catalog.db.table for external objects. Do not turn
Snowflake's database.schema model into invented StarRocks schemas.

```sql
CREATE DATABASE IF NOT EXISTS analytics;
SHOW DATABASES;
SHOW TABLES FROM analytics;
SELECT TABLE_NAME FROM information_schema.views
WHERE TABLE_SCHEMA = 'analytics' ORDER BY TABLE_NAME;
SHOW CREATE TABLE analytics.orders;
CREATE VIEW analytics.order_totals AS
SELECT customer_id, SUM(amount) AS total
FROM analytics.orders GROUP BY customer_id;
SHOW CREATE VIEW analytics.order_totals;
DROP VIEW IF EXISTS analytics.order_totals;
```

StarRocks does not support `SHOW VIEWS`. List SQL views using
`information_schema.views` and filter `TABLE_SCHEMA` to the requested database.
`SHOW CREATE VIEW db.view_name` inspects one view's definition.

A view stores a query; it is not refreshed like a materialized view. For an
asynchronous materialized view, choose explicit distribution and refresh:

```sql
CREATE MATERIALIZED VIEW analytics.order_totals_mv
DISTRIBUTED BY HASH(customer_id)
REFRESH MANUAL
AS SELECT customer_id, SUM(amount) AS total
FROM analytics.orders GROUP BY customer_id;
REFRESH MATERIALIZED VIEW analytics.order_totals_mv WITH SYNC MODE;
SHOW MATERIALIZED VIEWS FROM analytics;
```

Without synchronous refresh completion, inspect information_schema.task_runs
before claiming the MV is updated. A synchronous rollup MV has different
restrictions; do not mix its creation form with asynchronous MV options.

SHOW CATALOGS inspects catalogs. CREATE EXTERNAL CATALOG ... PROPERTIES (...)
is a native StarRocks family, but actual connector properties and secrets belong
to protected configuration. Do not produce live storage credentials or expose
backend storage names/paths. Use Nova's named storage connections and @stage
for user file operations. Catalog/table privileges remain caller-scoped.

Nova Semantic Views are governed application objects, separate from SQL views
and materialized views. create_semantic_view validates and publishes them.
Do not invent CREATE SEMANTIC VIEW SQL or claim CREATE VIEW publishes a semantic
model for an agent. A published view does not grant access to its source tables.

Implementation references: app/modules/query/service.py;
https://docs.starrocks.io/docs/sql-reference/information_schema/views/;
https://docs.starrocks.io/docs/sql-reference/sql-statements/View/CREATE_VIEW/;
https://docs.starrocks.io/docs/sql-reference/sql-statements/materialized_view/CREATE_MATERIALIZED_VIEW/;
https://docs.starrocks.io/docs/sql-reference/sql-statements/materialized_view/REFRESH_MATERIALIZED_VIEW/.
