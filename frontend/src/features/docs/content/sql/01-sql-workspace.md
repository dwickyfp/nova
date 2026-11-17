---
title: Use the SQL worksheet
description: Open a SQL file in Workspaces, choose a database, run one or many statements, and read, chart, and export the results.
---

## What it is

**Workspaces** is where you write and run SQL. Each SQL file you open becomes a
**tab** with an editor on top and a results panel below. Nova saves your files
for you, so you can close the browser and come back later.

A few words used on this page:

| Word | Meaning |
| --- | --- |
| SQL file (worksheet) | A saved text file of SQL statements, shown as a tab. |
| Statement | One SQL command, such as one `SELECT`. Statements are separated by `;`. |
| Database | A container for tables. In Nova a schema is the same thing as a database. |
| Active role | The role your queries run under. It decides which data you can see. |

## Before you start

- You need to be signed in. Every query runs with **your** account and your
  **active role**, so you only see data that role may read.
- To read a table, your role needs permission on it. If a query fails with
  `Access denied`, ask an administrator for access or switch to another role.

## Open or create a SQL file

1. In the left menu, under **Data Management**, click **Workspaces**.
2. In the file panel on the left, make sure the **Workspaces** tab is selected.
   Your files are listed under **Personal Workspace**.
3. Click **SQL file** to create a new file. To create a **Notebook**, a
   **Folder**, or a **Shared workspace** instead, click the arrow next to
   **SQL file** and pick from the menu.
4. Click any file in the list to open it as a tab. The **+** button at the end
   of the tab strip also creates a new SQL file.

To rename, move, or delete a file or folder, right-click it in the file panel
(or click its **...** button) and choose **Rename**, **Move to...**, or
**Delete**. A file's menu also has **Download**, which saves the file to your
computer under its own name. Use it for files that do not open as a tab, such
as Excel workbooks from a scheduled dashboard export. A tab's own menu (the
three dots on the tab) has **Rename**, **Download SQL**, and **Close**.

> **Tip:** On a narrow screen the file panel folds away. Click the arrow button
> at the top of the panel (**Expand file panel**) to open it again.

## Choose your role and database

- The **active role** is shown at the top right of the editor, next to a
  person icon. It is read-only here: change it from your account menu at the
  bottom left (**Switch Role**), or run `USE ROLE` in the worksheet (see below).
  The role applies to your whole session, in every tab.
- Use the **Databases** and **Schemas** pickers next to the role to choose
  where unqualified table names are looked up. Each tab remembers its own
  database.
- Running `USE <database>` in a tab changes that tab's database too.

```sql
USE DOCS_QA_SQL;
SELECT DATABASE();
```

```text
DATABASE()
DOCS_QA_SQL
```

You can always name a table in full instead, as `database.table`, or
`catalog.database.table` for a table in a connected source.

## Run SQL

1. Type or paste SQL in the editor.
2. Click **Run** (or press **Ctrl+Enter**, **Cmd+Enter** on a Mac). Nova runs
   the statement under your cursor, or the text you have selected.
3. To run every statement in the file from top to bottom, click the arrow next
   to **Run** and choose **Run all** (**Shift+Cmd+Enter**). Run all stops at
   the first statement that fails.
4. While a query runs, the button changes to **Cancel** with a timer. Click it
   to stop waiting.

When you run several statements, the results panel shows a small tab for each
one (**Result 1**, **Result 2**, ...). A failed statement has a warning icon.

## Read the results

The results panel has three tabs:

| Tab | What it shows |
| --- | --- |
| **Results** | The rows returned, with the total row count and time. Click a column header to sort. Right-click a cell to copy it. **Export** downloads the results as an Excel file. |
| **History** | Queries you ran, newest first. **Current file** shows this file's queries; **All files** shows the queries you ran from any of your SQL files. A statement you did not confirm is marked **NOT RUN**. Each entry can be copied (**Copy SQL**) or run again (**Re-run query**). |
| **Chart** | A quick chart of the current result. |

