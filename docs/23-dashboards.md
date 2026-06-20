# Module 23: Dashboards

> Save query results as interactive charts and pin to dashboards.

---

## Chart Types

| Type | Best For | Example |
|------|----------|---------|
| **Table** | Raw data, detailed results | Query output |
| **Bar** | Comparing categories | Sales by region |
| **Line** | Trends over time | Daily revenue |
| **Pie** | Proportions | Traffic sources |
| **Area** | Volume over time | Load volume daily |
| **Scatter** | Correlation | Price vs. demand |
| **Heatmap** | Density/distribution | Query latency by hour |
| **KPI** | Single metric | Total revenue |

---

## Dashboard Operations

### Create Dashboard

```
┌─ Dashboard: ETL Overview ───────────────────────────────┐
│                                                          │
│  [+ Add Widget]  [Edit Layout] [Share] [Refresh]         │
│                                                          │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐       │
│  │ 📊 KPI      │ │ 📊 KPI      │ │ 📊 KPI      │       │
│  │ Total Rows  │ │ Load Jobs   │ │ Error Rate  │       │
│  │ 12.4M       │ │ 247 today   │ │ 0.2%        │       │
│  └─────────────┘ └─────────────┘ └─────────────┘       │
│                                                          │
│  ┌────────────────────────────────────────────────┐     │
│  │ 📈 Line Chart: Daily Load Volume               │     │
│  │                                                │     │
│  │     ╱╲    ╱╲                                   │     │
│  │    ╱  ╲╱╱  ╲   ╱╲                             │     │
│  │   ╱         ╲╱╱  ╲                            │     │
│  │  ╱               ╲                            │     │
│  │ Mon  Tue  Wed  Thu  Fri  Sat  Sun             │     │
│  └────────────────────────────────────────────────┘     │
│                                                          │
│  ┌──────────────────┐ ┌──────────────────┐              │
│  │ 📊 Bar: Loads    │ │ 📊 Pie: Sources  │              │
│  │ by Stage         │ │                  │              │
│  │                  │ │   Stage1 45%     │              │
│  │ ████████ 120     │ │   Stage2 30%     │              │
│  │ ██████ 89        │ │   Stage3 25%     │              │
│  │ ████ 56          │ │                  │              │
│  └──────────────────┘ └──────────────────┘              │
└──────────────────────────────────────────────────────────┘
```

### Widget Configuration

```
┌─ Add Widget ────────────────────────────────────────────┐
│                                                          │
│  Query:                                                  │
│  SELECT DATE(timestamp) AS dt, COUNT(*) AS loads         │
│  FROM NOVA_SYSTEM.AUDIT_LOG                              │
│  WHERE action = 'LOAD'                                  │
│  GROUP BY dt ORDER BY dt                                 │
│                                                          │
│  Chart Type: [Line ▼]                                    │
│  X-axis: [dt ▼]                                          │
│  Y-axis: [loads ▼]                                       │
│  Title: [Daily Load Volume                    ]          │
│  Auto-refresh: [Every 5 minutes ▼]                       │
│                                                          │
│  [Preview] [Add to Dashboard]                            │
└──────────────────────────────────────────────────────────┘
```

---

## Data Model

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG_DASHBOARDS (
    id              VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(256) NOT NULL,
    description     TEXT,
    is_shared       BOOLEAN DEFAULT FALSE,
    created_by      VARCHAR(128),
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE NOVA_SYSTEM.CONFIG_DASHBOARD_WIDGETS (
    id              VARCHAR(64) PRIMARY KEY,
    dashboard_id    VARCHAR(64),
    title           VARCHAR(256),
    sql_text        TEXT,
    chart_type      VARCHAR(32),
    x_axis          VARCHAR(64),
    y_axis          VARCHAR(64),
    position_x      INT,
    position_y      INT,
    width           INT DEFAULT 4,
    height          INT DEFAULT 3,
    refresh_seconds INT DEFAULT 300,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```
