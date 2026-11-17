-- test (id, arr1, arr2): the page's CREATE uses ARRAY<VARCHAR> without a length, which the engine rejects.
CREATE TABLE test (id INT, arr1 ARRAY<INT>, arr2 ARRAY<VARCHAR(16)>)
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
