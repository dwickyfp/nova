# Workspaces, Database Explorer, and database objects

Keywords: home, workspace, workspaces, SQL worksheet, editor, query, database explorer, catalog, schema, table, view, materialized view, function, external catalog, indexes, index.

Home at `/` is the Nova console dashboard and starting point for warehouse work. It is separate from user-created Dashboards.

Workspaces at `/workspaces` is Nova's SQL editing and result surface. Users write SQL, run it through Nova's dialect pipeline, inspect results and history, and work with saved queries. The Nova MySQL proxy exposes the same SQL translation to MySQL clients. `@stage` references are rewritten before StarRocks runs the query. Nove can draft SQL without executing; a read-only query needs the authorized `query_execute` tool.

Database Explorer at `/database-explorer` is the object browser. It navigates catalogs, databases, tables, columns, views, materialized views, stages, functions, and supported Nova Intelligence metadata. Tables hold data and have key, partition, distribution, and index choices. A standard view stores a query; a materialized view stores precomputed query results and has refresh behavior. Functions extend SQL, including Nova's AI wrappers. External Catalogs expose external systems through StarRocks; they are administered from Explorer rather than a separate top-level sidebar entry.

Indexes help selected query patterns. Full-text inverted indexes accelerate text matching; n-gram Bloom filters are another table index option. AI Search Indexes are separate managed Intelligence objects with their own versions and relevance evaluation. Do not imply that creating a database table index automatically creates an AI Search Index.

The SQL Workspace and Explorer show objects only within the user's effective privileges. A feature described in a design document may still depend on deployment configuration; use live schema tools for claims about actual objects.

Implementation references: docs/02-sql-worksheet.md, docs/03-catalog-explorer.md, docs/05-table-manager.md, docs/06-view-manager.md, docs/07-function-manager.md, docs/10-external-catalogs.md, docs/24-advanced-indexes.md; frontend/src/routes/_authenticated/workspaces/index.tsx and database-explorer.tsx.
