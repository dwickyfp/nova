---
title: Back up and restore databases
description: Keep snapshots of databases and tables in a stage folder with BACKUP, and bring them back with RESTORE, as an administrator.
---

## What it is

A **backup** copies tables, with their data and definitions, into files. A
**restore** brings them back, into the same database or under new names.

Backups are kept in a **repository**: a folder in a [stage](/docs/data/stages)
that holds **snapshots**. Each snapshot is one backup, with a name you choose
and a timestamp Nova adds.

| Step | Statement |
| --- | --- |
| 1. Create a repository in a stage folder | `CREATE REPOSITORY` |
| 2. Back up a database or some tables | `BACKUP` |
| 3. Watch the backup | `SHOW BACKUP`, `SHOW SNAPSHOT` |
| 4. Restore tables from a snapshot | `RESTORE` |
| 5. Watch the restore | `SHOW RESTORE` |

## Before you start

- Your **active role must be `ACCOUNTADMIN`**. Every repository, backup and
  restore statement is refused for any other role with
  `Backup repositories, backups and restores need the ACCOUNTADMIN role: ...`.
  Switch role with `USE ROLE ACCOUNTADMIN`.
- The stage exists, and `ACCOUNTADMIN` can write to it (`INSERT` on the stage's
  database and schema). A read-only repository needs read access only.
- Run these statements in the **SQL worksheet**. A MySQL client cannot create
  a repository.

> **Warning:** A repository keeps working after you create it, so the database
> engine stores the repository's location **and the stage's storage
> credential** in its own settings until you drop the repository. Anyone who
> can use the repository can write to and read from that folder. This is why
> only `ACCOUNTADMIN` may create and use repositories. Drop a repository you no
> longer need.

The examples were run in Nova as `ACCOUNTADMIN` in the database `compat_w5_db`
with the stage `compat_w5_stage`, on these tables:

```sql
CREATE TABLE sales (id INT, region STRING, amount DOUBLE)
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) PROPERTIES ('replication_num' = '1');
INSERT INTO sales VALUES (1, 'north', 10.5), (2, 'south', 20.0), (3, 'east', 7.25);

CREATE TABLE regions (region STRING, manager STRING)
DUPLICATE KEY(region) DISTRIBUTED BY HASH(region) PROPERTIES ('replication_num' = '1');
INSERT INTO regions VALUES ('north', 'Ana'), ('south', 'Budi');
```

The test run added a suffix to the repository and snapshot names.

## Create a repository

```sql
CREATE REPOSITORY compat_w5_repo
WITH BROKER
ON LOCATION "@compat_w5_stage/backups/";
```

- `ON LOCATION` is a folder in a stage: `"@<stage>/<folder>/"`. It must name
  a folder, not the top of the stage. Folder names may use letters, digits,
  `_`, `-`, `.` and `=`.
- Write `WITH BROKER` alone. Do not add `PROPERTIES`: the stage supplies the
  storage address and credentials. A storage address, a broker name, or any
  property is refused.
- `CREATE READ ONLY REPOSITORY` creates a repository you can only restore
  from. Create it with the **same name** on the same folder as the repository
  that wrote the backups: the snapshots are stored under the repository's
  name.

List repositories. The location is shown as the stage folder:

```sql
SHOW REPOSITORIES;
```

| RepoName | IsReadOnly | Location |
| --- | --- | --- |
| compat_w5_repo | false | @compat_w5_stage/backups |

## Back up

```sql
BACKUP DATABASE compat_w5_db SNAPSHOT snap1 TO compat_w5_repo
ON (TABLE sales, TABLE regions)
PROPERTIES ("type" = "full", "timeout" = "3600");
```

A backup copies data out of the database into the stage, so the worksheet
asks you to confirm it, like an unload. It runs in the background.

| Part | Default | What it does |
| --- | --- | --- |
| `DATABASE <db>` | The worksheet's database | The database to back up. |
| `SNAPSHOT <name>` | (required) | The snapshot's name in the repository. |
| `TO <repository>` | (required) | Where the snapshot goes. |
| `ON (...)` | Every table in the database | What to back up: `TABLE t` or `TABLE t PARTITION (p1, p2)`, separated by commas. StarRocks also accepts `ALL TABLES` and views, materialized views and functions; the Nova test run covered tables. |
| `"type"` | `full` | Only full backups exist. |
| `"timeout"` | `86400` (1 day) | Seconds before an unfinished backup is cancelled. |

