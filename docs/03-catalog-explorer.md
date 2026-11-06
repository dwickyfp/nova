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

## Visibility

The tree and every detail page show only what the signed-in user's active role
can list in the engine. The backend opens the caller's StarRocks session,
activates that role, and reads `SHOW CATALOGS`, `SHOW DATABASES`,
`SHOW FULL TABLES`, and `SHOW FULL FUNCTIONS`; Ranger filters each of them.
Descriptive detail that a plain role may not read from `information_schema`
(table model, partitions, materialized-view refresh state, pipe status) comes
from the system pool, and only for objects that session listed. A database or
object the caller cannot list answers `404`, so its stages, tasks, and pipes do
not appear either.

The caller's listings are reused for `CATALOG_VISIBILITY_CACHE_SECONDS`
(default 60) in Redis, partitioned by principal, active role, and
security-context version. A role switch never reads another role's answer, and
an access change made through Nova, or a catalog create/alter/drop/refresh,
retires every cached answer at once. A grant or Ranger policy changed outside
Nova shows up once the short TTL passes.

## Nova Catalog store

Descriptions come from the Nova Catalog store (`NOVA_SYSTEM.CATALOG_CATALOGS`,
`CATALOG_OBJECTS`, `CATALOG_COLUMNS`, `CATALOG_SYNC_RUNS`), never visibility:
an object the caller can list but the store has not seen yet still appears,
by name only. A control-plane loop syncs every engine catalog every
`CATALOG_SYNC_INTERVAL_SECONDS` (default 300), records catalogs created in raw
SQL with `source = engine`, and marks vanished objects with `dropped_at`
instead of deleting them. External tables therefore show their columns, read
from the store (`GET /explorer/databases/{db}/tables/{table}?catalog=<name>`).

The catalog page shows when metadata was last synced. A role in
`ACCOUNTADMIN`, `SECURITYADMIN`, or `security_admin` also gets **Refresh
metadata**, which syncs that catalog at once; every refresh and every refusal
is written to `AUDIT_LOG` (`action = REFRESH_METADATA`).

The API lives at `/api/v1/catalog`:

| Endpoint | Purpose |
|---|---|
| `GET /catalogs` | Catalogs the caller can list, with sync status |
| `GET /catalogs/{name}` | Detail with the redacted `SHOW CREATE CATALOG` |
| `POST`, `PATCH`, `DELETE /catalogs[/{name}]` | Create, alter, drop, run as the caller |
| `POST /catalogs/refresh`, `POST /catalogs/{name}/refresh` | Sync now (security admin) |
| `GET /catalogs/{name}/sync-runs` | Recent syncs, errors redacted |
| `GET /catalogs/{name}/databases/{db}/objects[/{object}/columns]` | Listed objects and stored columns |

## Business metadata, search, and lineage

Every catalog, database, table, view, materialized view, and column the sync
sees gets a **securable id** in `NOVA_SYSTEM.CATALOG_SECURABLES`. Tags
(`CONFIG_OBJECT_TAGS`), the business description, owner, and column
classification (`CATALOG_ANNOTATIONS`), lineage edges (`CATALOG_LINEAGE`), and
links from Semantic Views and entities (`CATALOG_SECURABLE_LINKS`) attach to
that id, not to a name. When an object disappears from its source, the next
sync marks it `dropped_at`; it keeps its id, tags, and lineage and shows as
dropped. If it comes back under the same name, it is live again with the same
id.

**Who sees what.** Every read is cut by what the caller's own engine session
lists under the active role, exactly like the tree: a table or column whose
relation the role cannot list never appears in a page, a search result, a
lineage edge, or the assistant's `catalog_search` tool. A dropped object no
longer exists in the engine, so only an active security-admin role
(`ACCOUNTADMIN`, `SECURITYADMIN`, `security_admin`) sees it. Nova keeps no
permission table of its own.

**Who may change what.** Tags, the description, and a column's classification
can be changed by a security-admin role, or, for a live table, view, or
materialized view and its columns, by a role that Ranger grants `ALTER` or
`ALL` on that relation. The owner is set by security admins only. Every change
and every refusal is written to `AUDIT_LOG` (`event_type = CATALOG`, actions
`SET_TAG`, `UNSET_TAG`, `ANNOTATE`) with the object's name and the field
changed. The audit row never holds a tag value or the description text. The
classification is advisory: Ranger masks and row filters still decide what a
query returns.

**Search** (`GET /api/v1/catalog/search`) matches names, columns, tag keys
and values, descriptions, source comments, owners, and classifications. In the
Explorer header, type a word, `tag:pii`, or `tag:pii=email`; choosing a result
opens it in the tree.

**Lineage** is read from `AUDIT_LOG`: each successful
`INSERT ... SELECT`, `CREATE TABLE ... AS SELECT`, `CREATE VIEW`, or
`CREATE MATERIALIZED VIEW` run through Nova adds a table-to-table edge. The
control-plane loop reads new audit rows after each sync pass, and
**Refresh** on all catalogs does it at once. Statements run directly against
the engine, outside Nova, leave no audit row and no edge. One-part names use
the statement's session database in `default_catalog`.

**Semantic Views and entities** keep their stored dataset and relation names;
`CATALOG_SECURABLE_LINKS` records which securable each one uses (the active
version of a Semantic View, or its newest while none is active). A table page
lists the Semantic Views and entities that use it, filtered by their own
listing rules.

| Endpoint | Purpose |
|---|---|
| `GET /search?q=&tag=&type=&include_dropped=&limit=` | Search; `include_dropped` applies to security admins |
| `GET /objects/resolve?catalog=&database=&object=&column=` | The securable a name points at |
| `GET /objects/{id}` | Detail with columns, tags, description, owner, dependents, and whether the role may edit |
| `GET`, `PUT /objects/{id}/tags`, `DELETE /objects/{id}/tags/{key}` | Read, set, and remove tags |
| `PATCH /objects/{id}/annotations` | Description, owner, classification (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`) |
| `GET /objects/{id}/lineage` | Upstream and downstream relations the caller can see |

In Studio and the assistant, the read-only `catalog_search` tool searches the
same metadata or describes one relation (columns, tags, classifications,
lineage, dependents) as the caller. Its output marks tags and descriptions as
text written by people, not instructions and not evidence of data values.

When the backend starts, a `CONFIG_OBJECT_TAGS` table in the old name-keyed
shape is replaced by the securable-id shape. An empty old table is dropped; one
with rows is kept as `CONFIG_OBJECT_TAGS_LEGACY`, and the rows that name a
database, table, view, or column are copied to their securable ids.

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
