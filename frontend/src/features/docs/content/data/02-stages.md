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

- Your active role needs access to the stage's scope. Creating or deleting a
  stage requires `DELETE`; uploading requires `INSERT`. Browsing and downloading
  require `SELECT` or `INSERT`.
- Stage names use lowercase letters, numbers, and underscores.

## Create a stage

1. Open **Database Explorer** and expand the database that should own the
   stage.
2. Right-click the **Stages** group under it (it is there even when the
   database has no stages yet) and choose **Create Stage**.
3. In **Stage Name**, type a name such as `docs_qa_sql_files`.
4. Click **Create Stage**.

Each new stage starts with its own empty folder. Creating another stage with
the same name after deletion does not reconnect the old files. Existing stages
keep their files and locations.

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
  such as `production`). A connection name must be configured before you use it.
- When your active role is **ACCOUNTADMIN**, the form also offers **Base prefix**
  to register an existing folder. Leave it empty to create a new folder. Having
  ACCOUNTADMIN assigned while another role is active does not grant this access.
- **Browse** opens a stage. There you can **Upload**, open folders, download a
  file by clicking it, and copy a file's query reference (for example
  `@docs_qa_sql_files.new_orders.csv`) to paste into SQL.
- **Upload** saves files in the folder you are viewing. Return to the stage
  root first if you want to upload there.
- The delete button removes the stage after you confirm.

> **Warning:** Deleting a stage removes only its registration. Files already
> stored under it are not deleted. Delete the files first if you no longer
> need them.

## Limits and errors

- JSON files can be uploaded but not queried. Use CSV, TSV, or Parquet.
- `Stage '<name>' not found in <database>` means the stage belongs to another
  database or was deleted.
- `Access denied` means your active role may not use the stage's database.
- `Storage connection is not configured` means the connection name is unknown.
  Ask your administrator for the configured name.
- Names, scope fields, and connection names must fit within 128 UTF-8 bytes;
  **Base prefix** allows 512. Some characters use more than one byte.
