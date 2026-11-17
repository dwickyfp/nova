-- bitmap_table1 (target) and bitmap_table2 (source) for the Insert Into examples; the page creates pv_bitmap.
CREATE TABLE bitmap_table1 (id INT, id2 BITMAP BITMAP_UNION)
AGGREGATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE bitmap_table2 (id INT, id2 BITMAP BITMAP_UNION)
AGGREGATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO bitmap_table2 VALUES (1001, to_bitmap(1000)), (1002, to_bitmap(2000));
