-- Databases example_db and test_db, example_db.my_table, test_db.test_tbl and tbl (with a partition TTL) for the CREATE TABLE examples.
CREATE DATABASE IF NOT EXISTS example_db;
CREATE TABLE example_db.my_table (
    k1 INT,
    v1 VARCHAR(32)
) DUPLICATE KEY(k1)
DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE DATABASE IF NOT EXISTS test_db;
CREATE TABLE test_db.test_tbl (
    k1 INT,
    v1 VARCHAR(32)
) DUPLICATE KEY(k1)
DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE tbl (
    dt DATE NOT NULL,
    v INT
) DUPLICATE KEY(dt)
PARTITION BY date_trunc('month', dt)
DISTRIBUTED BY HASH(dt) BUCKETS 1
PROPERTIES (
    "replication_num" = "1",
    "partition_retention_condition" = "dt >= CURRENT_DATE() - INTERVAL 3 MONTH"
);
