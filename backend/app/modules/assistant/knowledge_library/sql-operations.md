# SQL operations, diagnostics and administration

Keywords: sql-operations, show, describe, explain, analyze, statistics, monitoring, query timeout, session, variable, task, schedule, load, routine load, backup, restore, kill.

Read inspection: SHOW DATABASES; SHOW TABLES FROM analytics; DESCRIBE
analytics.orders; SHOW CREATE TABLE analytics.orders; SHOW PROCESSLIST;
SHOW VARIABLES LIKE 'query_timeout'; SELECT CURRENT_USER(), CURRENT_ROLE();
EXPLAIN SELECT * FROM analytics.orders WHERE order_id = 1;

Function discovery: SHOW FULL BUILTIN FUNCTIONS LIKE 'date_trunc';
SHOW FULL GLOBAL FUNCTIONS LIKE 'AI_%'; SHOW FULL FUNCTIONS FROM analytics;
These expose registered signatures when run with appropriate visibility. A
grammar-valid function call is not proof that the function is installed or
accepts the supplied types. Use the function's specific reference and confirm
actual signatures before execution; never invent an overload from its name.

EXPLAIN shows a plan, not measured latency. EXPLAIN ANALYZE executes a query
and must not be used as a harmless substitute for a destructive statement.
Inspect filters, estimates, scanned partitions, join distribution and query
profile before attributing a slowdown to one cause. Syntax repair is unverified
until a correlated rerun succeeds. Errors can contain secrets; redact them.

ANALYZE TABLE analytics.orders; collects statistics (a write operation).
SET query_timeout = 60; affects a session. SET GLOBAL ... affects the cluster
and requires elevated privilege. Nova may use separate database connections
for statements; do not promise arbitrary SET variables persist across HTTP
requests. USE ROLE analyst or SET ROLE analyst is a standalone Nova session
role switch; the role must be granted. Never send it mixed with unrelated SQL.

Nova CREATE TASK is intercepted and stored as task metadata; load create-task
for the exact supported SCHEDULE, AFTER, FINALIZE, WHEN and OVERLAP_POLICY
clauses. It is different from native SUBMIT TASK, which runs supported engine
work. Do not invent Snowflake task RESUME/EXECUTE forms for Nova metadata.
Use a supplied typed task service tool or the Tasks page when no SQL is provided.

Native loading families include INSERT SELECT, BROKER LOAD and ROUTINE LOAD;
source-specific properties require official reference and deployment checks.
Stream Load ingestion uses a protocol; SHOW STREAM LOAD is a native inspection
statement. Never route to a generic API tool to perform ingestion.
Storage/provider setup belongs to protected configuration.
SHOW LOAD FROM analytics and SHOW ROUTINE LOAD are job inspection; submitted
jobs are not completed until their state says so.

BACKUP, RESTORE, CREATE REPOSITORY, resource groups, storage volumes, SQL
blacklists, cluster/node changes, and plugin/UDF installation are native admin
families with deployment prerequisites. Do not invent definitions or run them
as query troubleshooting. In particular, Nova built-in global functions and
ACCOUNTADMIN are protected. Explain missing typed capabilities concretely.

Implementation references: app/modules/query/service.py, app/modules/tasks/;
https://docs.starrocks.io/docs/sql-reference/sql-statements/;
https://docs.starrocks.io/docs/sql-reference/sql-statements/cluster-management/plan_profile/EXPLAIN/.
