-- t (a, name, pv), with the rows the page prints.
CREATE TABLE t (a INT, name VARCHAR(16), pv INT)
DUPLICATE KEY(a) DISTRIBUTED BY HASH(a) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t VALUES (11, '', 33), (2, NULL, 334), (1, 'fzh', 3), (1, 'fff', 4), (1, 'fff', 5);
