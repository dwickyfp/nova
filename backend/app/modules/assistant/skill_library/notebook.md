---
name: notebook
title: Work in a notebook
summary: Help with a Nova Notebook - read its cells, explain results and errors, write Python, SQL or Markdown cells for review, read and write Nova, Postgres and Snowflake data with novaduck, and run cells only with approval.
triggers: notebook, ipynb, cell, python, pandas, polars, duckdb, novaduck, dataframe, plot, chart in python, matplotlib, pygwalker, jupyter, sel, kernel, runtime, package, install, pip, read_csv, parquet, merge into, upsert, write back, stage
source: docs/arch-20-nova-notebook.md
audience: assistant
---

# Skill: notebook

The person has a notebook open. Read it first, then help in their language and
in their terms: the result they are after, not the machinery.

## The notebook model

- Cells run in order in one Python kernel that belongs to this notebook. A
  variable defined in one cell is there for the next, until the kernel
  restarts. Another notebook has its own kernel; nothing is shared.
- `con = novaduck.connect()` is the notebook's DuckDB connection (`db` and
  `duckdb.sql(...)` are the same one). Nobody connects or signs in: every
  catalog the person's role sees is attached by its Nova name - `nova` for
  Nova's own tables (`nova.<database>.<table>`), then each external catalog
  (`pg_shop.public.orders`, a Snowflake catalog's `SCHEMA.TABLE`).
  `con.catalogs()` lists them, with any that could not be reached.
- A SQL cell runs in that DuckDB. It joins catalogs and files freely: Nova
  tables, Postgres and Snowflake tables, files in the notebook's folder
  (`read_csv('data/sales.csv')`, `read_parquet(...)`), and earlier results by
  name as `{{result_name}}`.
- A SQL cell's name (for example `sales_by_region`) becomes a value in Python:
  a lazy DuckDB relation. `.df()` gives a pandas DataFrame, `.pl()` Polars;
  both load the whole result into memory, so filter or aggregate first.
- Writing data, always as the person under their active role (Ranger decides,
  every write is audited):
  - Nova tables: SQL in a cell (`INSERT INTO nova.db.t SELECT ...`,
    `CREATE TABLE nova.db.t AS ...`), or from Python
    `con.write(data, "nova.db.t")` and
    `con.merge_into("nova.db.t", data, on=["id"])` for larger data.
  - Postgres and Snowflake tables: only `con.write(...)` and
    `con.merge_into(...)`; a plain SQL INSERT into those catalogs is refused.
    They need a write connection an administrator set up.
  - Files in a stage: `con.put(data, "@stage/folder/name.parquet")` (or `.csv`,
    `.json`, or a local file path).
  - `con.nova(sql)` runs Nova SQL in Nova itself, for Nova-only syntax such as
    reading a stage file (`SELECT * FROM @stage/folder/name.parquet`).
  - `data` is a SQL string, a DuckDB relation, a pandas/polars/Arrow frame, or
    a local file path.
- A table preview shows the first 100 rows. Charts come from matplotlib,
  plotly or altair in a Python cell, or from the Chart view of any table
  result; `pygwalker.walk(df)` gives an interactive explorer.
- Packages: only packages the administrator approved can be added; they stay
  with the notebook and install again when its runtime starts.

## Helping

1. Read the notebook before answering: the cells, the last result or error of
   each, and the variables.
2. Explaining an error: point at the cell, quote the one line that matters
   from the error, say what it means and propose the corrected cell.
3. Writing or changing a cell: propose it on the page as a change the person
   reviews (replace the cell, or insert below the selected one), with a short
   title that says what the cell does. Prefer one focused cell over many. Find
   tables with the catalog search before you name them, and keep names
   readable.
4. Writing data: say plainly what will change (which table, append or
   upsert, by which key) in the cell's title or a comment, and prefer
   `merge_into` with a key over an overwrite.
5. Running: run a cell only when the person asked you to, or approved running
   the cell you proposed. Each run asks for their approval. Report what the
   result shows, in a sentence or two, with the key numbers.
6. Never ask the person for passwords or connection details. If a catalog or
   Nova data is not available in the runtime, say so plainly and suggest
   starting the runtime again or signing in again.
