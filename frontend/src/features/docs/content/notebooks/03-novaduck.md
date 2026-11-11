---
title: Read and write with novaduck
description: Use the notebook's shared connection to query governed data, append rows, and merge updates.
---

## Connect

Start a notebook runtime, then run this Python cell:

```python
import novaduck
con = novaduck.connect()
con.sql('SELECT 42').fetchall()
```

It returns `[(42,)]`. This is the same local connection used by SQL cells.
`con.catalogs()` lists the catalogs available to it and whether each attached
successfully. Nova's own catalog uses the name `nova`.

To run SQL in Nova itself:

```python
con.nova('SELECT CURRENT_ROLE()').fetchall()
```

The result contains your active role. `con.nova(...)` returns a relation, so
use `.fetchall()` for rows or `.df()` for a DataFrame. Relations do not support
`.fetchone()` in the notebook runtime; use `.limit(1).fetchall()` instead.

## Before writing

Your active role needs permission to change the target table. Create a
separate example database and this table in the SQL worksheet first. Replace
`notebook_examples` consistently if that database name is already in use.

```sql
CREATE DATABASE notebook_examples;
CREATE TABLE notebook_examples.orders (id INT, amount INT)
PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ('replication_num'='1');
```

The target must exist. The examples use a primary-key table so matching rows
can be updated by `merge_into`.

## Append rows

```python
con.write('SELECT 1 AS id, 100 AS amount', 'nova.notebook_examples.orders')
```

The default mode is `append`. This example returns a result reporting one
inserted row. Check the stored values:

```python
con.nova('SELECT * FROM notebook_examples.orders').fetchall()
```

The result is `[(1, 100)]`.

## Merge changes

```python
con.merge_into(
    'nova.notebook_examples.orders',
    'SELECT * FROM (VALUES (1,120),(2,200)) t(id,amount)',
    on=['id'],
)
```

Matching keys are updated and new keys are inserted. Verify the result:

```python
con.nova('SELECT * FROM notebook_examples.orders ORDER BY id').fetchall()
```

This returns `[(1, 120), (2, 200)]`.

## Replace the table's rows

> **Warning:** `overwrite` replaces the target's existing rows. Use it only
> when that is the intended change.

```python
con.write(
    'SELECT 3 AS id, 300 AS amount',
    'nova.notebook_examples.orders',
    mode='overwrite',
)
```

The same read now returns `[(3, 300)]`.

## Limits and errors

- Use `catalog.database.table` to name a target; `nova` selects Nova's catalog.
- Write calls use your permissions. A readable catalog does not imply that
  every table in it is writable.
- If a write times out or the result connection ends, its outcome is uncertain.
  Query the target before repeating it; repeating an append can duplicate data.
- Restarting the kernel clears the connection's local state. Run the import
  and connection cell again.
