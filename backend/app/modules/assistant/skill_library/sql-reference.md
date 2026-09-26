---
name: sql-reference
title: Nova SQL reference and compatibility
summary: Select the implemented Nova or StarRocks 4.1 SQL family; search_knowledge provides exact syntax, examples and unsupported forms.
triggers: sql, query, syntax, sintaks, select, join, cte, window, insert, update, delete, create, alter, drop, materialized, show, explain, database, catalog, function, grant
source: app/sql_dialect/grammar/StarRocks.g4, app/modules/query/service.py, knowledge_library/sql-*.md
---

# Nova SQL reference

Nova speaks StarRocks 4.1 SQL plus explicitly implemented Nova extensions.
Snowflake examples are workflow inspiration, not a compatible SQL grammar.
Use search_knowledge for the relevant SQL topic before drafting an unfamiliar
statement. Do not guess syntax from its name.

For any native statement or clause beyond the examples, search_knowledge accepts
`syntax:CREATE TABLE`, `syntax:SHOW RESOURCE GROUP`, or an exact grammar rule
such as `syntax:createTableStatement`. This reads the same StarRocks 4.1.4
grammar packaged with Nova. Follow referenced rule names when needed. It covers
the entire packaged grammar, but does not prove runtime support, installed
functions, object types or permissions. Run validate_sql on the completed draft;
if invalid, repair it and check again before answering.

Topics: sql-select (joins, CTEs, windows, dates, JSON, arrays), sql-tables
(table models, CTAS, DDL, DML), sql-views-catalog (databases, external catalogs,
views, materialized views), sql-operations (SHOW, EXPLAIN, statistics, sessions,
tasks, load jobs), sql-extensions (Nova interceptions and unsupported forms).
Accounts use create-user; stage loading uses copy-into; ML uses create-ml-model
or native-ml; AI uses ai-functions.

Drafting is not execution. "Buatkan query" means return SQL, including writes.
Preserve supplied object names. Label example names and unresolved placeholders.
Inspect real schema with query_execute when needed for execution. Never
fabricate a table, column, privilege, count or successful result.

Use query_execute for approved reads and standalone role switches, query_mutate
for explicitly requested writes with per-call approval, and typed internal
functions for non-SQL operations. provision_user collects protected input.
Never invoke generic API tools or present HTTP routes as the user's solution.
Tool availability does not confer database privileges.

Give complete SQL and a short explanation of prerequisites and effect. Keep
additional claims precise: statements that syntax is unsupported, or that a
table model is required, need the same reference check as the main draft.
Avoid unrelated alternatives and unverified internal table names.
Use ordinary action names in the answer; users do not need internal tool names.
Keep ACCOUNTADMIN immutable, storage access through @stage, and secrets out of chat.
Never execute unresolved placeholders. Batches stop at the first error; earlier
statements are not rolled back. Unsupported syntax deserves a concrete supported
alternative, not a fabricated Snowflake-style command.

Within a conversation, preserve named objects, date ranges and filters until
corrected. New conversations do not inherit them. Role or login changes require
fresh authorized evidence.
