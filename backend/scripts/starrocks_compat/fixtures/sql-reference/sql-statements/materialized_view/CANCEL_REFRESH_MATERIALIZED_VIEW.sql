-- lo_mv1: an asynchronous MV over a small base table whose refresh the examples cancel.
CREATE TABLE lo_base (lo_orderdate DATE NOT NULL, lo_orderkey INT, lo_revenue INT)
DUPLICATE KEY(lo_orderdate) DISTRIBUTED BY HASH(lo_orderkey) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO lo_base VALUES ('2020-02-10', 1, 10);
CREATE MATERIALIZED VIEW lo_mv1 DISTRIBUTED BY HASH(lo_orderkey) REFRESH DEFERRED MANUAL
AS SELECT lo_orderkey, sum(lo_revenue) AS total_revenue FROM lo_base GROUP BY lo_orderkey;
