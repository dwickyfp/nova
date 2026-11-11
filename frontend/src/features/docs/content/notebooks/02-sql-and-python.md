---
title: Use SQL results in Python
description: Name a SQL result, use it as a DataFrame, and display a chart.
---

## Before you start

Open a notebook and [start its runtime](/docs/notebooks/create-and-run).
Run cells in the order below: later cells use the earlier result.

## Create a named SQL result

Add a SQL cell with this query and set **Result name** to `regions`:

```sql
SELECT * FROM (VALUES ('Jakarta', 100), ('Bandung', 200)) t(region, amount)
```

Click **Run this cell**. The output contains two rows and the columns `region`
and `amount`. The default SQL engine is DuckDB, which runs inside the notebook.

## Read the result in Python

Add a Python cell below it:

```python
print('rows', regions.df().shape[0])
regions.df()
```

It prints `rows 2` and displays a DataFrame containing the SQL result. A result
name refers to live runtime state, so rerun the SQL cell after a restart.

## Query Nova data

A SQL cell can run in Nova when it needs Nova's SQL syntax and governed
catalogs. Use the cell's engine action to select Nova; the cell then says
**Runs in Nova**. **Run in DuckDB** switches it back.

This Nova-engine query shows the active role and the value `2`:

```sql
SELECT CURRENT_ROLE() AS role, 1 + 1 AS two
```

The result depends on your signed-in role. Choosing an engine does not grant
access to additional data. A Nova-engine SQL cell is for reads; use the
worksheet for database creation and administration.

## Display a chart

Run this in a Python cell:

```python
import matplotlib.pyplot as plt
plt.plot([1, 3, 2]); plt.show()
```

The image appears below the cell. Table results also offer **Table** and
**Chart** views. **Copy rows as CSV** and **Download rows as CSV** use the rows
in the displayed preview, so check any truncation message before treating an
export as the full dataset.

## Limits and errors

- SQL cells and Python variables share a runtime, but their execution order
  matters. Moving a cell does not execute it.
- A name that has not been created in the current runtime cannot be read.
- Displayed tables are previews. Keep larger computations in the runtime
  or the database instead of relying on every row being shown in the browser.
- HTML outputs cannot fetch arbitrary external scripts. A chart library that
  relies on downloading scripts may not render; matplotlib images work without that.
