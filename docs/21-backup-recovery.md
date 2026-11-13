# Module 21: Backup & Recovery

> Snapshot backup, point-in-time restore, and accidental deletion recovery.

---

## Backup Concepts

| Concept | Description |
|---------|-------------|
| **Snapshot** | Consistent point-in-time copy of data |
| **Repository** | Storage location for snapshots (HDFS, S3) |
| **Recovery** | Restore deleted objects from recycle bin |

---

## Backup Operations

### Create Backup Repository

```sql
CREATE REPOSITORY backup_repo
WITH BROKER
ON LOCATION "s3://backup-bucket/snapshots"
PROPERTIES(
    "aws.s3.endpoint" = "...",
    "aws.s3.access_key" = "...",
    "aws.s3.secret_key" = "..."
);
```

### Create Snapshot

```sql
-- Full backup of database
BACKUP SNAPSHOT DATALAKE.snapshot_20260618
TO backup_repo
ON (
    TABLE orders,
    TABLE customers,
    TABLE payments
)
PROPERTIES("type" = "full", "timeout" = "3600");

-- Backup entire database
BACKUP SNAPSHOT DATALAKE.full_backup
TO backup_repo
ON (DATABASE DATALAKE);
```

### Show Backups

```sql
SHOW BACKUP FROM DATALAKE;
SHOW REPOSITORIES;
SHOW SNAPSHOT ON backup_repo;
```

### Restore

```sql
-- Restore table from snapshot
RESTORE SNAPSHOT DATALAKE.snapshot_20260618
FROM backup_repo
ON (
    TABLE orders,
    TABLE customers
)
PROPERTIES(
    "backup_timestamp" = "2026-06-18-10-00-00",
    "replication_num" = "1"
);
```

---

### BI (Superset) metadata

Superset keeps charts, dashboards, datasets, alerts and reports in its own
PostgreSQL database. Creating a snapshot of `NOVA_SYSTEM` through Nova also
saves a dump of that database in Nova's managed storage under the same
repository and label; restoring the whole `NOVA_SYSTEM` snapshot (no table list)
restores it. The response's `bi_metadata` field says whether BI metadata was
saved, restored, absent from the snapshot, or failed, and each step is in
`AUDIT_LOG` (`BACKUP_BI_METADATA`, `RESTORE_BI_METADATA`). Restart the Superset
services after a restore. Studio artifacts in `NOVA_SYSTEM` point at Superset
objects by id, so restore the two together.

---

## Recovery (Recycle Bin)

### Recover Deleted Objects

Verified against the pinned StarRocks 4.1.4 on 2026-11-12:

```sql
-- Recover a dropped table (within the retention period)
RECOVER TABLE DATALAKE.orders;

-- Recover a dropped database
RECOVER DATABASE DATALAKE;
```

`RECOVER ... AS <new_name>` and `SHOW CATALOG RECYCLE BIN` are another engine's
syntax; the pinned grammar and engine reject both. The Nova API refuses a
rename-on-recover request with 400; recover first, then rename.

### Listing dropped objects

StarRocks 4.1.4 exposes the recycle bin as
`information_schema.recyclebin_catalogs`, but the engine answers it only for
its `root` account ("operation can be executed only by root user"). Nova never
runs a caller's request as root, so `GET /backup/recycle-bin` returns 501 with
an explanation and no engine call. Users recover by exact name.

---

## Nova UI

### Backup Manager

```
┌─ Backup & Recovery ─────────────────────────────────────┐
│                                                          │
│  [Snapshots] [Recycle Bin] [Repositories]               │
│                                                          │
│  ── Snapshots ──                                         │
│  Name                Database   Tables  Size    Status   │
│  snapshot_20260618   DATALAKE   3       2.3 GB  ✅ Done │
│  full_20260617       DATALAKE   12      8.1 GB  ✅ Done │
│  snapshot_20260616   ANALYTICS  5       1.2 GB  ❌ Error│
│                                                          │
│  [+ Create Snapshot]  [Schedule Backup]                  │
│                                                          │
│  ── Recycle Bin ──                                       │
│  Object               Type      Deleted    Expires In   │
│  DATALAKE.staging     TABLE     2h ago     22h          │
│  DATALAKE.old_data    PARTITION 5h ago     19h          │
│  ANALYTICS.temp       DATABASE  1d ago     — (expired)  │
│                                                          │
│  [Recover] [View Content] [Force Delete]                 │
└──────────────────────────────────────────────────────────┘
```

### Backup Scheduler

```
┌─ Backup Schedule ───────────────────────────────────────┐
│                                                          │
│  Database: [DATALAKE ▼]                                  │
│  Schedule: [Daily at 02:00 ▼]                            │
│  Retention: [30 days ▼]                                  │
│  Tables: [All ▼] or [Select specific]                   │
│  Repository: [backup_repo ▼]                             │
│                                                          │
│  [Save Schedule]                                         │
└──────────────────────────────────────────────────────────┘
```
