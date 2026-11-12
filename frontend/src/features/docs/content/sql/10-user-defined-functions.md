---
title: Create your own functions
description: Write a reusable SQL function once, call it in any query, and remove it when you no longer need it.
---

## What it is

A **user-defined function** is a small formula you name and store in a
database. After you create it, you call it in a query like a built-in function.
Nova's own AI functions, such as `ai_complete`, are global functions that Nova
manages for you.

## Before you start

- Open the SQL worksheet: in the sidebar, choose **Workspaces**, then open or
  create a SQL file.
- Your active role needs permission to create functions in the target database.
  The examples use a separate database, `docs_udf_examples`. Create it first, or
  use a database you are allowed to change:

```sql
CREATE DATABASE docs_udf_examples
```

## Create a function

1. Write `CREATE FUNCTION`, the database and function name, the arguments with
   their types, then `RETURNS` and the formula.
2. Click **Run**.

```sql
CREATE FUNCTION docs_udf_examples.add_one(x INT) RETURNS x + 1
```

Text arguments need a length:

```sql
CREATE FUNCTION docs_udf_examples.greet(name VARCHAR(100)) RETURNS concat('Hello, ', name)
```

## Use a function

Call it with the database name in front:

```sql
SELECT docs_udf_examples.add_one(41) AS answer
```

The result is `42`.

```sql
SELECT docs_udf_examples.greet('Nova') AS greeting
```

The result is `Hello, Nova`.

To see the functions in a database, open **Database Explorer** and expand the
database; its **Functions** appear with its other objects.

## Remove a function

Name the argument types, exactly as you created them:

```sql
DROP FUNCTION docs_udf_examples.add_one(INT)
```

```sql
DROP FUNCTION docs_udf_examples.greet(VARCHAR(100))
```

## Limits and errors

- **Varchar size must be > 0**: a `VARCHAR` argument was created without a
  length. Write `VARCHAR(100)` or another length.
- **Unexpected or missing SQL token at end of input** after `DROP FUNCTION`:
  the argument types are missing. Add them in brackets, for example `(INT)`.
- **No matching function with signature**: the function does not exist with
  those argument types. Check the database name and the value types you pass.
- Nova's global AI functions, such as `ai_complete`, are described in
  [AI SQL functions](/docs/ai-ml/ai-sql-functions).
