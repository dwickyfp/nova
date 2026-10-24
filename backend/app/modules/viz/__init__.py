"""Nova's visualization core — the Tableau-adapted analytics engine.

The model mirrors Tableau's split between the *document model* (worksheet
shelves and marks, dashboard zones), the *semantic query* (fields, filters,
aggregation, table calculations) and the *presentation model* the renderer
consumes:

* :mod:`app.modules.viz.spec` — worksheet/dashboard specifications. The
  worksheet is Nova's ``VisualSpecification``: column/row shelves, a marks
  card, filters, sort and top-N. The dashboard is a zone layout.
* :mod:`app.modules.viz.compiler` — the semantic query compiled to StarRocks
  SQL, the role Tableau gives ``tabquery`` after ``vizdataservice``.
* :mod:`app.modules.viz.showme` — chart-type requirements and ranking, ported
  from Tableau's ShowMe resolver, plus the field-placement engine that turns a
  notional (AI) spec into a worksheet.
* :mod:`app.modules.viz.presmodel` — the compute view: field classification,
  aggregation, table calculations and formatting, served to the renderer.
* :mod:`app.modules.viz.service` — execution under the caller's StarRocks
  session, artifact persistence and dashboard composition.

StarRocks replaces Tableau's Hyper engine; the agent loop and the SQL frontend
are reused unchanged.
"""
