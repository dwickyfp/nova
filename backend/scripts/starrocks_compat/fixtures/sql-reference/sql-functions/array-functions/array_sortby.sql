-- test_array_sortby_multi: the page creates "test_array_sortby_muliti" (typo) but inserts into and queries "_multi".
-- test_array is created by the page itself (replication_num is rewritten to 1).
CREATE TABLE test_array_sortby_multi (
    id INT NOT NULL,
    array_col1 ARRAY<INT>,
    array_col2 ARRAY<DOUBLE>,
    array_col3 ARRAY<VARCHAR(20)>,
    array_col4 ARRAY<DATE>
) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
