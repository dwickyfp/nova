-- hllDemo (k1, HLL v1) for the insert example.
CREATE TABLE hllDemo (k1 INT, v1 HLL HLL_UNION)
AGGREGATE KEY(k1) DISTRIBUTED BY HASH(k1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
