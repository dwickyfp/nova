-- dict, the dictionary table of Example 1 (an indented step the probe does not extract).
CREATE TABLE dict (order_uuid STRING, order_id_int BIGINT AUTO_INCREMENT)
PRIMARY KEY (order_uuid) DISTRIBUTED BY HASH (order_uuid) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO dict (order_uuid) VALUES ('a1'), ('a2'), ('a3');
