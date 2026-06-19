# StarRocks Privileges — Complete Reference Guide

> **Version**: StarRocks 4.1.1 | **Last Updated**: 2025-06-20  
> **Total Privileges**: 46 distinct privilege×object combinations across 13 object types

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Built-in Roles](#built-in-roles)
3. [System-Level Privileges (14)](#system-level-privileges)
4. [Object-Level Privileges (32)](#object-level-privileges)
5. [WITH GRANT OPTION](#with-grant-option)
6. [Role Nesting](#role-nesting)
7. [SQL Syntax Reference](#sql-syntax-reference)
8. [Privilege Matrix — Full Grid](#privilege-matrix--full-grid)
9. [Non-Existent Privileges](#non-existent-privileges)
10. [Data Sources for Monitoring](#data-sources-for-monitoring)

---

## Architecture Overview

```
StarRocks Cluster
└── 📦 CATALOG                    ← Top-level namespace (data source)
    │   ├── default_catalog       (internal — all your databases live here)
    │   ├── hive_catalog          (external — Hive Metastore)
    │   └── iceberg_catalog       (external — Apache Iceberg)
    │
    └── 🗄️ DATABASE               ← Logical grouping within a catalog
        ├── 📋 TABLE
        ├── 👁️ VIEW
        ├── 📊 MATERIALIZED VIEW
        ├── 🔄 PIPE
        └── ⚙️ FUNCTION

Instance-level objects (not inside catalogs):
├── 🔧 SYSTEM                     ← Instance-wide operations
├── 👤 USER                       ← User impersonation
├── 📦 RESOURCE                   ← External resources (broker, spark, etc.)
├── 🏗️ RESOURCE GROUP             ← Workload isolation
├── 💾 STORAGE VOLUME             ← Shared-data storage
├── 🏭 WAREHOUSE                  ← Compute warehouses (shared_data mode)
├── 🌐 GLOBAL FUNCTION            ← UDFs registered at instance level
└── 📚 CATALOG                    ← External catalog management
```

### Privilege Scope Levels

| Scope | Scope Target | Example |
|-------|-------------|---------|
| **Global** (ALL) | All objects of a type | `GRANT SELECT ON ALL TABLES IN ALL DATABASES` |
| **Catalog** | Specific catalog | `GRANT USAGE ON CATALOG hive_catalog` |
| **Database** | Specific database | `GRANT CREATE TABLE ON DATABASE my_db` |
| **Object** | Specific table/view/etc | `GRANT SELECT ON TABLE my_db.my_table` |
| **System** | Instance-wide | `GRANT OPERATE ON SYSTEM` |

---

## Built-in Roles

StarRocks has **6 built-in roles** that cannot be dropped or renamed.

| Role | Builtin | Description | Key Privileges |
|------|:-------:|-------------|----------------|
| `root` | ✅ | **Super admin** — all privileges on all objects | Everything (GRANT, NODE, all object privs) |
| `db_admin` | ✅ | **Database administrator** — full DDL/DML on all databases | All TABLE/DATABASE/VIEW/MV/FUNCTION privs + most SYSTEM privs |
| `cluster_admin` | ✅ | **Cluster administrator** — infrastructure management | NODE, CREATE WAREHOUSE, USAGE/ALTER/DROP on WAREHOUSES |
| `user_admin` | ✅ | **User administrator** — manage users & roles | GRANT on SYSTEM, IMPERSONATE on ALL USERS |
| `security_admin` | ✅ | **Security administrator** — security operations | OPERATE, SECURITY on SYSTEM |
| `public` | ✅ | **Default role** — automatically assigned to every user | SELECT on information_schema tables |

### Custom Role: `ACCOUNTADMIN` (Nova-specific)

| Role | Builtin | Description |
|------|:-------:|-------------|
| `ACCOUNTADMIN` | ❌ | Custom super-admin role created during Nova setup. Has most privileges WITH GRANT OPTION. Protected from DROP/REVOKE. |

### What Each Built-in Role Has (Summary)

| Privilege Area | root | db_admin | cluster_admin | user_admin | security_admin | public |
|---------------|:----:|:--------:|:-------------:|:----------:|:--------------:|:------:|
| All TABLE privs | ✅ | ✅ | ❌ | ❌ | ❌ | ❌* |
| All DATABASE privs | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| All VIEW privs | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| All MV privs | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| All FUNCTION privs | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| CATALOG mgmt | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| RESOURCE mgmt | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| RESOURCE GROUP mgmt | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| STORAGE VOLUME mgmt | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| GLOBAL FUNCTION mgmt | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| WAREHOUSE mgmt | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| GRANT (manage roles) | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| NODE (cluster mgmt) | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| OPERATE (monitoring) | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| SECURITY | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| IMPERSONATE | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| CREATE EXTERNAL CATALOG | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| CREATE RESOURCE | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| CREATE WAREHOUSE | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| FILE | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| BLACKLIST | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| PLUGIN | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| REPOSITORY | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |

\\* `public` only has SELECT on `information_schema` tables.

---

## System-Level Privileges

System privileges are instance-wide. They are granted via:
```sql
GRANT <privilege> ON SYSTEM TO ROLE <role_name>;
```

### Total: 14 System Privileges (12 grantable + 2 built-in only)

| # | Privilege | Custom Role? | Description | What It Allows |
|---|-----------|:---:|-------------|----------------|
| 1 | **GRANT** | ❌ | Manage privileges | `GRANT`/`REVOKE` privileges to/from users and roles. `SHOW GRANTS` for any user. `SHOW ROLES`. `SHOW USERS`. |
| 2 | **NODE** | ❌ | Cluster node management | `ALTER SYSTEM` (add/drop FE/BE/CN nodes). Cluster topology changes. |
| 3 | **OPERATE** | ✅ | Operational monitoring | `SHOW PROCESSLIST`, `KILL` queries, `SHOW BACKENDS/FRONTENDS`, `SHOW PROFILELIST`, `SHOW PROC`, `SHOW TABLET`, `ADMIN SET/SHOW FRONTEND CONFIG`, `ADMIN CHECK TABLET`, `SET GLOBAL` variables, `SHOW PROPERTY` |
| 4 | **SECURITY** | ✅ | Security management | Security policy management. Included in `security_admin` and `db_admin`. |
| 5 | **CREATE RESOURCE** | ✅ | Create external resources | `CREATE RESOURCE` (broker, spark, JDBC, etc.) |
| 6 | **FILE** | ✅ | File access | File import/export operations |
| 7 | **BLACKLIST** | ✅ | SQL blacklist management | Manage SQL blacklist rules to block specific query patterns |
| 8 | **CREATE EXTERNAL CATALOG** | ✅ | Create external catalogs | `CREATE EXTERNAL CATALOG` (Hive, Iceberg, Hudi, JDBC, etc.) |
| 9 | **REPOSITORY** | ✅ | Backup/restore management | `CREATE/SHOW/DROP REPOSITORY`, `BACKUP/RESTORE SNAPSHOT` |
| 10 | **CREATE RESOURCE GROUP** | ✅ | Resource group management | `CREATE RESOURCE GROUP` for workload isolation |
| 11 | **CREATE GLOBAL FUNCTION** | ✅ | Global UDF creation | `CREATE GLOBAL FUNCTION` (instance-level UDFs) |
| 12 | **CREATE STORAGE VOLUME** | ✅ | Storage volume management | `CREATE STORAGE VOLUME` (shared-data mode) |
| 13 | **CREATE WAREHOUSE** | ✅ | Warehouse creation | `CREATE WAREHOUSE` (shared_data mode compute resources) |
| 14 | **PLUGIN** | ✅ | Plugin management | Install/uninstall/manage plugins |

### ⚠️ Key Constraint

> **`GRANT` and `NODE` CANNOT be granted to custom roles.**  
> Error: *"Operation not permitted, 'GRANT' cannot be granted to user or role directly, use built-in role instead."*  
> To give a custom role these capabilities, assign the built-in `user_admin` or `cluster_admin` role to it.

---

## Object-Level Privileges

### TABLE — 8 Privileges

Granted on: `TABLE db.table_name` or `ALL TABLES IN ALL DATABASES` or `ALL TABLES IN DATABASE db`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **SELECT** | Read data | `SELECT`, `SHOW CREATE TABLE`, `DESC`, `EXPLAIN` |
| **INSERT** | Write data | `INSERT INTO`, `INSERT OVERWRITE`, `LOAD DATA`, Stream Load |
| **UPDATE** | Modify data | `UPDATE` statement (Primary Key tables) |
| **DELETE** | Remove data | `DELETE FROM` statement |
| **ALTER** | Modify schema | `ALTER TABLE` (add/drop columns, partitions, rollups, etc.) |
| **DROP** | Drop table | `DROP TABLE`, `TRUNCATE TABLE` |
| **EXPORT** | Export data | `EXPORT` statement for data export |
| **REFRESH** | Refresh metadata | Refresh table metadata/statistics |

### DATABASE — 7 Privileges

Granted on: `DATABASE db_name` or `ALL DATABASES`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **CREATE TABLE** | Create tables | `CREATE TABLE` in the database |
| **CREATE VIEW** | Create views | `CREATE VIEW` in the database |
| **CREATE FUNCTION** | Create UDFs | `CREATE FUNCTION` in the database |
| **CREATE MATERIALIZED VIEW** | Create MVs | `CREATE MATERIALIZED VIEW` in the database |
| **CREATE PIPE** | Create pipes | `CREATE PIPE` for continuous data ingestion |
| **DROP** | Drop database | `DROP DATABASE` |
| **ALTER** | Alter database | `ALTER DATABASE` (rename, set properties) |

### VIEW — 3 Privileges

Granted on: `VIEW db.view_name` or `ALL VIEWS IN ALL DATABASES`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **SELECT** | Query view | `SELECT FROM view` |
| **ALTER** | Modify view | `ALTER VIEW` (change definition) |
| **DROP** | Drop view | `DROP VIEW` |

### MATERIALIZED VIEW — 4 Privileges

Granted on: `MATERIALIZED VIEW db.mv_name` or `ALL MATERIALIZED VIEWS IN ALL DATABASES`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **SELECT** | Query MV | `SELECT FROM materialized_view` |
| **ALTER** | Modify MV | `ALTER MATERIALIZED VIEW` (change definition, properties) |
| **DROP** | Drop MV | `DROP MATERIALIZED VIEW` |
| **REFRESH** | Refresh MV | `REFRESH MATERIALIZED VIEW` (trigger data refresh) |

### FUNCTION — 2 Privileges

Granted on: `FUNCTION db.func_name()` or `ALL FUNCTIONS IN ALL DATABASES`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **USAGE** | Use function | Call the function in queries |
| **DROP** | Drop function | `DROP FUNCTION` |

### GLOBAL FUNCTION — 2 Privileges

Granted on: `GLOBAL FUNCTION func_name()` or `ALL GLOBAL FUNCTIONS`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **USAGE** | Use global function | Call the global function in queries |
| **DROP** | Drop global function | `DROP GLOBAL FUNCTION` |

### CATALOG — 4 Privileges

Granted on: `CATALOG catalog_name` or `ALL CATALOGS`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **USAGE** | Use catalog | Access databases/tables in the catalog. `USE CATALOG`. |
| **CREATE DATABASE** | Create databases | `CREATE DATABASE` within the catalog |
| **DROP** | Drop catalog | `DROP CATALOG` |
| **ALTER** | Alter catalog | `ALTER CATALOG` (change properties) |

### RESOURCE — 3 Privileges

Granted on: `RESOURCE resource_name` or `ALL RESOURCES`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **USAGE** | Use resource | Reference the resource in queries/loads |
| **ALTER** | Modify resource | `ALTER RESOURCE` |
| **DROP** | Drop resource | `DROP RESOURCE` |

### RESOURCE GROUP — 2 Privileges

Granted on: `RESOURCE GROUP rg_name` or `ALL RESOURCE GROUPS`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **ALTER** | Modify resource group | `ALTER RESOURCE GROUP` (change CPU/memory limits) |
| **DROP** | Drop resource group | `DROP RESOURCE GROUP` |

### STORAGE VOLUME — 3 Privileges

Granted on: `STORAGE VOLUME sv_name` or `ALL STORAGE VOLUMES`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **USAGE** | Use storage volume | Use the storage volume for data storage |
| **ALTER** | Modify storage volume | `ALTER STORAGE VOLUME` |
| **DROP** | Drop storage volume | `DROP STORAGE VOLUME` |

### WAREHOUSE — 3 Privileges

Granted on: `WAREHOUSE wh_name` or `ALL WAREHOUSES`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **USAGE** | Use warehouse | Execute queries using the warehouse's compute resources |
| **ALTER** | Modify warehouse | `ALTER WAREHOUSE` (scale, rename, etc.) |
| **DROP** | Drop warehouse | `DROP WAREHOUSE` |

### USER — 1 Privilege

Granted on: `USER 'username'@'host'` or `ALL USERS`

| Privilege | Description | What It Allows |
|-----------|-------------|----------------|
| **IMPERSONATE** | Impersonate user | `EXECUTE AS 'user'@'host' WITH NO REVERT` — execute commands as another user with their privileges. **⚠️ Extremely powerful** — effectively grants full access to the target user's privileges. |

---

## WITH GRANT OPTION

The `WITH GRANT OPTION` clause allows the grantee to **re-grant** the same privilege to other users or roles.

### Syntax

```sql
GRANT <privilege> ON <object_type> <object> TO ROLE <role> WITH GRANT OPTION;
```

### Behavior

| Without | With |
|---------|------|
| User can **use** the privilege | User can **use** the privilege |
| User **cannot** grant it to others | User **can** grant it to others |
| `IS_GRANTABLE = NO` in `sys.grants_to_roles` | `IS_GRANTABLE = YES` |

### Example

```sql
-- Grant SELECT with ability to re-grant
GRANT SELECT ON ALL TABLES IN ALL DATABASES TO ROLE analyst_lead WITH GRANT OPTION;

-- Now a user with analyst_lead role can:
GRANT SELECT ON TABLE analytics.orders TO ROLE junior_analyst;  -- ✅ succeeds

-- A user WITHOUT grant option cannot:
GRANT SELECT ON TABLE analytics.orders TO ROLE intern;  -- ❌ "Access denied"
```

### Important Notes

- `WITH GRANT OPTION` only applies to **object-level** privileges (TABLE, DATABASE, VIEW, etc.)
- It does NOT work for **SYSTEM-level** privileges (you can't grant OPERATE WITH GRANT OPTION)
- The built-in roles already have implicit grant option for their privileges
- To revoke grant option, you must revoke and re-grant without it (no `REVOKE GRANT OPTION FOR` syntax)

---

## Role Nesting

Roles can be granted to other roles, creating a hierarchy.

### Syntax

```sql
GRANT <child_role> TO ROLE <parent_role>;
```

### Behavior

```sql
CREATE ROLE analyst;
CREATE ROLE senior_analyst;

GRANT SELECT ON ALL TABLES IN ALL DATABASES TO ROLE analyst;
GRANT analyst TO ROLE senior_analyst;

-- senior_analyst inherits analyst's SELECT privilege
-- Users granted senior_analyst get both senior_analyst's own privileges AND analyst's privileges
```

### Important Notes

- `SHOW GRANTS FOR ROLE parent` does **NOT** display inherited privileges from child roles
- At runtime, all privileges from nested roles are active
- `sys.role_edges` tracks role-to-role grants (`TO_ROLE` column) and role-to-user grants (`TO_USER` column)
- Built-in roles can be nested: `GRANT db_admin TO ROLE my_custom_admin;`
- Circular role grants are prevented by StarRocks

---

## SQL Syntax Reference

### Role Management

| Operation | SQL |
|-----------|-----|
| List roles | `SHOW ROLES;` |
| Create role | `CREATE ROLE [IF NOT EXISTS] role_name;` |
| Drop role | `DROP ROLE [IF EXISTS] role_name;` |
| Show role grants | `SHOW GRANTS FOR ROLE role_name;` |

### User Management

| Operation | SQL |
|-----------|-----|
| List users | `SHOW USERS;` |
| Create user | `CREATE USER 'name'@'host' IDENTIFIED BY 'password';` |
| Change password | `ALTER USER 'name'@'host' IDENTIFIED BY 'new_password';` |
| Drop user | `DROP USER [IF EXISTS] 'name'@'host';` |
| Show user grants | `SHOW GRANTS FOR 'name'@'host';` |
| Show user properties | `SHOW PROPERTY FOR 'name';` |
| Set property | `SET PROPERTY FOR 'name' 'key'='value';` |

### Role ↔ User Assignment

| Operation | SQL |
|-----------|-----|
| Grant role to user | `GRANT role_name TO 'user'@'host';` |
| Revoke role from user | `REVOKE role_name FROM 'user'@'host';` |
| Set default role | `SET DEFAULT ROLE role_name TO 'user'@'host';` |

### Privilege Granting

| Scope | Syntax |
|-------|--------|
| System | `GRANT <priv> ON SYSTEM TO ROLE <role>;` |
| All catalogs | `GRANT <priv> ON ALL CATALOGS TO ROLE <role>;` |
| Specific catalog | `GRANT <priv> ON CATALOG <name> TO ROLE <role>;` |
| All databases | `GRANT <priv> ON ALL DATABASES TO ROLE <role>;` |
| Specific database | `GRANT <priv> ON DATABASE <name> TO ROLE <role>;` |
| All tables in all DBs | `GRANT <priv> ON ALL TABLES IN ALL DATABASES TO ROLE <role>;` |
| All tables in one DB | `GRANT <priv> ON ALL TABLES IN DATABASE <db> TO ROLE <role>;` |
| Specific table | `GRANT <priv> ON TABLE <db>.<table> TO ROLE <role>;` |
| All views | `GRANT <priv> ON ALL VIEWS IN ALL DATABASES TO ROLE <role>;` |
| Specific view | `GRANT <priv> ON VIEW <db>.<view> TO ROLE <role>;` |
| All MVs | `GRANT <priv> ON ALL MATERIALIZED VIEWS IN ALL DATABASES TO ROLE <role>;` |
| Specific MV | `GRANT <priv> ON MATERIALIZED VIEW <db>.<mv> TO ROLE <role>;` |
| All functions | `GRANT <priv> ON ALL FUNCTIONS IN ALL DATABASES TO ROLE <role>;` |
| Specific function | `GRANT <priv> ON FUNCTION <db>.<func>() TO ROLE <role>;` |
| All global functions | `GRANT <priv> ON ALL GLOBAL FUNCTIONS TO ROLE <role>;` |
| All resources | `GRANT <priv> ON ALL RESOURCES TO ROLE <role>;` |
| Specific resource | `GRANT <priv> ON RESOURCE <name> TO ROLE <role>;` |
| All resource groups | `GRANT <priv> ON ALL RESOURCE GROUPS TO ROLE <role>;` |
| All storage volumes | `GRANT <priv> ON ALL STORAGE VOLUMES TO ROLE <role>;` |
| All warehouses | `GRANT <priv> ON ALL WAREHOUSES TO ROLE <role>;` |
| All users | `GRANT IMPERSONATE ON ALL USERS TO ROLE <role>;` |
| Specific user | `GRANT IMPERSONATE ON USER 'name'@'host' TO ROLE <role>;` |

### Privilege Revoking

Same syntax as granting, but with `REVOKE ... FROM ROLE` instead of `GRANT ... TO ROLE`:

```sql
REVOKE <priv> ON <object_type> <object> FROM ROLE <role>;
```

### Granting Directly to Users (not via roles)

```sql
GRANT <priv> ON <object_type> <object> TO 'user'@'host';
REVOKE <priv> ON <object_type> <object> FROM 'user'@'host';
```

---

## Privilege Matrix — Full Grid

| Object Type | SELECT | INSERT | UPDATE | DELETE | ALTER | DROP | CREATE | USAGE | EXPORT | REFRESH | IMPERSONATE | Other |
|-------------|:------:|:------:|:------:|:------:|:-----:|:----:|:------:|:-----:|:------:|:-------:|:-----------:|:-----:|
| **SYSTEM** | | | | | | | | | | | | OPERATE, SECURITY, GRANT*, NODE*, CREATE RESOURCE, FILE, BLACKLIST, CREATE EXTERNAL CATALOG, REPOSITORY, CREATE RESOURCE GROUP, CREATE GLOBAL FUNCTION, CREATE STORAGE VOLUME, CREATE WAREHOUSE, PLUGIN |
| **TABLE** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | | | ✅ | ✅ | | |
| **DATABASE** | | | | | ✅ | ✅ | TABLE, VIEW, FUNCTION, MATERIALIZED VIEW, PIPE | | | | | |
| **VIEW** | ✅ | | | | ✅ | ✅ | | | | | | |
| **MATERIALIZED VIEW** | ✅ | | | | ✅ | ✅ | | | | ✅ | | |
| **FUNCTION** | | | | | | ✅ | | ✅ | | | | |
| **GLOBAL FUNCTION** | | | | | | ✅ | | ✅ | | | | |
| **CATALOG** | | | | | ✅ | ✅ | DATABASE | ✅ | | | | |
| **RESOURCE** | | | | | ✅ | ✅ | | ✅ | | | | |
| **RESOURCE GROUP** | | | | | ✅ | ✅ | | | | | | |
| **STORAGE VOLUME** | | | | | ✅ | ✅ | | ✅ | | | | |
| **WAREHOUSE** | | | | | ✅ | ✅ | | ✅ | | | | |
| **USER** | | | | | | | | | | | ✅ | |

\\* = built-in only, cannot be granted to custom roles

---

## Non-Existent Privileges

These privileges from MySQL/PostgreSQL/Snowflake **do NOT exist** in StarRocks:

| Attempted Privilege | Status | Notes |
|---------------------|--------|-------|
| MONITOR | ❌ Not supported | Use OPERATE instead |
| EXECUTE | ❌ Not supported | Functions use USAGE privilege |
| TRIGGER | ❌ Not supported | StarRocks has no triggers |
| INDEX | ❌ Not supported | Indexes managed via ALTER TABLE |
| REFERENCES | ❌ Not supported | No foreign key constraints |
| PROCESS | ❌ Not supported | Use OPERATE instead |
| RELOAD | ❌ Not supported | |
| REPLICATION CLIENT | ❌ Not supported | |
| REPLICATION SLAVE | ❌ Not supported | |
| SHOW DATABASES | ❌ Not supported | Covered by CATALOG USAGE |
| SHOW VIEW | ❌ Not supported | Covered by VIEW SELECT |
| SHUTDOWN | ❌ Not supported | |
| SUPER | ❌ Not supported | Use root role instead |
| EVENT | ❌ Not supported | |
| CREATE TEMPORARY TABLES | ❌ Not supported | |
| LOCK TABLES | ❌ Not supported | StarRocks has no table locks |
| CREATE ROUTINE | ❌ Not supported | Use CREATE FUNCTION |
| ALTER ROUTINE | ❌ Not supported | |
| USAGE ON DATABASE | ❌ Not applicable | Database access via CREATE TABLE priv |
| INSERT ON VIEW | ❌ Not applicable | Views are read-only |
| SELECT ON FUNCTION | ❌ Not applicable | Functions use USAGE |
| USAGE ON RESOURCE GROUP | ❌ Not applicable | Only ALTER/DROP supported |
| CREATE CATALOG ON SYSTEM | ❌ Not applicable | Use CREATE EXTERNAL CATALOG ON SYSTEM |
| WORKLOAD GROUP | ❌ Not supported | Syntax doesn't exist |
| WORKLOAD POLICY | ❌ Not supported | Syntax doesn't exist |

---

## Data Sources for Monitoring

### System Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `sys.grants_to_roles` | All privileges granted to roles | GRANTEE, OBJECT_TYPE, OBJECT_DATABASE, OBJECT_NAME, PRIVILEGE_TYPE, IS_GRANTABLE |
| `sys.grants_to_users` | All direct privileges granted to users | Same schema as grants_to_roles |
| `sys.role_edges` | Role-to-user and role-to-role assignments | FROM_ROLE, TO_ROLE, TO_USER |

### SHOW Commands

| Command | Output |
|---------|--------|
| `SHOW ROLES` | Name, Builtin, Comment |
| `SHOW USERS` | User (format: `'name'@'host'`) |
| `SHOW GRANTS` | Current user's grants |
| `SHOW GRANTS FOR 'user'@'host'` | Specific user's grants |
| `SHOW GRANTS FOR ROLE name` | Specific role's grants |
| `SHOW PROPERTY FOR 'user'` | User properties (max_user_connections, catalog, database) |
| `SHOW CATALOGS` | All catalogs |
| `SHOW DATABASES` | All databases in current catalog |

### User Properties

| Property | Default | Description |
|----------|---------|-------------|
| `max_user_connections` | 1024 | Maximum simultaneous connections |
| `catalog` | default_catalog | Default catalog on login |
| `database` | (empty) | Default database on login |

---

## Quick Reference for Nova Administrator UI

### When Creating a Role — Available Privilege Categories

```
🔧 System Privileges (14):
   GRANT* | NODE* | OPERATE | SECURITY | CREATE RESOURCE | FILE | BLACKLIST |
   CREATE EXTERNAL CATALOG | REPOSITORY | CREATE RESOURCE GROUP |
   CREATE GLOBAL FUNCTION | CREATE STORAGE VOLUME | CREATE WAREHOUSE | PLUGIN

📋 Object Privileges (by type):
   TABLE (8):      SELECT, INSERT, UPDATE, DELETE, ALTER, DROP, EXPORT, REFRESH
   DATABASE (7):   CREATE TABLE, CREATE VIEW, CREATE FUNCTION, CREATE MATERIALIZED VIEW, CREATE PIPE, DROP, ALTER
   VIEW (3):       SELECT, ALTER, DROP
   MAT. VIEW (4):  SELECT, ALTER, DROP, REFRESH
   FUNCTION (2):   USAGE, DROP
   GLOBAL FN (2):  USAGE, DROP
   CATALOG (4):    USAGE, CREATE DATABASE, DROP, ALTER
   RESOURCE (3):   USAGE, ALTER, DROP
   RES. GROUP (2): ALTER, DROP
   STORAGE (3):    USAGE, ALTER, DROP
   WAREHOUSE (3):  USAGE, ALTER, DROP
   USER (1):       IMPERSONATE

🎭 Built-in Role Assignment (capabilities):
   ☐ user_admin (manage users & roles)
   ☐ security_admin (security operations)
   ☐ cluster_admin (cluster management)
   ☐ db_admin (database administration)
```

### Guard Rails for Nova UI

```
❌ Cannot DROP:        root, nova_admin (users) | ACCOUNTADMIN, built-in roles (roles)
❌ Cannot REVOKE from:  root, nova_admin (users)
❌ Cannot GRANT to custom roles: GRANT, NODE (system privileges)
✅ Can always:          CREATE ROLE, DROP ROLE (non-protected), GRANT/REVOKE object privs
```
