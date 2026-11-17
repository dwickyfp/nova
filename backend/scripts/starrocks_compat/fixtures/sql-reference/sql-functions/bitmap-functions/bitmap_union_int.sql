-- tbl1 (INT k1) with two distinct values, as printed.
CREATE TABLE tbl1 (k1 INT)
DUPLICATE KEY(k1) DISTRIBUTED BY HASH(k1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO tbl1 VALUES (1), (2), (2);
