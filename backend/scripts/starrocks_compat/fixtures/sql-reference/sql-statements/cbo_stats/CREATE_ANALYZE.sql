-- Database db_name and table tbl_name(c1, c2, c3) for the CREATE ANALYZE examples.
CREATE DATABASE IF NOT EXISTS db_name;
CREATE TABLE tbl_name (c1 INT, c2 INT, c3 VARCHAR(20)) DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO tbl_name VALUES (1, 10, 'a'), (2, 20, 'b');