The worksheet returns at most **500 rows** per statement. When a query returns
more, a note above the table says so. Add a `WHERE` filter or a `LIMIT`, or
summarize with `GROUP BY`, to see what you need.

Statements that change data, such as `INSERT`, show
`Query completed. Affected rows: N` instead of a table.

When a query fails, the error is shown in the results panel. **Explain** and
**Fix with Neva** ask the assistant to explain the error or suggest a fix.

### Choose when Neva asks for approval

Open **Ask Neva** and use the approval picker beside **Model**:

- **Need approval** asks before running a read-only query.
- **Always allow read-only** approves read-only tools for this conversation
  while you remain signed in. It does not approve writes.

Your approval does not carry over to a new login. Choose **Need approval** to
revoke it for the current login. Nova checks the saved approval before each
new turn; a revoked or expired approval asks again. The approval lasts at most
12 hours, and a service interruption can require you to approve again.

For an action that still needs approval, **Allow** or **Deny** applies to that
action. Either choice keeps your existing **Always allow read-only** selection.
Choose **Need approval** when you want read-only queries to ask again.
An earlier action's approval cannot be reused for a later action.

## See a query plan

Put `EXPLAIN` in front of a query to see how Nova will run it. The plan
appears in the **Results** tab with an **Explain** label.

```sql
EXPLAIN SELECT c.city, SUM(o.amount)
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
GROUP BY c.city;
```

`EXPLAIN COSTS` adds estimated costs, and `EXPLAIN ANALYZE` runs the query and
reports what actually happened.

## Format and version history

- **Format SQL** (the braces icon, or **Ctrl+Shift+F**, **Cmd+Shift+F** on a
  Mac) tidies the layout of the SQL in the editor.
- **Version history** (the clock icon) lists earlier saved versions of the
  file. Click a version to read it.

Files are saved automatically a moment after you stop typing, and again each
time you run a query.

## Confirm statements that change or delete data

Before a statement that can change or remove existing data runs, Nova asks
you to confirm. A dialog titled **Run destructive query?** appears. Click
**Run query** to go ahead or **Cancel** to run nothing.

Statements that need confirmation include `UPDATE`, `DELETE`, `MERGE INTO`,
`TRUNCATE TABLE`, `DROP ...`, `ALTER TABLE ... DROP COLUMN`, and the partition
changes `ALTER TABLE ... TRUNCATE PARTITION`, `ALTER TABLE ... REPLACE PARTITION`
and `ALTER TABLE ... SWAP WITH`. Adding data with `INSERT` or creating objects
does not ask.

When you run a script, Nova checks the whole script first. If any statement
needs confirmation, nothing runs until you confirm.

> **Note:** **Run all** sends statements one at a time. When it reaches a
> statement that needs confirmation it stops and asks; after you confirm, only
> that statement runs. Run the remaining statements again.

## Switch role from SQL

```sql
USE ROLE stream_lab_role;
```

```text
CURRENT_ROLE()   SECURITY_CONTEXT_VERSION
stream_lab_role  2
```

- Run `USE ROLE` (or `SET ROLE`) on its own. Mixing it with other statements
  in one run returns `Run USE ROLE or SET ROLE on its own, then run the other
  statements`.
- `USE ROLE DEFAULT` goes back to your default role.
- You can only switch to roles you have. `SHOW AVAILABLE ROLES` lists them,
  and `SHOW CURRENT ACCESS` shows the role you are using now.

## Session settings and variables

A worksheet keeps a **session**, like a MySQL client connection does: session
settings and user variables you set in a SQL file stay in effect for later runs
of that same file, until you clear them, switch role, or sign out.

When a run contains `SET`, `SET @variable`, `BEGIN`, `COMMIT` or `ROLLBACK`, or
the file already has settings kept from an earlier run, Nova runs every
statement of that run on one connection, as you and with your active role.
Other runs work as before.

### Change a session setting

