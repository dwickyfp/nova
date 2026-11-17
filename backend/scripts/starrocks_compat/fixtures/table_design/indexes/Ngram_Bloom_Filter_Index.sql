-- The database test (the page creates test.table1) and an unqualified table1 like it for the SHOW/ALTER examples that omit the database.
CREATE DATABASE IF NOT EXISTS test;
CREATE TABLE table1
(
    k1 CHAR(10),
    k2 CHAR(10),
    v1 INT SUM,
    INDEX index_name (k2) USING NGRAMBF ("gram_num" = "4", "bloom_filter_fpp" = "0.05") COMMENT ''
)
ENGINE = olap
AGGREGATE KEY(k1, k2)
DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
