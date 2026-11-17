-- example_db.test for SHOW DATA FROM example_db.test.
CREATE DATABASE IF NOT EXISTS example_db;
CREATE TABLE example_db.test (c1 INT, c2 INT) DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
