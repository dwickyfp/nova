# Module 27: Data Sharing

> Share data with external consumers without copying — views, exports, and catalog-based sharing.

---

## Sharing Methods

| Method | Description | Use Case |
|--------|-------------|----------|
| **Shared Views** | Grant SELECT on views | Internal teams |
| **Export to Stage** | Export to shared stage | External partners |
| **External Catalog** | Consumer creates catalog pointing to your data | Cross-cluster |
| **API Endpoint** | Query results via HTTP | Applications |

---

## Shared Views

```sql
-- Create a curated view for sharing
CREATE VIEW shared_order_summary AS
SELECT
    DATE(order_date) AS order_date,
    region,
    COUNT(*) AS order_count,
    SUM(amount) AS total_amount
FROM orders
GROUP BY 1, 2;

-- Grant access
GRANT SELECT ON shared_order_summary TO USER partner_user;
GRANT SELECT ON shared_order_summary TO ROLE partner_role;
```

## Export to Shared Stage

```sql
-- Export to partner stage
INSERT INTO @partner_stage.exports.order_report.parquet
SELECT * FROM shared_order_summary
WHERE order_date >= '2026-01-01';

-- Partner can read from their stage
-- (stage configured with partner's storage credentials)
```

## Cross-Cluster Sharing (External Catalog)

```sql
-- Consumer creates catalog pointing to shared data
CREATE EXTERNAL CATALOG shared_data_from_nova
PROPERTIES(
    "type" = "hive",
    "hive.metastore.uris" = "thrift://nova-hms:9083",
    "aws.s3.endpoint" = "http://nova-minio:9000",
    "aws.s3.access_key" = "***",
    "aws.s3.secret_key" = "***"
);

-- Consumer queries shared data
SELECT * FROM shared_data_from_nova.analytics.order_summary;
```

---

## Nova UI

```
┌─ Data Sharing ──────────────────────────────────────────┐
│                                                          │
│  [Shared Views] [Shared Stages] [External Access]        │
│                                                          │
│  ── Shared Views ──                                      │
│  View Name             Access To      Last Accessed      │
│  order_summary         partner_user   2 hours ago        │
│  customer_analytics    analyst_role   15 min ago         │
│  revenue_report        exec_team      1 day ago          │
│                                                          │
│  ── Shared Stages ──                                     │
│  Stage Name            Partner        Files   Last Sync  │
│  partner_acme_stage    Acme Corp      12      1h ago     │
│  partner_beta_stage    Beta Inc       5       3h ago     │
│                                                          │
│  [+ Create Shared View]  [+ Create Shared Stage]         │
└──────────────────────────────────────────────────────────┘
```

---

## Security

| Control | Implementation |
|---------|---------------|
| Access control | StarRocks GRANT/REVOKE on views |
| Data masking | Apply masking policies before sharing |
| Row filtering | Apply row access policies before sharing |
| Audit | Log all access to shared objects in NOVA_SYSTEM.AUDIT.LOG |
| Encryption | Data encrypted at rest (storage) and in transit (TLS) |
