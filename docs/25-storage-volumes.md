# Module 25: Storage Volumes

> Manage shared-data mode storage volumes — abstract storage backends for StarRocks cloud-native tables.

---

## Concept

Storage Volumes are StarRocks-native objects that define storage backends for **shared-data mode** (cloud-native tables). Unlike Nova Stages (UI-level abstraction), Storage Volumes are actual StarRocks catalog objects.

```
Storage Volume (StarRocks native)  →  cloud-native table storage
Nova Stage (Nova custom)           →  user file staging area
```

---

## Operations

### Create Storage Volume

```sql
CREATE STORAGE VOLUME my_volume
    TYPE = S3
    LOCATIONS = ("s3://my-bucket/starrocks-data/")
    PROPERTIES(
        "aws.s3.endpoint" = "http://minio:9000",
        "aws.s3.access_key" = "***",
        "aws.s3.secret_key" = "***",
        "aws.s3.enable_path_style_access" = "true",
        "aws.s3.enable_ssl" = "false"
    );
```

### Manage Storage Volumes

```sql
-- List volumes
SHOW STORAGE VOLUMES;

-- Describe volume
DESC STORAGE VOLUME my_volume;

-- Enable volume
ALTER STORAGE VOLUME my_volume SET ("enabled" = "true");

-- Set as default
SET my_volume AS DEFAULT STORAGE VOLUME;

-- Drop volume
DROP STORAGE VOLUME my_volume;
```

### Use Volume for Tables

```sql
-- Create table on specific volume
CREATE TABLE orders (...)
DISTRIBUTED BY HASH(id)
PROPERTIES("storage_volume" = "my_volume");

-- Create database with default volume
CREATE DATABASE analytics PROPERTIES("storage_volume" = "my_volume");
```

---

## Nova UI

```
┌─ Storage Volumes ───────────────────────────────────────┐
│                                                          │
│  [+ Create Volume]                                       │
│                                                          │
│  Name          Type   Location              Status       │
│  default_vol   S3     s3://sr-data/default  🟢 Default  │
│  fast_ssd      S3     s3://sr-data/ssd      🟢 Enabled  │
│  archive       S3     s3://sr-data/archive  🟡 Enabled  │
│  old_vol       S3     s3://sr-data/old      🔴 Disabled │
│                                                          │
│  ── Default Volume: default_vol ──                       │
│  All new databases use default_vol unless overridden.    │
│                                                          │
│  ── Volume: fast_ssd ──                                  │
│  Type: S3                                                │
│  Location: s3://sr-data/ssd/                             │
│  Tables: orders (1.2 GB), payments (800 MB)             │
│  [Edit] [Disable] [Delete]                               │
└──────────────────────────────────────────────────────────┘
```
