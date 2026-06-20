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

Tag databases, tables, columns with key-value metadata.

### Implementation (NOVA_SYSTEM)

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG_OBJECT_TAGS (
    object_type     VARCHAR(32),    -- DATABASE, TABLE, COLUMN
    object_name     VARCHAR(512),   -- db.table.column
    tag_key         VARCHAR(128),   -- pii, owner, team, cost_center
    tag_value       VARCHAR(512),
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by      VARCHAR(128),
    PRIMARY KEY (object_type, object_name, tag_key)
) DISTRIBUTED BY HASH(object_type) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### Tag Operations (via UI)

```
┌─ Tags ──────────────────────────────────────────────────┐
│                                                          │
│  [+ Add Tag]                                             │
│                                                          │
│  ── Tags on customers table ──                           │
│  Object        Key          Value        Applied By     │
│  customers     pii          true         admin          │
│  customers     team         data-eng     admin          │
│  customers.email pii        email        admin          │
│  customers.phone pii        phone        admin          │
│                                                          │
│  ── Search by Tag ──                                     │
│  Key: [pii ▼]  Value: [true ▼]                          │
│  Results: customers.email, customers.phone, hr.ssn       │
└──────────────────────────────────────────────────────────┘
```

---

## Data Lineage

Visualize data flow: which tables feed into which.

### Implementation

Parse `NOVA_SYSTEM.AUDIT_LOG` to build dependency graph:

```python
# services/lineage_service.py
def build_lineage(target_table: str) -> dict:
    """Parse INSERT INTO ... SELECT FROM to build lineage."""
    result = sr_execute(f"""
        SELECT sql_text, timestamp
        FROM NOVA_SYSTEM.AUDIT_LOG
        WHERE action IN ('INSERT', 'CREATE_TABLE', 'CTAS')
        AND target LIKE '%{target_table}%'
        AND status = 'SUCCESS'
        ORDER BY timestamp DESC
    """)
    
    sources = set()
    for row in result["rows"]:
        sql = row[0]
        # Parse: INSERT INTO target SELECT ... FROM source
        parsed = parse_sql_lineage(sql)
        sources.update(parsed["sources"])
    
    return {"target": target_table, "sources": list(sources)}
```

### Lineage UI

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
