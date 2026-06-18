# Gap Analysis — Missing Features

> Cross-check against StarRocks 4.1 capabilities + Snowflake-like features.
> 32 features identified as missing from current Nova docs.

---

## Summary

| Priority | Count | Description |
|----------|-------|-------------|
| 🔴 HIGH | 7 | Core operational/security gaps — should ship |
| 🟡 MEDIUM | 11 | Operational efficiency + Snowflake parity — v1.1 |
| 🟢 LOW | 14 | Nice-to-have developer experience — backlog |

---

## 🔴 HIGH PRIORITY (Must Ship)

### 1. Backup & Restore

**StarRocks:** `BACKUP SNAPSHOT`, `RESTORE SNAPSHOT`, `RECOVER TABLE/DB/PARTITION`
**Snowflake:** Time Travel + Fail-safe

```sql
-- StarRocks commands
BACKUP SNAPSHOT db.snapshot_name TO repo ON (TABLE tbl) PROPERTIES("type"="full");
RESTORE SNAPSHOT db.snapshot_name FROM repo ON (TABLE tbl);
RECOVER TABLE db.table_name;
RECOVER DATABASE db_name;
RECOVER TABLE db.table_name PARTITION(p1);
```

**Nova features:**
- Backup scheduler (daily/weekly snapshots)
- One-click restore from snapshot
- Recover deleted tables/partitions (within retention period)
- Cross-cluster restore
- Snapshot browser (list, compare, delete)

**Add to:** New doc or extend Cluster Monitor

---

### 2. Dynamic Data Masking

**StarRocks:** `CREATE MASKING POLICY`, `ALTER TABLE ... SET MASKING POLICY` (since 3.5)
**Snowflake:** Dynamic Data Masking

```sql
-- Create masking policy
CREATE MASKING POLICY email_mask AS (val STRING) ->
  CASE
    WHEN current_role() IN ('admin') THEN val
    ELSE CONCAT('***', SUBSTR(val, LENGTH(val) - 3))
  END;

-- Apply to column
ALTER TABLE customers MODIFY COLUMN email SET MASKING POLICY email_mask;
```

**Nova features:**
- Masking policy CRUD (create/edit/delete)
- Column-level binding (drag & drop)
- Preview masked vs unmasked data
- Role-based visibility (admin sees full, others see masked)
- Built-in templates (email, phone, SSN, credit card)

---

### 3. Row Access Policies

**StarRocks:** Row-level security policies (since 3.5)
**Snowflake:** Row Access Policies

```sql
-- Create row access policy
CREATE ROW ACCESS POLICY region_policy AS (region_col VARCHAR) ->
  CASE
    WHEN current_role() = 'admin' THEN TRUE
    WHEN current_role() = 'west_sales' AND region_col = 'west' THEN TRUE
    ELSE FALSE
  END;

-- Apply to table
ALTER TABLE sales ADD ROW ACCESS POLICY region_policy ON (region);
```

**Nova features:**
- Policy CRUD
- Table-level binding
- Test-as-user preview (see what different users see)
- Policy audit (who can see what)

**Add to:** Extend User & Access Control or new "Data Governance" doc

---

### 4. Password Policies

**StarRocks:** Password policy system variables
**Snowflake:** Password Policy objects

```sql
-- StarRocks system variables
SET GLOBAL password_lifetime = 90;
SET GLOBAL password_history = 5;
SET GLOBAL failed_login_attempts = 5;
SET GLOBAL password_lock_time = 30;
SET GLOBAL validate_password = ON;
```

**Nova features:**
- Password policy configuration UI
- Expiration, complexity, history, lockout settings
- Per-user or global policies
- Password reset flow

**Add to:** Extend Authentication doc

---

### 5. Network Policies

**StarRocks:** FE config / system variables
**Snowflake:** Network Policy objects

```sql
-- StarRocks config
-- fe.conf: enable_ip_based_authentication = true
```

**Nova features:**
- IP allowlist/blocklist configuration
- Per-user network restrictions
- Connection source tracking

**Add to:** Extend Authentication doc

---

### 6. Inverted Index (Full-Text Search)

**StarRocks 4.1:** Built-in CLucene inverted index

```sql
-- Create inverted index
ALTER TABLE articles ADD INDEX idx_content (content) USING GIN (
    "parser" = "english"
);

-- Query with full-text search
SELECT * FROM articles
WHERE articles MATCH_ANY('machine learning')
ORDER BY score DESC;

-- Supported parsers: english, chinese, standard, unicode, ngram
```

