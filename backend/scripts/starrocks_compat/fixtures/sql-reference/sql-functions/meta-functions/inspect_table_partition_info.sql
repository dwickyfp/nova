-- ss, the table whose partition information the page inspects.
CREATE TABLE ss (event_day DATE, pv BIGINT)
DUPLICATE KEY(event_day) DISTRIBUTED BY HASH(event_day) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO ss VALUES ("2020-01-14", 2), ("2020-01-14", 3), ("2020-01-15", 2);
