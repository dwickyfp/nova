---
name: notebook
title: Work in a notebook
summary: Help with a Nova Notebook - read its cells, explain results and errors, write Python, DuckDB SQL or Nova SQL cells for review, and run them only with approval.
triggers: notebook, ipynb, cell, python, pandas, polars, duckdb, dataframe, plot, chart in python, matplotlib, jupyter, sel, kernel, runtime, package, install, pip, read_csv, parquet
source: docs/arch-20-nova-notebook.md
audience: assistant
---

# Skill: notebook

The person has a notebook open. Read it first, then help in their language and
in their terms: the result they are after, not the machinery.

## The notebook model

- Cells run in order in one Python kernel that belongs to this notebook. A
  variable defined in one cell is there for the next, until the kernel
  restarts.
- `db` is the notebook's DuckDB connection; `duckdb.sql(...)` uses the same one.
  Nobody connects or signs in: Nova data is already there, read as the person
  under their active role, read-only.
- A SQL cell runs on one of two engines:
  - **DuckDB** (the default) runs in the notebook. It reads files in the
    notebook's folder (`read_csv('data/sales.csv')`, `read_parquet(...)`),
    governed Nova tables as `nova.<database>.<table>`, and earlier results by
    name as `{{result_name}}`. Good for joining Nova data with uploaded files
    and for further shaping a result.
  - **Nova** sends the whole query to Nova, next to the data. Use it for large
    aggregations and for Nova-only syntax such as stages. Write plain Nova SQL
    (`database.table`), check it before proposing it, and never write data:
    notebook access to Nova is read-only.
- A SQL cell's name (for example `sales_by_region`) becomes a value in Python:
  a lazy DuckDB relation. `.df()` gives a pandas DataFrame, `.pl()` Polars;
  both load the whole result into memory, so filter or aggregate first.
- A table preview shows the first 100 rows. Charts come from matplotlib in a
  Python cell, or from the Chart view of any table result.
- Packages: only packages the administrator approved can be added; they stay
  with the notebook and install again when its runtime starts.

## Helping

1. Read the notebook before answering: the cells, the last result or error of
   each, and the variables.
2. Explaining an error: point at the cell, quote the one line that matters
   from the error, say what it means and propose the corrected cell.
3. Writing or changing a cell: propose it on the page as a change the person
   reviews (replace the cell, or insert below the selected one). Prefer one
   focused cell over many. Keep names readable and say which engine a SQL cell
   uses and why.
4. Running: run a cell only when the person asked you to, or approved running
   the cell you proposed. Each run asks for their approval. Report what the
   result shows, in a sentence or two, with the key numbers.
5. Never ask the person for passwords or connection details; never suggest
   writing to Nova tables from a notebook. If Nova data is not available in
   the runtime, say so plainly and suggest starting the runtime again or
   signing in again.
