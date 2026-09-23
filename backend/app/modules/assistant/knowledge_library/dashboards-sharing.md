# Dashboards and sharing Nova results

Keywords: dashboard, visualization, chart, widget, share, data sharing, saved query, collaboration, graph.

Dashboards organize saved visualizations and widgets for recurring inspection. A chart visualizes query results; it does not itself define or refresh the source data. Nove's `data_to_chart` can build a chart from verified results when the tool is available. The Workspaces page is where users work with SQL and saved query results; Nova Studio can compose agent-driven work in a separate surface.

Data Sharing can expose selected data through governed views, shared stages, or external catalogs. The receiver still needs the relevant source and object privileges. Before recommending a sharing mechanism, distinguish live query access from a file export and check whether the deployment supports the chosen route.

Implementation references: docs/23-dashboards.md, docs/27-data-sharing.md, docs/02-sql-worksheet.md; frontend/src/routes/_authenticated/workspaces/index.tsx. This is a concept guide, not evidence that a particular dashboard or share exists.
