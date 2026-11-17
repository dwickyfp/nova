-- A small SSB lineorder for the digest example queries.
CREATE TABLE lineorder (lo_orderkey INT, lo_orderdate INT, lo_revenue INT)
DUPLICATE KEY(lo_orderkey) DISTRIBUTED BY HASH(lo_orderkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO lineorder VALUES (1, 19920105, 100), (2, 19920301, 200);
