-- t1 (c1), with the rows the page prints.
CREATE TABLE t1 (id INT, c1 INT)
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (1, NULL), (2, 1);
