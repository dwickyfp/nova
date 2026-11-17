-- test1.table1 for the LIKE example (the docs describe a MySQL external table; a native table stands in).
CREATE DATABASE IF NOT EXISTS test1;
CREATE TABLE test1.table1 (
    k1 INT,
    k2 VARCHAR(32),
    v1 DATE
) DUPLICATE KEY(k1)
DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
