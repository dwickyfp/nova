---
title: Connect with a MySQL client
description: Run Nova SQL from the mysql command line, a BI tool, or a programming language through Nova's MySQL-compatible endpoint.
---

## What it is

Nova speaks the **MySQL protocol**, the "language" MySQL tools use to talk to
a database. That means you can connect with tools you already have: the
`mysql` command line, database tools such as DBeaver, BI tools, or a MySQL
library in Python and other languages.

Queries sent this way go through the same Nova SQL path as the worksheet:
your account, your active role, your data access rules, `@stage` files, and
Nova's own statements all work the same.

## Before you start

- A Nova username and password.
- The host and port. Open your account menu (bottom left), click
  **Information**, and open the **Connection** tab. It shows **Host**,
  **Port**, and **Username**, and whether **SQL connection** is **Enabled**.
  The **SQL commands** tab has a ready-made command to copy.

## Steps

1. Open **Information** > **SQL commands** and copy the
   **Connect from your terminal** command. It looks like this:

   ```text
   mysql -h '<host>' -P 4406 -u <your_username> -p
   ```

2. Replace `<your_username>` with your login name and run the command.
3. Type your password when asked. It is never shown on screen.
4. Run SQL as you would in the worksheet.

In other tools, create a **MySQL** connection with the same host, port,
username, and password. Leave the database empty or set one you can read.

## Examples

These ran through the MySQL endpoint:

```sql
SELECT CURRENT_ROLE(), CURRENT_USER();
USE DOCS_QA_SQL;
SELECT order_id, amount FROM orders ORDER BY order_id LIMIT 3;
SELECT COUNT(*) FROM @docs_qa_sql_files.new_orders.csv;
SHOW CONNECTIONS;
```

```text
CURRENT_ROLE()  CURRENT_USER()
ACCOUNTADMIN    'nova_admin'@'%'
```

Switch role for the rest of the connection:

```sql
SET ROLE stream_lab_role;
```

Group changes in a transaction and undo them:

```sql
BEGIN;
INSERT INTO orders (order_id, customer_id, order_date, amount)
VALUES (301, 1, '2026-07-01', 1.00);
ROLLBACK;
SELECT COUNT(*) FROM orders WHERE order_id = 301;   -- 0
```

## Differences from the worksheet

| Topic | In a MySQL client |
| --- | --- |
| Confirmation | There is no **Run destructive query?** dialog. `UPDATE`, `DELETE`, `DROP`, and similar statements run as soon as you send them. |
| Row limit | The worksheet's 500-row limit does not apply. |
| Transactions | `BEGIN`, `COMMIT`, and `ROLLBACK` work. Run `SET ROLE` outside a transaction. |
| Refused statements | The same statements are refused, for example `DROP USER root` returns `root user cannot be dropped`. |

> **Warning:** Because there is no confirmation step, double-check `UPDATE`
> and `DELETE` statements, and always include a `WHERE` clause.

## Limits and errors

- After `SET ROLE`, if the connection's current database is one the new role
  cannot use, every statement fails with `Access denied ... on DATABASE ...`.
  Run `USE` with a database the role can read, or switch back with
  `SET ROLE <your previous role>`.
- The client must use the `mysql_native_password` sign-in method, which is the
  default for the `mysql` command line and most libraries.
- If the **Connection** tab says **SQL connection: Disabled**, your
  administrator has turned MySQL client access off for this Nova instance.
