-- t (id, name, job1, job2), with the row the page prints.
CREATE TABLE t (id INT, name INT, job1 INT, job2 INT)
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t VALUES (2, 2, 2, 2);
