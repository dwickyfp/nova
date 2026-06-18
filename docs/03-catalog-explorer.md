# Module 03: Catalog Explorer

> Hierarchical browser for catalogs, databases, schemas, tables, columns, and all database objects.

---

## Navigation Tree

```
Catalogs
├── default_catalog (internal)
│   ├── DATALAKE
│   │   ├── bronze
│   │   │   ├── Tables/
│   │   │   ├── Views/
│   │   │   ├── Materialized Views/
│   │   │   ├── Functions/
│   │   │   └── Stages/ ← Nova custom
│   │   ├── silver/
│   │   └── gold/
│   └── ANALYTICS/
│       └── ...
├── iceberg_lake (external)
│   ├── analytics
│   │   ├── events
│   │   └── sessions
│   └── raw/
├── hive_warehouse (external)
│   └── ...
└── paimon_catalog (external)
    └── ...
```

## Supported Objects per Level

### Catalog Level

| Action | SQL |
|--------|-----|
| List catalogs | `SHOW CATALOGS` |
| Switch catalog | `USE <catalog>` |
| Show create | `SHOW CREATE CATALOG <name>` |
| Create external | `CREATE EXTERNAL CATALOG ...` |
| Drop catalog | `DROP CATALOG <name>` |
| Alter catalog | `ALTER CATALOG <name> SET ...` |

### Database Level

| Action | SQL |
|--------|-----|
| List databases | `SHOW DATABASES` |
| Create database | `CREATE DATABASE <name>` |
| Drop database | `DROP DATABASE <name>` |
| Use database | `USE <database>` |
| Show data size | `SHOW DATA FROM <database>` |

### Table Level

| Action | SQL |
|--------|-----|
| List tables | `SHOW TABLES` |
| Show table status | `SHOW TABLE STATUS` |
| Show create table | `SHOW CREATE TABLE <name>` |
| Describe table | `DESC[RIBE] <name>` |
| Show partitions | `SHOW PARTITIONS FROM <name>` |
| Show tablets | `SHOW TABLET FROM <name>` |
| Show indexes | `SHOW INDEX[ES] FROM <name>` |
| Show alter status | `SHOW ALTER TABLE ...` |
| Show data size | `SHOW DATA` |

### Column Level

| Action | SQL |
|--------|-----|
| List columns | `SELECT * FROM information_schema.columns WHERE TABLE_NAME = ...` |
| Column details | Name, type, nullable, default, comment |
| Column statistics | `ANALYZE TABLE <name>` then inspect stats |

### View Level

| Action | SQL |
|--------|-----|
| List views | `SHOW FULL VIEWS` |
| Show create view | `SHOW CREATE VIEW <name>` |
| Create view | `CREATE VIEW ...` |
| Drop view | `DROP VIEW <name>` |

### Materialized View Level

| Action | SQL |
|--------|-----|
| List MVs | `SHOW MATERIALIZED VIEWS` |
| Show create MV | `SHOW CREATE MATERIALIZED VIEW <name>` |
| MV status | `SELECT * FROM information_schema.materialized_views` |
| Refresh MV | `REFRESH MATERIALIZED VIEW <name>` |
| Alter MV | `ALTER MATERIALIZED VIEW <name> ...` |
| Drop MV | `DROP MATERIALIZED VIEW <name>` |

---

## Table Detail Page

When user clicks a table, show:

| Tab | Content |
|-----|---------|
| **Overview** | Name, type, engine, row count, size, created, updated, comment |
| **Columns** | Column name, type, nullable, default, comment |
| **Partitions** | Partition name, range/list, row count, size |
| **Distribution** | Hash keys, bucket count |
| **Indexes** | Bitmap/bloom filter/inverted indexes |
| **Properties** | Table properties (replication_num, storage_medium, etc.) |
| **Preview** | First 100 rows (SELECT * LIMIT 100) |
| **DDL** | SHOW CREATE TABLE output |

---

## External Catalog Support

| Catalog Type | Supported Operations |
|-------------|---------------------|
| **Hive** | Query, INSERT INTO, CREATE TABLE, DROP TABLE, REFRESH |
| **Iceberg** | Query, INSERT INTO, CREATE TABLE, DROP TABLE, DELETE, time-travel |
| **Hudi** | Query, INSERT INTO, REFRESH |
| **Delta Lake** | Query, INSERT INTO |
| **Paimon** | Query, branch/tag/version/timestamp time-travel |
| **JDBC** | Query (MySQL, PostgreSQL, Oracle, SQL Server, etc.) |
| **Elasticsearch** | Query |
| **Unified** | Query across Hive/Iceberg/Hudi/Delta Lake/Paimon/Kudu |

---

## Nova Addition: Stages

In addition to standard StarRocks objects, each database.schema shows:

```
├── Tables/
├── Views/
├── Materialized Views/
├── Functions/
└── Stages/          ← Nova custom object
    ├── stage1/
    │   ├── file1.csv
    │   └── data/
    │       └── file2.parquet
    └── stage2/
```

Stage is a Nova-managed object (not in StarRocks catalog). Stage files are browsed via storage provider layer.
