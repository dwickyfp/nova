-- example_table (here and in example_db) and db1.sales_records with the synchronous materialized view store_amt.
CREATE TABLE example_table (
    k1 INT,
    v1 INT
) DUPLICATE KEY(k1)
DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE DATABASE IF NOT EXISTS example_db;
CREATE TABLE example_db.example_table (
    k1 INT,
    v1 INT
) DUPLICATE KEY(k1)
DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE DATABASE IF NOT EXISTS db1;
CREATE TABLE db1.sales_records (
    record_id INT,
    seller_id INT,
    store_id INT,
    sale_date DATE,
    sale_amt BIGINT
) DUPLICATE KEY(record_id, seller_id, store_id)
DISTRIBUTED BY HASH(record_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE MATERIALIZED VIEW db1.store_amt AS
SELECT store_id, SUM(sale_amt) FROM db1.sales_records GROUP BY store_id;
