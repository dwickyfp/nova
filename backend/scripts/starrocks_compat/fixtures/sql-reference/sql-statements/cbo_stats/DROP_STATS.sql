-- tbl_name for DROP STATS tbl_name.
CREATE TABLE tbl_name (c1 INT, col_name INT) DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
