-- t1/t2 (v1, v2, v3) for the baseline examples, and minimal TPC-DS store_sales, catalog_sales and item.
CREATE TABLE t1 (v1 INT, v2 INT, v3 INT) DUPLICATE KEY(v1) DISTRIBUTED BY HASH(v1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE t2 (v1 INT, v2 INT, v3 INT) DUPLICATE KEY(v1) DISTRIBUTED BY HASH(v1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (1, 150, 10), (2, 50, 20), (30, 200, 30);
INSERT INTO t2 VALUES (1, 150, 15), (2, 200, 25);
CREATE TABLE item (
    i_item_sk INT, i_item_id VARCHAR(16), i_color VARCHAR(20)
) DUPLICATE KEY(i_item_sk) DISTRIBUTED BY HASH(i_item_sk) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE store_sales (
    ss_item_sk INT, ss_ext_sales_price DECIMAL(7,2)
) DUPLICATE KEY(ss_item_sk) DISTRIBUTED BY HASH(ss_item_sk) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE catalog_sales (
    cs_item_sk INT, cs_ext_sales_price DECIMAL(7,2)
) DUPLICATE KEY(cs_item_sk) DISTRIBUTED BY HASH(cs_item_sk) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO item VALUES (1, 'AAAA1', 'slate'), (2, 'AAAA2', 'blanched'), (3, 'AAAA3', '25');
INSERT INTO store_sales VALUES (1, 10.00), (2, 20.00), (3, 5.00);
INSERT INTO catalog_sales VALUES (1, 7.50), (3, 2.25);
