---
title: Migrate databases from another cluster
description: Understand the Migrate databases page, which copies databases from another StarRocks cluster into Nova, and what it needs before it can run.
---

## What it is

**Migration** copies databases from another StarRocks cluster (the
**source**) into Nova. It works in three stages on one page, **Migrate
databases**:

1. **Source connection**: where the source cluster is and how to sign in.
2. **Choose databases**: which databases to copy.
3. **Review and run**: the plan for each database, access checks, and the job.

## Before you start

- You need a role that may create databases and tables in Nova.
- The source cluster must be reachable from Nova on its SQL port (usually
  `9030`), with an account that can read the databases to copy.
- For a password-protected account, your administrator must first store the
  password as a **credential reference** (a named secret). You never type the
  password on this page.
- Copying table **data** (not only table definitions) also needs the
  cluster sync utility installed by your administrator, and a transfer stage
  that both clusters can use. The **Source connection** section shows
  **Sync utility: available** or **Sync utility: not configured**.

## Steps

1. In the left menu, under **Operations**, click **Migration**.
2. Under **Source connection**, fill in **Connection name**, **IP or
   hostname**, **SQL port**, **Username**, and, if needed, **Credential
   reference**.
3. Click **Test connection**. When it succeeds, click **Save source and list
   databases**. Next time, pick it under **Saved connection** and click
   **List databases**.
4. Under **Choose databases**, tick the databases to copy. **Find a
   database** filters the list; **Select shown** and **Clear selection** help
   with long lists.
5. Under **Review and run**, choose whether to **Include table data**, then
   click the review button. For each database Nova shows the objects it can copy, the
   ones needing attention, the statements it will run, and a preflight access
   check (**Preflight passed** or not). The button reads, for example,
   **Review 2 selected databases**.
6. Read every plan. Tick **I reviewed every plan and any omissions. A
   restorable backup exists for the target cluster.**, type the confirmation
   phrase shown, and click **Queue migration job**.
7. Follow progress in the **Migration job** panel. Click **Refresh job status**
   to update it.

## Limits and errors

- `Could not test this connection. Check the address, account, credential
  reference, and worker, then try again.` means Nova could not reach or sign
  in to the source.
- Database names that need quoted identifiers (for example with spaces) are
  listed under **Some database names cannot be migrated yet** and are skipped.
- All selected databases must pass preflight before the job can start.
- Without the sync utility, table data is not copied.
