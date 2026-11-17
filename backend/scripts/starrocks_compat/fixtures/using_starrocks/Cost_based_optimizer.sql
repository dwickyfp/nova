-- tbl_name (columns c1..c3, v1..v3, the names the ANALYZE examples use) with a few rows, and an empty database db_name.
-- ex_hive_tbl is a Hive table and is not provided.
CREATE TABLE tbl_name (
    c1 INT, c2 INT, c3 VARCHAR(20), v1 INT, v2 INT, v3 VARCHAR(20)
) DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO tbl_name VALUES
    (1, 10, 'a', 100, 1000, 'x'),
    (2, 20, 'b', 200, 2000, 'y'),
    (3, 20, 'b', 300, 3000, 'z');
CREATE DATABASE IF NOT EXISTS db_name;
