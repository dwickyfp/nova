-- dict (global dictionary) and dest_table (dict_mapping column) from the page, loaded with the rows of batch1.csv and batch2.csv
-- (the page creates both tables in list items and loads them with Stream Load).
CREATE TABLE dict (
    order_uuid STRING,
    order_id_int BIGINT AUTO_INCREMENT
)
PRIMARY KEY (order_uuid)
DISTRIBUTED BY HASH (order_uuid) BUCKETS 1
PROPERTIES ("replication_num" = "1", "replicated_storage" = "true");
INSERT INTO dict (order_uuid) VALUES ('a1'), ('a2'), ('a3'), ('a101'), ('a102');
CREATE TABLE dest_table (
    id BIGINT,
    order_uuid STRING,
    batch INT comment 'Used to distinguish different batch loading',
    order_id_int BIGINT AS dict_mapping('dict', order_uuid)
)
DUPLICATE KEY (id, order_uuid)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO dest_table (id, order_uuid, batch) VALUES
    (1, 'a1', 1), (2, 'a2', 1), (3, 'a3', 1), (11, 'a1', 1), (11, 'a2', 1), (12, 'a1', 1),
    (1, 'a2', 2), (2, 'a2', 2), (3, 'a2', 2), (11, 'a2', 2), (12, 'a101', 2), (12, 'a102', 2), (13, 'a102', 2);
