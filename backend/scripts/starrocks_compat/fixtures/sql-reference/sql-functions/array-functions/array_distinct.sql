-- test (c1, ARRAY c2), with the rows the page prints.
CREATE TABLE test (c1 INT, c2 ARRAY<INT>)
DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO test VALUES (1, [1,1,2]), (2, [1,NULL,NULL]), (3, NULL), (4, [NULL]);
