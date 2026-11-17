-- tbl (k1, HLL v1): 3 distinct values for k1 = 1 and 4 for k1 = 2, as printed.
CREATE TABLE tbl (k1 INT, v1 HLL HLL_UNION)
AGGREGATE KEY(k1) DISTRIBUTED BY HASH(k1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO tbl VALUES
    (1, hll_hash(1)), (1, hll_hash(2)), (1, hll_hash(3)),
    (2, hll_hash(1)), (2, hll_hash(2)), (2, hll_hash(3)), (2, hll_hash(4));
