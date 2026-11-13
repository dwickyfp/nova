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

SQL cells run in DuckDB inside the notebook. They can also read Nova tables:
your Nova databases appear under the catalog name `nova`, so write
`nova.<database>.<table>`. You see only data your active role can read.

After you create the example table from
[Query and change Nova data with novaduck](/docs/notebooks/novaduck) and insert
its first two rows, this SQL cell returns `id` 1 with `amount` 100 and `id` 2
with `amount` 200:

```sql
SELECT * FROM nova.notebook_examples.orders
```

The output says **Computed in DuckDB**. To run a query with Nova's own SQL
syntax, use DuckDB's `mysql_query('nova', ...)`, as described on that page.
Use the SQL worksheet for database creation and administration.

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
