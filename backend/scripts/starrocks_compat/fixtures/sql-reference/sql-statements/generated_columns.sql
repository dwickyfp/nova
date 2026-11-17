-- test_tbl3 with generated columns newcol1/newcol2 and its row (the page creates it in an indented block the probe does not run).
CREATE TABLE test_tbl3 (
    id INT NOT NULL,
    data_array ARRAY<int> NOT NULL,
    data_json JSON NOT NULL,
    newcol1 DOUBLE AS array_avg(data_array),
    newcol2 String AS json_string(json_query(data_json, "$.a"))
) PRIMARY KEY (id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO test_tbl3 (id, data_array, data_json) VALUES (1, [1,2], parse_json('{"a" : 1, "b" : 2}'));
