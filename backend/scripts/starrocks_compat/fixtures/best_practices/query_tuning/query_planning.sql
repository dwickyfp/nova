-- Minimal TPC-DS store_sales, household_demographics, time_dim and store (only the columns the page's query uses).
CREATE TABLE store_sales (
    ss_sold_time_sk INT, ss_hdemo_sk INT, ss_store_sk INT, ss_quantity INT
) DUPLICATE KEY(ss_sold_time_sk) DISTRIBUTED BY HASH(ss_sold_time_sk) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE household_demographics (
    hd_demo_sk INT, hd_dep_count INT
) DUPLICATE KEY(hd_demo_sk) DISTRIBUTED BY HASH(hd_demo_sk) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE time_dim (
    t_time_sk INT, t_hour INT, t_minute INT
) DUPLICATE KEY(t_time_sk) DISTRIBUTED BY HASH(t_time_sk) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE store (
    s_store_sk INT, s_store_name VARCHAR(50)
) DUPLICATE KEY(s_store_sk) DISTRIBUTED BY HASH(s_store_sk) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO store_sales VALUES (30600, 1, 1, 2), (30660, 2, 1, 1);
INSERT INTO household_demographics VALUES (1, 5), (2, 3);
INSERT INTO time_dim VALUES (30600, 8, 30), (30660, 8, 31);
INSERT INTO store VALUES (1, 'ese');
