-- Tables t(a, b) and tb(x, z, a) for the sample join query.
CREATE TABLE t (a INT, b INT) DUPLICATE KEY(a) DISTRIBUTED BY HASH(a) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE tb (x INT, z INT, a INT) DUPLICATE KEY(x) DISTRIBUTED BY HASH(x) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t VALUES (1, 10), (1, 11), (2, 20);
INSERT INTO tb VALUES (1, 100, 11), (2, 200, 12), (3, 300, 5);
