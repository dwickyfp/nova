-- exam, with the rows the page prints.
CREATE TABLE exam (Subject VARCHAR(16), Score INT)
DUPLICATE KEY(Subject) DISTRIBUTED BY HASH(Subject) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO exam VALUES
    ('chemistry', 80), ('chemistry', 100), ('chemistry', NULL),
    ('math', 60), ('math', 70), ('math', 85),
    ('physics', 75), ('physics', 80), ('physics', 85), ('physics', 99);
