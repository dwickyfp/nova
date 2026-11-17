-- Asynchronous MVs the examples alter (lo_mv1, lo_mv2, mv1 with col1/col2, order_mv, order_mv1) over one small base table.
CREATE TABLE mv_base (dt DATE NOT NULL, col1 INT, col2 VARCHAR(20), revenue INT)
DUPLICATE KEY(dt) PARTITION BY date_trunc('day', dt)
DISTRIBUTED BY HASH(col1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO mv_base VALUES ('2023-07-01', 1, 'a', 10), ('2023-07-02', 2, 'b', 20);
CREATE MATERIALIZED VIEW lo_mv1 DISTRIBUTED BY HASH(col1) REFRESH DEFERRED MANUAL
AS SELECT col1, sum(revenue) AS total_revenue FROM mv_base GROUP BY col1;
CREATE MATERIALIZED VIEW lo_mv2 DISTRIBUTED BY HASH(col1) REFRESH DEFERRED ASYNC EVERY (INTERVAL 1 HOUR)
AS SELECT col1, sum(revenue) AS total_revenue FROM mv_base GROUP BY col1;
CREATE MATERIALIZED VIEW mv1 DISTRIBUTED BY HASH(col1) REFRESH DEFERRED MANUAL
AS SELECT dt, col1, col2, revenue FROM mv_base;
CREATE MATERIALIZED VIEW order_mv DISTRIBUTED BY HASH(col1) REFRESH DEFERRED MANUAL
AS SELECT dt, col1, sum(revenue) AS total_revenue FROM mv_base GROUP BY dt, col1;
CREATE MATERIALIZED VIEW order_mv1 DISTRIBUTED BY HASH(col1) REFRESH DEFERRED MANUAL
AS SELECT dt, col1, sum(revenue) AS total_revenue FROM mv_base GROUP BY dt, col1;
