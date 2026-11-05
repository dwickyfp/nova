# Module 20: Data Governance

> Dynamic data masking, row access policies, object tagging, and data lineage.

---

## Dynamic Data Masking

Mask sensitive columns based on user role — same column shows different data to different users.

### Create Masking Policy

```sql
-- Email masking
CREATE MASKING POLICY email_mask AS (val STRING) ->
  CASE
    WHEN current_role() IN ('admin', 'data_engineer') THEN val
    ELSE CONCAT('***', SUBSTR(val, LENGTH(val) - 3))
  END;

-- Phone masking
CREATE MASKING POLICY phone_mask AS (val STRING) ->
  CASE
    WHEN current_role() = 'admin' THEN val
    ELSE CONCAT('***-***-', RIGHT(val, 4))
  END;

-- Number masking (salary)
CREATE MASKING POLICY salary_mask AS (val DECIMAL) ->
  CASE
    WHEN current_role() = 'admin' THEN val
    ELSE NULL
  END;

-- Partial mask (credit card)
CREATE MASKING POLICY cc_mask AS (val STRING) ->
  CASE
    WHEN current_role() = 'admin' THEN val
    ELSE CONCAT('****-****-****-', RIGHT(val, 4))
  END;
```

### Apply to Columns

```sql
ALTER TABLE customers MODIFY COLUMN email SET MASKING POLICY email_mask;
ALTER TABLE customers MODIFY COLUMN phone SET MASKING POLICY phone_mask;
ALTER TABLE employees MODIFY COLUMN salary SET MASKING POLICY salary_mask;
```

### Operations

```sql
SHOW MASKING POLICIES;
DESC MASKING POLICY email_mask;
ALTER MASKING POLICY email_mask SET BODY -> <new_expression>;
DROP MASKING POLICY email_mask;
```

### Nova UI

```
┌─ Data Masking Policies ─────────────────────────────────┐
│                                                          │
│  [+ Create Policy]                                       │
│                                                          │
│  Name          Applied To         Expression             │
│  email_mask    customers.email    ***@domain.com         │
│  phone_mask    customers.phone    ***-***-1234           │
│  salary_mask   employees.salary   [hidden]               │
│  cc_mask       orders.cc_number   ****-****-****-1234    │
│                                                          │
│  ── Preview ──                                           │
│  As role: [admin ▼]                                      │
│  email: john.doe@company.com  ← full                    │
│                                                          │
│  As role: [analyst ▼]                                    │
│  email: ***pany.com  ← masked                           │
└──────────────────────────────────────────────────────────┘
```

---

## Row Access Policies

Control which rows each user can see — row-level security.

### Create Row Access Policy

```sql
-- Regional access: users only see their region
CREATE ROW ACCESS POLICY region_policy AS (region STRING) ->
  CASE
    WHEN current_role() = 'admin' THEN TRUE
    WHEN current_role() = 'west_team' AND region = 'west' THEN TRUE
    WHEN current_role() = 'east_team' AND region = 'east' THEN TRUE
    ELSE FALSE
  END;

-- Department access
CREATE ROW ACCESS POLICY dept_policy AS (department STRING) ->
  current_role() = 'admin' OR
  department IN (SELECT dept FROM user_departments WHERE user = current_user());
```

### Apply to Tables

```sql
ALTER TABLE sales ADD ROW ACCESS POLICY region_policy ON (region);
ALTER TABLE hr_data ADD ROW ACCESS POLICY dept_policy ON (department);
```

### Operations

```sql
SHOW ROW ACCESS POLICIES;
DESC ROW ACCESS POLICY region_policy;
ALTER TABLE sales DROP ROW ACCESS POLICY region_policy;
DROP ROW ACCESS POLICY region_policy;
```

### Nova UI

```
┌─ Row Access Policies ───────────────────────────────────┐
│                                                          │
│  [+ Create Policy]                                       │
│                                                          │
│  Name           Applied Tables      Roles                │
│  region_policy  sales, orders       admin, west, east    │
│  dept_policy    hr_data             admin, dept_*        │
│                                                          │
│  ── Test as User ──                                      │
│  Test as: [analyst ▼]  Role: [west_team ▼]              │
│  Table: sales                                            │
│  Visible rows: 1,240 / 5,000 (filtered by region=west)  │
│                                                          │
│  ┌──────────────────────────────────────────────┐       │
│  │ date       | region | amount | customer      │       │
│  │ 2026-01-15 | west   | 1500   | Acme Corp     │       │
│  │ 2026-01-16 | west   | 2300   | Beta Inc      │       │
│  │ (east rows hidden)                            │       │
│  └──────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

---

## Object Tagging

Tags are key-value metadata on catalogs, databases, tables, views,
materialized views, and columns. They are stored in
`NOVA_SYSTEM.CONFIG_OBJECT_TAGS` keyed by the object's securable id, so a tag
survives a rename-free drop and shows the object as dropped. Business
descriptions, owners, and column classifications sit beside them in
`CATALOG_ANNOTATIONS`. The current behaviour, permissions, audit, and API are
in [Catalog Explorer: Business metadata, search, and lineage](03-catalog-explorer.md#business-metadata-search-and-lineage).

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG_OBJECT_TAGS (
    securable_id   VARCHAR(64) NOT NULL,   -- CATALOG_SECURABLES.id
    tag_key        VARCHAR(128) NOT NULL,  -- pii, team, cost_center
    tag_value      VARCHAR(512),
    securable_type VARCHAR(32) NOT NULL,   -- catalog, database, table, view, materialized_view, column
    created_at     DATETIME,
    created_by     VARCHAR(128),
    updated_at     DATETIME,
    updated_by     VARCHAR(128)
) PRIMARY KEY(securable_id, tag_key)
DISTRIBUTED BY HASH(securable_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

Tags are descriptive. A `pii` tag or a `CONFIDENTIAL` classification does not
mask anything by itself: Ranger masks and row filters remain the only
enforcement.

---

## Data Lineage

Table-to-table lineage is harvested from `NOVA_SYSTEM.AUDIT_LOG`: successful
`INSERT ... SELECT`, `CREATE TABLE ... AS SELECT`, `CREATE VIEW`, and
`CREATE MATERIALIZED VIEW` statements run through Nova are parsed with the SQL
frontend parser (`backend/app/sql_frontend/analysis/lineage.py`) and stored
per pair of securable ids in `NOVA_SYSTEM.CATALOG_LINEAGE`. A caller sees an
edge only when both relations are visible to their active role. Column-level
lineage, stage loads (`LINEAGE_LOAD_HISTORY`), and the graph view below are not
built yet.

### Lineage UI (proposed)

```
┌─ Data Lineage: DATALAKE.bronze.orders ──────────────────┐
│                                                          │
│  ┌─────────────┐                                        │
│  │ @stage1     │                                        │
│  │ orders.csv  │──── LOAD ────┐                         │
│  └─────────────┘              │                         │
│                               ▼                         │
│                    ┌──────────────────┐                 │
│                    │ DATALAKE.bronze  │                 │
│                    │ .orders          │                 │
│                    └────────┬─────────┘                 │
│                             │                            │
│                    ┌────────┼────────┐                   │
│                    ▼        ▼        ▼                   │
│            ┌──────────┐ ┌──────┐ ┌──────────┐          │
│            │ bronze   │ │ gold │ │ gold     │          │
│            │ .archive │ │ .agg │ │ .report  │          │
│            └──────────┘ └──────┘ └──────────┘          │
│                                                          │
│  Impact: 3 downstream objects affected                   │
└──────────────────────────────────────────────────────────┘
```
