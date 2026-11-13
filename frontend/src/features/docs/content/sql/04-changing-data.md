---
title: Add, change, and delete rows
description: Use INSERT, UPDATE, DELETE, MERGE INTO, and TRUNCATE, and understand when Nova asks you to confirm.
---

## What it is

These statements change the data in a table:

| Statement | What it does | Asks to confirm? |
| --- | --- | --- |
| `INSERT INTO ... VALUES` | Adds rows you type in. | No |
| `INSERT INTO ... SELECT` | Adds rows from a query. | No |
| `INSERT OVERWRITE ... SELECT` | Replaces all rows with the rows from a query. | No |
| `UPDATE` | Changes existing rows. | Yes |
| `DELETE` | Removes rows. | Yes |
| `MERGE INTO` | Updates, inserts, or deletes rows by matching them to another table. | Yes |
| `TRUNCATE TABLE` | Removes every row. | Yes |
| `ALTER TABLE ... TRUNCATE PARTITION` | Removes every row in the named partitions. | Yes |
| `ALTER TABLE ... REPLACE PARTITION` | Replaces partitions with temporary partitions. | Yes |
| `ALTER TABLE ... SWAP WITH` | Exchanges the rows of two tables. | Yes |

"Asks to confirm" means the worksheet shows **Run destructive query?** and
runs nothing until you click **Run query**. See
[Use the SQL worksheet](/docs/sql/sql-workspace#confirm-statements-that-change-or-delete-data).

## Before you start

- Your active role needs permission to change the table.
- `UPDATE` and `MERGE INTO` need a **primary-key** table.
  See [Create databases and tables](/docs/sql/databases-and-tables#choose-a-table-type).

## Insert rows

```sql
INSERT INTO customers VALUES
  (1, 'Ana', 'Jakarta', '2026-01-15'),
  (2, 'Budi', 'Bandung', '2026-02-03'),
  (3, 'Citra', 'Jakarta', '2026-03-20'),
  (4, 'Dewi', 'Surabaya', '2026-04-11');
```

Name the columns when you leave some out. Missing columns get their default
value (here `status` becomes `'new'`):

```sql
INSERT INTO orders (order_id, customer_id, order_date, amount) VALUES
  (101, 1, '2026-05-01', 120.00),
  (102, 1, '2026-05-03', 80.50),
  (103, 2, '2026-05-03', 42.00),
  (104, 3, '2026-05-04', 310.25),
  (105, 4, '2026-05-06', 15.00);
```

The result shows `Query completed. Affected rows: 5`.

## Insert from a query

```sql
INSERT INTO orders_archive
SELECT * FROM orders WHERE order_date < '2026-05-04';
```

Replace everything in a table with a query's rows:

```sql
INSERT OVERWRITE orders_archive
SELECT * FROM orders WHERE order_id = 104;
```

To load rows from a file, see
[Load data from files](/docs/sql/load-data-from-files).

## Update rows

```sql
UPDATE orders SET status = 'shipped' WHERE order_id = 101;
```

Nova asks you to confirm, then reports `Affected rows: 1`.

> **Warning:** Always include a `WHERE` clause. Without one, every row in the
> table changes.

## Delete rows

```sql
DELETE FROM orders WHERE order_id = 105;
```

`DELETE` also works on duplicate-key tables. On those tables the worksheet
may report `Affected rows: 0` even though the rows were removed; run a
`SELECT COUNT(*)` to check.

Remove every row but keep the table:

```sql
TRUNCATE TABLE orders_archive;
```

## Merge changes from another table

`MERGE INTO` compares a **target** table with a **source** table or query,
row by row, using the `ON` condition. For each source row:

- `WHEN MATCHED ... THEN UPDATE SET ...` changes the matching target row;
- `WHEN MATCHED ... THEN DELETE` removes the matching target row;
- `WHEN NOT MATCHED ... THEN INSERT ...` adds a new row.

Clauses are checked in order and the first whose condition holds is used.

Suppose `order_updates` holds changes to apply:

```sql
CREATE TABLE order_updates (
  order_id BIGINT NOT NULL, customer_id INT, order_date DATE,
  amount DECIMAL(10, 2), status VARCHAR(20)
)
DUPLICATE KEY (order_id)
DISTRIBUTED BY HASH (order_id);

INSERT INTO order_updates VALUES
  (102, 1, '2026-05-03', 85.00, 'shipped'),
  (103, 2, '2026-05-03', 0, 'cancelled'),
  (106, 2, '2026-05-07', 64.00, 'new');
```

Update matching orders and add new ones:

```sql
MERGE INTO orders AS t
USING order_updates AS s
ON t.order_id = s.order_id
WHEN MATCHED AND s.status <> 'cancelled' THEN
  UPDATE SET amount = s.amount, status = s.status
WHEN NOT MATCHED THEN
  INSERT (order_id, customer_id, order_date, amount, status)
  VALUES (s.order_id, s.customer_id, s.order_date, s.amount, s.status);
```

Then remove the cancelled ones, using a query as the source:

```sql
MERGE INTO orders t
USING (SELECT order_id FROM order_updates WHERE status = 'cancelled') s
ON t.order_id = s.order_id
WHEN MATCHED THEN DELETE;
```

```sql
SELECT * FROM orders ORDER BY order_id;
```

```text
order_id  customer_id  order_date  amount  status
101       1            2026-05-01  120.00  shipped
102       1            2026-05-03  85.00   shipped
104       3            2026-05-04  310.25  new
106       2            2026-05-07  64.00   new
```

### MERGE limits

| Message | What to do |
| --- | --- |
| `MERGE needs a Primary Key table; DB.table is not one` | The target must be a primary-key table. |
| `MERGE cannot combine WHEN MATCHED ... THEN DELETE with WHEN NOT MATCHED ... THEN INSERT` | Run the delete and the upsert as two `MERGE` statements, as shown above. |
| `more than one source row matches the same key` | Each target row may match at most one source row. Remove duplicates from the source first. |

## Limits and errors

| Message | What it means |
| --- | --- |
| `table customers does not support update` | `UPDATE` works only on primary-key tables. |
| `Access denied ...` | Your active role may not change this table. |

- A script that contains a statement needing confirmation does not run at all
  until you confirm.
- Statements are not wrapped in one transaction in the worksheet. If a script
  stops halfway, the statements before the failure have already been applied.
- Each statement commits when it runs, so the worksheet refuses `BEGIN`,
  `START TRANSACTION`, `COMMIT`, and `ROLLBACK`, and nothing in the script runs:

  ```text
  Transactions are not available in the SQL worksheet: each statement commits when it runs, so ROLLBACK cannot undo it. Run BEGIN, COMMIT and ROLLBACK from a MySQL client.
  ```

  To group changes and undo them, use a
  [MySQL client](/docs/sql/mysql-clients), where transactions work.
- Session settings such as `SET query_timeout = 600`, `SET @name = ...`, and
  `SET NAMES ...` are refused the same way, because the next statement would
  not see them. Put the setting in the query instead:

  ```sql
  SELECT /*+ SET_VAR(query_timeout = 600) */ COUNT(*) FROM orders;
  ```

  `SET GLOBAL ...` is still allowed for administrators; it changes the value
  for everyone.
