-- t1 (c1, BITMAP c2) holding 1..10 for c1 = 1, as printed.
CREATE TABLE t1 (c1 INT, c2 BITMAP BITMAP_UNION)
AGGREGATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (1, bitmap_from_string('1,2,3,4,5,6,7,8,9,10'));
