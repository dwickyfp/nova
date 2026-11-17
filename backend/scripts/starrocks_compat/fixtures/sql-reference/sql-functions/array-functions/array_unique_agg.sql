-- array_unique_agg_example (a, ARRAY b), with the rows the page prints.
CREATE TABLE array_unique_agg_example (a INT, b ARRAY<INT>)
DUPLICATE KEY(a) DISTRIBUTED BY HASH(a) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO array_unique_agg_example VALUES (2, [1,NULL,2,4]), (2, [1,NULL,3]), (1, [1,1,2,3]), (1, [2,3,4]);
