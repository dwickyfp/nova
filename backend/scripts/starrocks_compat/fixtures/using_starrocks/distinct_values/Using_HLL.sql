-- db0.table0 with a column c2 for the SQL-only HLL estimate.
CREATE DATABASE IF NOT EXISTS db0;
CREATE TABLE db0.table0 (c1 INT, c2 VARCHAR(32)) DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO db0.table0 VALUES (1, 'a'), (2, 'b'), (3, 'a'), (4, 'c');
