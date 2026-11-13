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
**Delete**. A tab's own menu (the three dots on the tab) has **Rename**,
**Download SQL**, and **Close**.

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
| **History** | Queries you ran, newest first. **Current file** shows this file's queries; **All files** shows everything. Each entry can be copied (**Copy SQL**) or run again (**Re-run query**). |
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
`TRUNCATE TABLE`, `DROP ...`, and `ALTER TABLE ... DROP COLUMN`. Adding data
with `INSERT` or creating objects does not ask.

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

- Some statements are always refused, for example dropping the `root` user,
  changing the protected `ACCOUNTADMIN` role, or writing query results out to
  files (`INTO OUTFILE`, `INSERT INTO FILES(...)`, `INTO @stage`).
- `SET sql_dialect` to anything other than StarRocks is refused.
