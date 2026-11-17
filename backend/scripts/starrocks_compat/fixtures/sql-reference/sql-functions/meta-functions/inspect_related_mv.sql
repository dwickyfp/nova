-- ss with two related materialized views, mv_on_view_1 (via view1) and test_mv1, as printed.
CREATE TABLE ss (event_day DATE, pv BIGINT)
DUPLICATE KEY(event_day) DISTRIBUTED BY HASH(event_day) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO ss VALUES ("2020-01-14", 2), ("2020-01-14", 3), ("2020-01-15", 2);
CREATE VIEW view1 AS SELECT event_day, sum(pv) AS sum_pv FROM ss GROUP BY event_day;
CREATE MATERIALIZED VIEW mv_on_view_1 DISTRIBUTED BY RANDOM BUCKETS 1 REFRESH DEFERRED MANUAL
PROPERTIES ("replication_num" = "1") AS SELECT * FROM view1;
CREATE MATERIALIZED VIEW test_mv1 DISTRIBUTED BY RANDOM BUCKETS 1 REFRESH DEFERRED MANUAL
PROPERTIES ("replication_num" = "1") AS SELECT event_day, sum(pv) AS sum_pv FROM ss GROUP BY event_day;
