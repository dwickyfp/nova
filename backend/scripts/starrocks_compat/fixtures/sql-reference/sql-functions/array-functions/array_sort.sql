-- test (c1, ARRAY c2), with the rows the page prints.
CREATE TABLE test (c1 INT, c2 ARRAY<INT>)
DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO test VALUES (1, [4,3,NULL,1]), (2, NULL), (3, [NULL]), (4, [8,5,1,4]);