One backup or restore can run in a database at a time.

## Watch a backup

```sql
SHOW BACKUP FROM compat_w5_db;
SHOW SNAPSHOT ON compat_w5_repo WHERE SNAPSHOT = 'snap1';
```

`SHOW BACKUP` shows the database's latest backup job: `State` goes from
`PENDING` through `SNAPSHOTING`, `UPLOAD_SNAPSHOT`, `UPLOADING` and
`SAVE_META` to `FINISHED`, or `CANCELLED`; `Status` shows `[OK]` or the error.
`SHOW SNAPSHOT` lists the snapshots in the repository with their
`Timestamp`, which you need to restore. Add `AND TIMESTAMP = '<timestamp>'`
to see the snapshot's tables.

Cancel a running backup:

```sql
CANCEL BACKUP FROM compat_w5_db;
```

## Restore

```sql
RESTORE SNAPSHOT compat_w5_db.snap1 FROM compat_w5_repo
ON (TABLE sales)
PROPERTIES ("backup_timestamp" = "2026-11-17-00-12-10-576", "replication_num" = "1");
```

Use the `Timestamp` that `SHOW SNAPSHOT` shows for your snapshot. Restore a
table under a new name, next to the current one:

```sql
RESTORE SNAPSHOT compat_w5_db.snap1 FROM compat_w5_repo
ON (TABLE regions AS regions_copy)
PROPERTIES ("backup_timestamp" = "2026-11-17-00-12-10-576", "replication_num" = "1");
```

| Part | Default | What it does |
| --- | --- | --- |
| `SNAPSHOT <db>.<snapshot>` | (required) | The database to restore into, and the snapshot. |
| `FROM <repository>` | (required) | The repository that holds the snapshot. |
| `ON (...)` | Everything in the snapshot | The same object list as `BACKUP`; `AS <new name>` restores under another name. |
| `"backup_timestamp"` | (required) | The snapshot's `Timestamp` from `SHOW SNAPSHOT`. |
| `"replication_num"` | `3` | Copies of each restored tablet. Use `1` when the database has one storage server. |
| `"timeout"` | `86400` | Seconds before an unfinished restore is cancelled. |

A restore replaces the data of an existing table with the same name and
definition, like `INSERT OVERWRITE`, without asking for confirmation. While a
table is being restored, queries on it fail with
`Table state is not NORMAL: 'RESTORING'`.

```sql
SHOW RESTORE FROM compat_w5_db;
CANCEL RESTORE FROM compat_w5_db;
```

`SHOW RESTORE` shows the latest restore job; `State` ends at `FINISHED` or
`CANCELLED`. `CANCEL RESTORE` with no running restore says
`No restore is currently running`.

## Drop a repository

```sql
DROP REPOSITORY compat_w5_repo;
```

Nova asks you to confirm. Dropping a repository removes it from the engine,
with the stored credential. The snapshot files stay in the stage folder: a
repository created again with the same name on that folder sees them. Delete
the folder's files on the stage's page when you no longer need the backups.

## Limits and errors

| Message | Meaning |
| --- | --- |
| `Backup repositories, backups and restores need the ACCOUNTADMIN role: ...` | Your active role is not `ACCOUNTADMIN`. |
| `CREATE REPOSITORY keeps backups only in a stage. Write the location as "@<stage>/<folder>/" ...` | The location was a storage address. |
| `CREATE REPOSITORY on a stage takes no broker name or properties ...` | Remove `PROPERTIES (...)` and any broker name. |
| `CREATE REPOSITORY needs a folder inside the stage ...` | Add a folder after the stage name. |
| `Stage access denied` | `ACCOUNTADMIN` may not write to (or, for a read-only repository, read) this stage. |
| `Writing to a stage is not allowed here.` | `CREATE REPOSITORY` was sent from a MySQL client. Use the worksheet. |
| `Repository [<name>] does not exist` | Check the name with `SHOW REPOSITORIES`. |
| `Table state is not NORMAL: 'RESTORING'` | Wait until `SHOW RESTORE` shows `FINISHED`. |

- Backup and restore work on tables in this database engine, not on
  connected catalogs.
