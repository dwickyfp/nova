-- test_uv, an HLL table of unique visitors.
CREATE TABLE test_uv (dt DATE, uv_set HLL HLL_UNION)
AGGREGATE KEY(dt) DISTRIBUTED BY HASH(dt) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO test_uv VALUES
    ("2024-01-01", hll_hash(1)), ("2024-01-01", hll_hash(2)), ("2024-01-02", hll_hash(3));
