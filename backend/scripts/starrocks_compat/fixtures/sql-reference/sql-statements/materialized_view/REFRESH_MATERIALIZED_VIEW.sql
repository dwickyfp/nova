-- lo_mv1: a date-partitioned asynchronous MV (manual refresh) with base partitions in Feb 2020 for the partition-range examples.
CREATE TABLE lo_base (lo_orderdate DATE NOT NULL, lo_orderkey INT, lo_revenue INT)
DUPLICATE KEY(lo_orderdate) PARTITION BY date_trunc('day', lo_orderdate)
DISTRIBUTED BY HASH(lo_orderkey) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO lo_base VALUES ('2020-01-15', 1, 10), ('2020-02-10', 2, 20), ('2020-02-20', 3, 30);
CREATE MATERIALIZED VIEW lo_mv1 PARTITION BY lo_orderdate DISTRIBUTED BY HASH(lo_orderkey)
REFRESH DEFERRED MANUAL
AS SELECT lo_orderdate, lo_orderkey, sum(lo_revenue) AS total_revenue FROM lo_base GROUP BY lo_orderdate, lo_orderkey;
