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

## Recovery (Recycle Bin)

### Recover Deleted Objects

```sql
-- Recover dropped table (within retention period)
RECOVER TABLE DATALAKE.orders;

-- Recover dropped database
RECOVER DATABASE DATALAKE;

-- Recover dropped partition
RECOVER TABLE DATALAKE.orders PARTITION p202601;

-- Recover with rename (if name conflicts)
RECOVER TABLE DATALAKE.orders AS orders_restored;
```

### Recycle Bin Management

```sql
-- Show recoverable objects
SHOW CATALOG RECYCLE BIN;

-- Configure retention
ADMIN SET FRONTEND CONFIG ("catalog_trash_expire_second" = "86400");  -- 24 hours
```

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
