-- tableA (Unique Key on k1, k2) for the sort-key materialized view, and db.tbl for the storage-medium/replica examples.
CREATE TABLE tableA (
    k1 INT, k2 INT, k3 INT
) UNIQUE KEY(k1, k2) DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO tableA VALUES (1, 1, 10), (2, 1, 20);
CREATE DATABASE IF NOT EXISTS db;
CREATE TABLE db.tbl (
    dt DATE, id INT, v INT
) DUPLICATE KEY(dt, id)
PARTITION BY RANGE(dt) (
    PARTITION p202401 VALUES LESS THAN ("2024-02-01"),
    PARTITION p202402 VALUES LESS THAN ("2024-03-01")
)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
