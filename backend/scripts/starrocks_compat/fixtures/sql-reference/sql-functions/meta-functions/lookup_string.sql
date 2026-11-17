-- t2, a Primary Key table where key 1 has event_day 2020-01-14, as printed.
CREATE TABLE t2 (id INT, event_day DATE, pv BIGINT)
PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t2 VALUES (1, '2020-01-14', 2), (2, '2020-01-15', 3);
