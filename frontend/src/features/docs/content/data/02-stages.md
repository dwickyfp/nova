---
title: Stages
description: Create a stage, upload, browse, download, and delete its files, and use them in SQL.
---

## What it is

A **stage** is a named folder of files that Nova can read. Each stage belongs
to one database. You upload files to it, then query or load them with SQL by
writing `@` and the stage name, for example `@docs_qa_sql_files`.

You never handle storage passwords or locations: the stage keeps them, and
Nova checks your access to the stage's database before it opens a file.

## Before you start

- Your active role needs access to the database the stage belongs to. Creating
  a stage, uploading, and deleting files need write access.
- Stage names use lowercase letters, numbers, and underscores.

## Create a stage

1. Open **Database Explorer** and expand the database that should own the
   stage.
2. Right-click the **Stages** group under it (it is there even when the
   database has no stages yet) and choose **Create Stage**.
3. In **Stage Name**, type a name such as `docs_qa_sql_files`.
4. Click **Create Stage**.

## Upload files

1. In **Database Explorer**, click the stage.
2. Click **Upload**.
3. Drag files onto the box, or click **Drop files here or click to browse**.
4. Optional: type a **Folder** such as `incoming` to put the files in a
   subfolder.
5. Click **Upload**.

The stage's file list shows each file's name, size, and date. Click a folder
to open it. Click the delete button next to a file and confirm **Delete
File** to remove it; this cannot be undone.

## Use the files in SQL

```sql
LIST @docs_qa_sql_files;
SELECT * FROM @docs_qa_sql_files/new_orders.csv;
COPY INTO orders FROM @docs_qa_sql_files/new_orders.csv;
```

See [Query files in a stage](/docs/sql/query-files-in-stages) and
[Load data from files](/docs/sql/load-data-from-files).

## Stage Manager

The **Stage Manager** page lists every stage you can see, across databases.
Open it by going to `/stages` in the address bar of the Nova console (it has
no menu item). It shows each stage's **Scope** (database and schema),
**Storage connection**, and **Created** date.

- **Create stage** opens a form with **Name**, **Database**, **Schema**,
  **Storage connection** (a connection name from the server configuration,
  such as `production`), and an optional **Base prefix**.
- **Browse** opens a stage. There you can **Upload**, open folders, download a
  file by clicking it, and copy a file's query reference (for example
  `@docs_qa_sql_files.new_orders.csv`) to paste into SQL.
- The delete button removes the stage after you confirm.

> **Warning:** Deleting a stage removes only its registration. Files already
> stored under it are not deleted. Delete the files first if you no longer
> need them.

## Limits and errors

- JSON files can be uploaded but not queried. Use CSV, TSV, or Parquet.
- `Stage '<name>' not found in <database>` means the stage belongs to another
  database or was deleted.
- `Access denied` means your active role may not use the stage's database.
