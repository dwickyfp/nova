-- lineorder, a few SSB rows with lo_quantity.
CREATE TABLE lineorder (lo_orderkey INT, lo_quantity INT)
DUPLICATE KEY(lo_orderkey) DISTRIBUTED BY HASH(lo_orderkey) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO lineorder VALUES (1, 17), (2, 36), (3, 8), (4, 28), (5, 24), (6, 32), (7, 38), (8, 45);