**Nova features:**
- Inverted index management in Table Manager
- Index configuration (parser, analyzer, ngram params)
- MATCH_ANY / MATCH_ALL query builder
- Search preview with relevance scoring

**Add to:** Extend Table Manager indexes tab

---

### 7. Session & Global Variables

**StarRocks:** `SET SESSION/GLOBAL variable = value`
**Snowflake:** Session/Parameter management

```sql
SHOW VARIABLES;
SHOW GLOBAL VARIABLES;
SET SESSION query_timeout = 300;
SET GLOBAL enable_pipeline_engine = true;
```

**Nova features:**
- Variable browser (search, filter, paginate)
- Session vs Global toggle
- Set variable UI (with validation)
- Reset to default
- Variable documentation tooltip

**Add to:** New "Variables & Settings" section or Settings page

---

## 🟡 MEDIUM PRIORITY (v1.1)

### 8. Colocate Groups

**StarRocks:** `colocate_with` table property

```sql
CREATE TABLE orders (...) PROPERTIES("colocate_with" = "order_group");
CREATE TABLE order_items (...) PROPERTIES("colocate_with" = "order_group");

SHOW PROCEDURE STATUS;  -- shows colocate group status
```

**Nova features:**
- Colocate group list + status
- Create/manage groups
- Balance status visualization
- Group-to-table mapping

**Add to:** Extend Table Manager or Cluster Monitor

---

### 9. Compaction Management

**StarRocks:** Manual compaction commands

```sql
ALTER TABLE tbl COMPACT;           -- full compaction
ALTER TABLE tbl COMPACT SEGMENT;   -- segment compaction
SHOW PROCEDURE STATUS;              -- compaction progress
```

**Nova features:**
- Compaction trigger per table
- Compaction status dashboard
- Compaction score monitoring
- Auto-compaction recommendations

**Add to:** Extend Cluster Monitor

---

### 10. Storage Volumes (Shared-Data Mode)

**StarRocks 4.1:** `CREATE STORAGE VOLUME`

```sql
CREATE STORAGE VOLUME my_volume
    TYPE = S3
    LOCATIONS = ("s3://bucket/path")
    PROPERTIES(
        "aws.s3.endpoint" = "...",
        "aws.s3.access_key" = "...",
        "aws.s3.secret_key" = "..."
    );

ALTER STORAGE VOLUME my_volume SET ("enabled" = "true");
SET my_volume AS DEFAULT STORAGE VOLUME;
```

**Nova features:**
- Storage volume CRUD
- Volume-to-database binding
- Default volume management
- Volume health monitoring

**Add to:** Extend Storage Connections or new doc

---

### 11. Auto Tablet Split/Merge (v4.1)

```sql
-- Enable per table
ALTER TABLE tbl SET ("enable_automatic_tablet_split" = "true");
ALTER TABLE tbl SET ("tablet_split_size_threshold_bytes" = "1073741824");

-- Monitor
SHOW TABLET FROM tbl;  -- shows tablet distribution
```

**Nova features:**
- Enable/disable per table
- Split/merge event visualization
- Threshold configuration
- Tablet distribution heatmap

**Add to:** Extend Cluster Monitor

---

### 12. Fast Schema Evolution v2 (v4.1)

```sql
-- Enable at table creation
CREATE TABLE tbl (...) PROPERTIES("fast_schema_evolution" = "true");

-- Instant DDL: ADD/DROP COLUMN becomes synchronous
ALTER TABLE tbl ADD COLUMN new_col INT;
ALTER TABLE tbl DROP COLUMN old_col;
```

**Nova features:**
- Enable/disable per table
- Visual indicator for instant DDL tables
- Schema change history

**Add to:** Extend Table Manager

---

### 13. Cache Observability

```sql
-- BE cache metrics
SELECT * FROM information_schema.be_metrics WHERE name LIKE '%cache%';
```

**Nova features:**
- Cache hit/miss ratio dashboard
- Cache size per BE
- Eviction rate monitoring
- Per-table cache effectiveness

**Add to:** Extend Cluster Monitor

---

### 14. Time Travel / Historical Query

**StarRocks:** Limited (recycle bin + binlog_mode)
**Snowflake:** Full Time Travel (up to 90 days)

```sql
-- StarRocks: recover from recycle bin
RECOVER TABLE db.table_name;

-- StarRocks: binlog-based (limited)
SET enable_binlog = true;
```

**Nova features:**
- Recycle bin browser (deleted tables, partitions)
- Recovery countdown timer
- Binlog status monitoring
- Point-in-time query (where supported)

**Add to:** New doc or extend Data Recovery

---

### 15. Object Tagging

**StarRocks:** No native tagging
**Snowflake:** Object Tagging

