-- item, a few TPC-DS item rows with i_current_price and i_rec_start_date.
CREATE TABLE item (i_item_sk INT, i_rec_start_date DATE, i_current_price DECIMAL(7,2))
DUPLICATE KEY(i_item_sk) DISTRIBUTED BY HASH(i_item_sk) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO item VALUES
    (1, '1997-10-27', 27.02), (2, '1997-10-27', 1.12), (3, '1999-10-28', 9.54),
    (4, '1999-10-28', 45.10), (5, '2000-10-27', 2.75), (6, '2000-10-27', 61.20),
    (7, '2001-10-27', 7.11), (8, '2001-10-27', 33.80), (9, NULL, 4.48), (10, NULL, 52.33);
