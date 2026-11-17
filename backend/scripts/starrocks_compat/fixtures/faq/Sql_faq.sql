-- lineitem (minimal TPC-H columns) for SHOW TABLET, and starrocks.ods_sale_branch for the DELETE example.
CREATE TABLE lineitem (
    l_orderkey BIGINT, l_linenumber INT, l_quantity DECIMAL(15,2), l_shipdate DATE
) DUPLICATE KEY(l_orderkey) DISTRIBUTED BY HASH(l_orderkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE DATABASE IF NOT EXISTS starrocks;
CREATE TABLE starrocks.ods_sale_branch (
    branch_id INT, create_time VARCHAR(8), amount DECIMAL(10,2)
) DUPLICATE KEY(branch_id) DISTRIBUTED BY HASH(branch_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO starrocks.ods_sale_branch VALUES (1, '202203', 10.00), (2, '202305', 20.00);