**Nova features (custom implementation):**
- Tag tables/columns/schemas with key-value pairs
- Tag-based search ("find all PII columns")
- Tag inheritance (schema → table → column)
- Tag-based masking integration

**Add to:** New "Data Governance" or extend Catalog Explorer

---

### 16. Data Lineage Visualization

**StarRocks:** No native lineage
**Snowflake:** ACCESS_HISTORY + lineage

**Nova features:**
- Parse query history to build table→table dependency graph
- Visual DAG (directed acyclic graph)
- Column-level lineage (basic)
- Impact analysis ("what breaks if I drop this table?")

**Add to:** New "Lineage" section

---

### 17. Worksheet Sharing / Collaboration

**Snowflake:** Snowsight Worksheets sharing

**Nova features:**
- Share worksheet via link (read-only or edit)
- Comment threads on queries
- Worksheet folders/organization
- Export as .sql file

**Add to:** Extend SQL Worksheet

---

### 18. Dashboards / Charting

**Snowflake:** Snowsight Dashboards

**Nova features:**
- Save query results as charts (bar, line, pie, table)
- Pin charts to dashboard
- Auto-refresh intervals
- Dashboard sharing
- Parameterized dashboards

**Add to:** New "Dashboards" doc

---

## 🟢 LOW PRIORITY (Backlog)

### 19. Data Recovery (Advanced)

**StarRocks:** `RECOVER TABLE/PARTITION/DATABASE`

```sql
RECOVER TABLE db.table_name BEFORE_DROP;
```

### 20. SQL Digest & Advanced Audit

**StarRocks:** SQL digest for query fingerprinting

### 21. Zero-Copy Cloning

**StarRocks:** `CREATE TABLE LIKE` (not true zero-copy)
**Snowflake:** `CREATE TABLE ... CLONE`

### 22. Stored Procedures / Anonymous Blocks

**StarRocks 4.1:** Anonymous blocks support
**Snowflake:** Full stored procedure language

### 23. Streams (CDC)

**StarRocks:** binlog + external CDC (Flink)
**Snowflake:** Native Streams

### 24. Schema Diff / Migration Tool

Compare two schemas, generate migration SQL.

### 25. Code Snippets / Templates

ETL patterns, admin scripts, common queries library.

### 26. ERD Diagrams

Auto-generate entity-relationship diagrams from schema.

### 27. Git Integration (dbt/sqlmesh)

Connect Git repos, show dbt models, deploy from branch.

### 28. Cost / Credit Tracking

Track compute hours per user, estimate cost.

### 29. Resource Monitors

Set spend thresholds, auto-suspend, alert notifications.

### 30. Data Sharing

Export-as-view or catalog-based sharing.

### 31. Tablet Repair/Clone

**StarRocks:** `ADMIN REPAIR TABLET`, `ADMIN CLONE TABLET`

### 32. Storage Encryption

**StarRocks:** TDE (Transparent Data Encryption)

---

## Updated Module Map

```
docs/
│
│  ── EXISTING (26 docs) ──
├── 01-overview.md
├── 02 ~ 19 (feature + auth + ML)
├── arch-01 ~ arch-07 (architecture)
│
│  ── NEW DOCS NEEDED (8 docs) ──
├── 20-data-governance.md        ← masking + row access + tagging + lineage
├── 21-backup-recovery.md        ← backup, restore, recover, time travel
├── 22-variables-settings.md     ← session/global variables, password policies
├── 23-dashboards.md             ← charts, dashboards, visualization
├── 24-advanced-indexes.md       ← inverted index, full-text search
├── 25-storage-volumes.md        ← shared-data mode volumes
├── 26-compaction-manager.md     ← compaction status, manual trigger
└── 27-data-sharing.md           ← share data with external consumers
│
│  ── DOCS TO EXTEND ──
├── 11-user-access-control.md    ← add row access policies, network policies
├── 13-cluster-monitor.md        ← add cache obs, compaction, tablet repair, cost
├── 18-authentication.md         ← add password policies, network policies
└── 02-sql-worksheet.md          ← add snippets, sharing, variables panel
```

---

## Implementation Roadmap

### v1.0 (MVP)
- All existing 26 docs
- HIGH priority items (7): masking, row access, password policy, network policy, inverted index, variables, backup

### v1.1
- MEDIUM priority (11): colocate, compaction, storage volumes, tablet split, cache obs, time travel, tagging, lineage, worksheets, dashboards

### v2.0
- LOW priority (14): cloning, stored procedures, streams, schema diff, snippets, ERD, git integration, cost tracking, resource monitors, data sharing, tablet repair, encryption
