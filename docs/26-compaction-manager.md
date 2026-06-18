# Module 26: Compaction Manager

> Monitor and manage data compaction — merging versions for read performance.

---

## Concept

Compaction merges multiple data versions into one, improving query performance. StarRocks does this automatically, but manual compaction may be needed after bulk loads.

---

## Operations

### Manual Compaction

```sql
-- Full table compaction
ALTER TABLE orders COMPACT;

-- Segment compaction only
ALTER TABLE orders COMPACT SEGMENT;

-- Partition compaction
ALTER TABLE orders COMPACT PARTITION(p202601);
```

### Monitor Compaction

```sql
-- Compaction status per BE
SELECT * FROM information_schema.be_compactions;

-- Cloud-native compaction (shared-data)
SELECT * FROM information_schema.be_cloud_native_compactions;

-- Tablet compaction score
SHOW TABLET FROM orders;  -- shows compaction_score column
```

---

## Nova UI

```
┌─ Compaction Manager ────────────────────────────────────┐
│                                                          │
│  ── Compaction Status ──                                 │
│  Table               Partitions  Avg Score  Status       │
│  orders              12          3.2        🟢 Healthy  │
│  events              24          8.7        🟡 Slow      │
│  logs                48          15.3       🔴 Backlog   │
│  payments            6           1.1        🟢 Healthy  │
│                                                          │
│  ── Actions ──                                           │
│  [Compact Selected]  [Compact All]  [Auto-Compact: ON]  │
│                                                          │
│  ── Compaction History ──                                │
│  Table      Partition   Type      Duration  Rows Merged  │
│  orders     p202601     Full      12s       45,000       │
│  events     ALL         Segment   3s        12,000       │
│  logs       p202606     Full      45s       1.2M         │
└──────────────────────────────────────────────────────────┘
```

---

## Compaction Score Guide

| Score | Status | Action |
|-------|--------|--------|
| 0-5 | Healthy | None |
| 5-10 | Moderate | Monitor |
| 10-20 | Slow | Consider manual compaction |
| >20 | Backlog | Immediate manual compaction |