`SET <setting> = <value>` changes an engine setting for this SQL file. The
setting applies to the statements after it in the same run and to every later
run of the file.

```sql
SET time_zone = 'Asia/Shanghai';
SELECT now(), @@time_zone;
```

```text
now()                @@time_zone
2026-11-16 22:25:17  Asia/Shanghai
```

Run `SELECT @@time_zone;` again later in the same file and it still returns
`Asia/Shanghai`. The same query in another SQL file returns the default time
zone, because each file has its own session.

Expressions are allowed, and several settings can go in one run:

```sql
SET query_mem_limit = 10 * 1024 * 1024 * 1024;
SET sql_mode = 'PIPES_AS_CONCAT';
SELECT @@query_mem_limit, 'Nova' || ' SQL' AS joined;
```

```text
@@query_mem_limit  joined
10737418240        Nova SQL
```

Other settings from the StarRocks documentation work the same way, for example
`SET enable_profile = true` or `SET dynamic_overwrite = true`. `SET SESSION
<setting> = <value>` and `SET @@session.<setting> = <value>` mean the same.

To change a setting for one query only, without keeping it, use a hint:

```sql
SELECT /*+ SET_VAR(query_timeout = 600) */ COUNT(*) FROM information_schema.tables;
```

### Clear a setting

Set it back to `DEFAULT`. Nova stops applying it to later runs.

```sql
SET time_zone = DEFAULT;
SELECT @@time_zone;
```

You can clear several at once:
`SET query_mem_limit = DEFAULT, sql_mode = DEFAULT, enable_profile = DEFAULT;`.

### Use variables

A user variable holds a value you name with `@`. Set it with `SET`, then use it
in later statements, in this run or a later run of the same file.

```sql
SET @v1 = 1, @v2 = 2;
SELECT @v1 + @v2 AS total;
```

```text
total
3
```

A variable can hold text, such as a JSON document you want to inspect:

```sql
SET @file = '{"Person": {"Name": "Ann", "Age": 30, "Pets": ["dog", "cat"]}}';
SELECT json_length(@file) AS top_level, json_length(@file, '$.Person') AS person_fields;
```

```text
top_level  person_fields
1          3
```

The value can also come from a query or an expression. Nova keeps the value
the variable had at the end of the run, not the query, so a later run does not
run the query again:

```sql
SET @total = (SELECT COUNT(*) FROM information_schema.tables);
SET @start = DATE '2026-01-01';
SELECT @start, DATE_ADD(@start, INTERVAL 1 MONTH) AS next_month;
```

To clear a variable, set it to `NULL`: `SET @v1 = NULL;`. A variable that was
never set, or was cleared, reads as `NULL`.

### What is kept between runs

| Kept for later runs of the same file | Applies to the current run only |
| --- | --- |
| Engine settings set with `SET <setting> = <value>` | `SET NAMES ...` and `SET TRANSACTION ...` |
| Variables holding numbers, text, dates, times, `true`/`false` and JSON | Variables holding an `ARRAY`, `MAP` or `STRUCT` value. The run shows the note `@tags holds an ARRAY, MAP or STRUCT value, which is kept for this run only.` |
| | An open transaction (see below) |

Kept settings and variables are cleared when you:

- set them to `DEFAULT` (a setting) or `NULL` (a variable);
- switch role, from the account menu or with `USE ROLE`;
- sign out.

They belong to you and to one SQL file. They never apply to another file, to
another person, to another of your roles, or to a MySQL client connection. A
file keeps at most 64 settings and 64 variables.

> **Note:** Query **History** shows a variable's value as `?`, for example
> `SET @token = ?`, because a variable can hold data. The value is stored
> encrypted.

## Use a transaction

A transaction groups changes so that they are all kept or none are. In the
worksheet, a transaction lives inside **one run**: start it with `BEGIN` (or
`START TRANSACTION`) and end it with `COMMIT` or `ROLLBACK` in the same run.
Select all of its statements and click **Run**, or use **Run all**: a file
that contains `BEGIN`, `COMMIT` or `ROLLBACK` is sent as one run instead of
one statement at a time.

