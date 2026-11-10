---
name: notebook
title: Work in a notebook
summary: Help with a Nova Notebook - read its cells, explain results and errors, write Python, SQL or Markdown cells for review, read and write Nova, Postgres and Snowflake data with novaduck, and run cells only with approval.
triggers: notebook, ipynb, cell, python, pandas, polars, duckdb, novaduck, con.write, merge_into, con.put, con.nova, mysql_execute, catalogs, dataframe, plot, chart in python, matplotlib, pygwalker, jupyter, sel, kernel, runtime, package, install, pip, read_csv, parquet, merge into, upsert, write back, stage
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
- `con = novaduck.connect()` gives the notebook's DuckDB connection, already
  connected to Nova; `novaduck` is ready in every notebook without an import
  (`db` and `duckdb.sql(...)` are the same connection). Nobody connects or
  signs in.
- A SQL cell runs in that DuckDB. It joins catalogs and files freely: Nova
  tables, Postgres and Snowflake tables, files in the notebook's folder
  (`read_csv('data/sales.csv')`, `read_parquet(...)`), and earlier results by
  name as `{{result_name}}`.
- A SQL cell's name (for example `sales_by_region`) becomes a value in Python:
  a lazy DuckDB relation. `.df()` gives a pandas DataFrame, `.pl()` Polars;
  both load the whole result into memory, so filter or aggregate first.
- A table preview shows the first 100 rows. Charts come from matplotlib,
  plotly or altair in a Python cell, or from the Chart view of any table
  result; `pygwalker.walk(df)` gives an interactive explorer.
- Packages: only packages the administrator approved can be added; they stay
  with the notebook and install again when its runtime starts.

## novaduck: what `con` can do

Everything runs as the person under their active role: Ranger decides what
they may read and change, ACCOUNTADMIN protections apply, and every write is
audited.

- **Catalogs.** Every catalog the role sees is attached by its Nova name:
  `nova` for Nova's own tables (`nova.<database>.<table>`), and each external
  catalog by its name (`pg_shop.public.orders`; Snowflake keeps its upper-case
  names, `sf_cat.SALES.ORDERS`). `con.catalogs()` lists them with `attached`
  and `error`; a catalog that could not be reached is listed, not attached.
  Find tables with the catalog search before naming them.
- **Read and transform.** Plain DuckDB SQL across catalogs in one query: joins,
  `UNION ALL`, window functions, `GROUP BY`. Simple filters run in the source.
  `con.nova(sql)` runs Nova SQL in Nova itself, for Nova-only syntax such as a
  stage file (`SELECT * FROM @stage/folder/name.parquet`) or Nova functions.
- **Nova DDL.** `con.execute("CALL mysql_execute('nova', 'CREATE TABLE IF NOT
  EXISTS db.t (...) PRIMARY KEY (id) DISTRIBUTED BY HASH(id)')")` runs a
  statement in Nova. Use it to create a primary-key table before upserting.
  DuckDB SQL can also write Nova directly (`INSERT INTO nova.db.t SELECT ...`,
  `CREATE TABLE nova.db.t AS ...`), fine for small results.
- **Write** `con.write(data, "catalog.schema.table", mode="append")`;
  `mode="overwrite"` replaces the rows. Two-part names mean Nova
  (`"sales.t"` is `nova.sales.t`).
- **Upsert** `con.merge_into(target, data, on=["key", ...],
  when_matched="update", when_not_matched="insert")`. `when_matched` can be
  `"update"`, `"delete"` or `"ignore"`; `when_not_matched` `"insert"` or
  `"ignore"`. The target table must exist with those key columns. It returns
  the counts (`inserted`, `updated`, `deleted`, `rows`; Nova reports `rows`),
  and running it again with the same data updates instead of duplicating.
- **Postgres and Snowflake writes** go only through `con.write` and
  `con.merge_into`, through the catalog's write connection that an
  administrator adds in Catalog. A plain SQL INSERT into those catalogs is
  refused; without a write connection the call says so. Never suggest a
  workaround.
- **Stages** `con.put(data, "@stage/folder/name.parquet")` saves data (or a
  local file) into a Nova stage as `.parquet`, `.csv` or `.json`.
- `data` is a SQL string, a DuckDB relation (such as a SQL cell's result), a
  pandas, Polars or Arrow frame, or a local file path. One write takes at most
  1,000,000 rows and 200 MB; aggregate or split larger data.
- A worked example covering all of this (read Nova, Postgres and Snowflake,
  transform, upsert into Nova and Postgres, check) is the "novaduck tour"
  notebook; offer to build the same steps for someone getting started.

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
   `merge_into` with a key over an overwrite. If the target table does not
   exist yet, propose the cell that creates it first.
5. Running: run a cell only when the person asked you to, or approved running
   the cell you proposed. Each run asks for their approval. Report what the
   result shows, in a sentence or two, with the key numbers.
6. Never ask the person for passwords or connection details. If a catalog or
   Nova data is not available in the runtime, say so plainly and suggest
   starting the runtime again or signing in again.
