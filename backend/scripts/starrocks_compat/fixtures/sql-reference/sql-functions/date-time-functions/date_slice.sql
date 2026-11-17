-- test_all_type_select, with the rows the page prints.
CREATE TABLE test_all_type_select (
    id_date DATE,
    id_datetime DATETIME,
    id_int INT
) DUPLICATE KEY(id_date) DISTRIBUTED BY HASH(id_int) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO test_all_type_select VALUES
    ('2052-12-26', '1691-12-23 04:01:09', 0),
    ('2168-08-05', '2169-12-18 15:44:31', 1),
    ('1737-02-06', '1840-11-23 13:09:50', 2),
    ('2245-10-01', '1751-03-21 00:19:04', 3),
    ('1889-10-27', '1861-09-12 13:28:18', 4);
