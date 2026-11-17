-- t_recharge_detail2 (same shape as the page's t_recharge_detail1) holding partition p20220402_texas for the INSERT OVERWRITE example.
CREATE TABLE t_recharge_detail2 (
    id bigint,
    user_id bigint,
    recharge_money decimal(32,2),
    city varchar(20) not null,
    dt varchar(20) not null
)
DUPLICATE KEY(id)
PARTITION BY dt,city
DISTRIBUTED BY HASH(`id`) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t_recharge_detail2 VALUES (2, 2, 50.00, 'texas', '2022-04-02');
