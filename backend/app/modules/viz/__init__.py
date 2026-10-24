"""Nova's visualization core: worksheets, calculations, dashboards.

The core keeps three layers apart:

* :mod:`app.modules.viz.spec` — worksheet/dashboard specifications. A
  worksheet is column/row shelves of field pills, a marks card, filters,
  sort, top-N and calculated fields; a dashboard is a zone layout.
* :mod:`app.modules.viz.calc` — the calculation language: formulas with
  row-level, aggregate, level-of-detail and table-calculation functions,
  parsed, type-checked and lowered to StarRocks SQL.
* :mod:`app.modules.viz.compiler` — the worksheet compiled to one layered
  StarRocks query (context filters, fixed LODs, dimension filters, view
  aggregation, measure filters, table calculations).
* :mod:`app.modules.viz.showme` — chart-type requirements and ranking, plus
  the field-placement engine that turns a chart intent into a worksheet.
* :mod:`app.modules.viz.presmodel` — the presentation model: field
  classification, aggregation, table calculations and formatting, served to
  the renderer.
* :mod:`app.modules.viz.service` — execution under the caller's StarRocks
  session, artifact persistence and dashboard composition.

StarRocks is the only execution engine; the agent loop and the SQL guard are
reused unchanged.
"""
