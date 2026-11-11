---
title: Search records by text
description: Build a search index over text columns and find matching records.
---

## What AI Search does

An index selects the table and text columns you can search. Results show
matching text and the source record's key. Search does not change those records.

## Before you start

Your active role needs access to the source table and the columns in the index.
To create or rebuild an index, use its owner account or an authorized administrator.
Text search needs no AI provider. Meaning-based search needs a configured
embedding model: a model that turns text into values used to compare meaning.

## Search an existing index

1. Open **AI & ML → AI Search**.
2. Under **Available indexes**, select an index marked **ACTIVE**.
3. Enter words in **Search query**, such as `billing`.
4. Choose **Text match** in **Mode**, then click **Search**.
5. Read **Search results**. The key beside each result identifies its record
   in the source table. If no records match, try a word present in the indexed columns.

An index without an embedding model offers **Text match**. **Meaning match**
and **Hybrid (text + meaning)** are unavailable for that index.

## Create an index

1. Expand **Create a search index**.
2. Enter **Index name** using letters, numbers and underscores.
3. Choose a database and table in **Source relation**.
4. Select **Key columns** that uniquely identify a source row.
5. Select **Search text columns**. These columns supply the text in results.
6. Optionally select **Filter columns** and an **Embedding model**.
7. Click **Create search index** and wait for the build to become **ACTIVE**.

A newly created index can show **BUILDING** before its first version is ready.
Do not treat an accepted build request as a completed index.

## Refresh and remove

Use **Rebuild** to request a new version from the source. Review its build
status; when the new version is **READY**, click **Activate** beside it.
The previous version remains in use until you activate the replacement.
Removing an index with **Delete** removes the search resource, not the source
table.

## Limits and errors

- Key, text and filter columns must be distinct. A missing or inaccessible
  source column prevents creation or use.
- Results remain subject to the current role's access to the source.
- A build may fail even after it was accepted. Check the version status;
  changing the source or choosing a valid model may be needed before rebuilding.
