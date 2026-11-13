---
name: notebook
title: Work in a notebook
summary: Help with a Nova Notebook - read its cells, explain results and errors, write Python, SQL or Markdown cells for review, read and write Nova, Postgres and Snowflake data with novaduck, and run cells only with approval.
triggers: notebook, ipynb, cell, python, pandas, polars, duckdb, novaduck, con.write, merge_into, con.put, con.nova, mysql_execute, mysql_query, catalogs, dataframe, plot, chart in python, matplotlib, pygwalker, jupyter, sel, kernel, runtime, package, install, pip, read_csv, parquet, merge into, upsert, write back, stage
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

- **Only `novaduck.connect()` is Nova's.** `con = novaduck.connect()` is the
  notebook's DuckDB 2.0 connection, signed in as the person. Everything else
  is ordinary DuckDB SQL and DuckDB in Python; write code a DuckDB user
  would recognise and do not invent helpers.
- **Catalogs.** Every catalog the role sees is attached by its Nova name:
  `nova` for Nova's own tables (`nova.<database>.<table>`), and each external
  catalog by its name (`pg_shop.public.orders`; Snowflake keeps its upper-case
  names, `sf_cat.SALES.ORDERS`). `SELECT database_name FROM duckdb_databases()`
  lists what is attached. Find tables with the catalog search before naming
  them, and always write full names: `USE` and `SET search_path` cannot point
  at a Nova catalog.
- **Read and transform.** Plain DuckDB SQL across catalogs in one query: joins,
  `UNION ALL`, window functions, `QUALIFY`, `PIVOT`, `GROUP BY ALL`. Simple
  filters run in the source. For Nova-only syntax (a stage file
  `@stage/folder/name.parquet`, Nova functions) use DuckDB's
  `mysql_query('nova', '<Nova SQL>')`. DuckDB 2.0 relations have no
  `fetchone()` (use `.limit(1).fetchall()`), and `.arrow()` returns a reader:
  read it fully (`.arrow().read_all()`) before the next query.
- **Nova DDL.** `con.sql("CALL mysql_execute('nova', 'CREATE TABLE IF NOT
  EXISTS db.t (...) PRIMARY KEY (id) DISTRIBUTED BY HASH(id)')")` runs a
  statement in Nova, for example a primary-key table before upserting.
- **Write Nova tables with SQL.** `INSERT INTO nova.db.t SELECT ...` (also from
  a pandas frame by name), `UPDATE`, `DELETE ... WHERE ...` and
  `CREATE TABLE nova.db.t AS ...`. On a primary-key table `INSERT` of an
  existing key replaces the row, which is the upsert; `MERGE INTO` is not
  available on Nova tables. `DELETE` needs a `WHERE` (`WHERE true` empties a
  table); `TRUNCATE` is refused. Each statement takes effect as it runs:
  `ROLLBACK` does not undo it.
- **Postgres and Snowflake writes** are refused through SQL. Only when the
  person needs one, the connection still accepts the older calls
  `con.write(data, "catalog.schema.table", mode="append")` and
  `con.merge_into(target, data, on=["key"])`, through the catalog's write
  connection an administrator adds in Catalog; without it the call says so.
  Never suggest another workaround.
- **Stage files.** To save data into a Nova stage, the connection still accepts
  `con.put(data, "@stage/folder/name.parquet")` (`.csv` and `.json` too).
- One older write call takes at most 1,000,000 rows and 200 MB.
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