```sql
BEGIN;
INSERT INTO notes VALUES (1, 'draft');
ROLLBACK;
SELECT COUNT(*) FROM notes;
```

```text
COUNT(*)
0
```

Commit changes to several tables together:

```sql
BEGIN;
INSERT INTO notes VALUES (1, 'first'), (2, 'second');
INSERT INTO note_log VALUES (1, 'added two');
COMMIT;
```

- If a run ends with the transaction still open, Nova rolls it back and the
  last result says `The run ended with a transaction still open, so Nova rolled
  it back and kept none of its changes. End the transaction with COMMIT in the
  same run to keep them.`
- If a statement inside the transaction fails, the run stops there and Nova
  rolls the transaction back. The failed result adds `A statement failed inside
  a transaction, so Nova rolled the transaction back and kept none of its
  changes.`
- Inside one transaction, each table can be written by one statement only, and
  a `SELECT` cannot read a table changed earlier in the same transaction. The
  engine refuses these with `NOT allowed to read or write tables that have been
  subjected to DML operations before` or `SELECT cannot read table ... modified
  earlier in the same transaction`. Put the rows of one table in one `INSERT`,
  and check the result after `COMMIT`.

## Show a result one record per block

End a statement with `\G` instead of `;` to see its result vertically, as the
MySQL command line does: one block per row, with each column name beside its
value. This helps with wide results.

```sql
SELECT now(), now(1)\G
```

```text
Row 1
 now()  2026-11-16T21:25:38
now(1)  2026-11-16T21:25:38.500000
```

`SHOW PROCESSLIST\G` and `SHOW CREATE TABLE <table>\G` work the same way.
Nova removes `\G` before it runs the statement; a `\G` inside quotes, such as
`SELECT 'a\Gb'`, is left alone. Paging and right-click copy work as in the
normal table; sorting by column is only in the normal table.

## Share files with your team

Click the arrow next to **SQL file** and choose **Shared workspace** to create
a workspace other people can see. Right-click it and choose **Share with
roles...** to pick which roles may **view** or **edit** its files. Sharing
shares the files only: when someone runs a query, it runs with their own role
and permissions.

## Limits and errors

| Message | What it means |
| --- | --- |
| `Access denied; you need (at least one of) the SELECT privilege(s) ...` | Your active role may not read that table or column. Switch role or ask for access. |
| `Unknown table 'DB.name'` | The table does not exist in the selected database, or the name is misspelled. |
| `Invalid SQL: 1:13 Unexpected or missing SQL token ...` | A syntax error. The numbers are the line and column. In a script, nothing runs until every statement is valid. |
| `The run ended with a transaction still open, so Nova rolled it back ...` | Add `COMMIT` (or `ROLLBACK`) at the end of the same run. |
| `The remembered setting <name> could not be applied again and was cleared.` | A setting kept from an earlier run is no longer accepted by the engine. Set it again if you still need it. |
| `This file's stored contents do not match what was saved, so it was not opened. ...` | Nova checks every file and saved version against what was saved. The stored copy was changed or damaged outside Nova, so it is not shown. Ask an administrator to restore it. Other versions of the file may still open. |

- Some statements are always refused, for example dropping the `root` user or
  changing the protected `ACCOUNTADMIN` role. Writing query results to files
  works only into a stage you may write to, after you confirm; see
  [Unload data to a stage](/docs/sql/unload-data-to-stages).
- `SET sql_dialect` to anything other than StarRocks is refused.
- One `SET` cannot change a server-wide (`GLOBAL`) setting and a session
  setting together: `Run SET GLOBAL on its own: one SET cannot change a
  server-wide setting and this worksheet's session together`.
- `USE ROLE` and `SET ROLE` must run on their own, as described above. A role
  switch clears the settings and variables kept by your SQL files.
