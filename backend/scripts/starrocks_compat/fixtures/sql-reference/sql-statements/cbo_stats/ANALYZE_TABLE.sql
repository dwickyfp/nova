-- tbl_name with the columns the ANALYZE examples name (c1..c3, v1..v3).
CREATE TABLE tbl_name (c1 INT, c2 INT, c3 VARCHAR(20), v1 INT, v2 DOUBLE, v3 VARCHAR(20))
DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO tbl_name VALUES (1, 10, 'a', 100, 1.5, 'x'), (2, 20, 'b', 200, 2.5, 'y'), (3, 30, 'c', 300, 3.5, 'z');
