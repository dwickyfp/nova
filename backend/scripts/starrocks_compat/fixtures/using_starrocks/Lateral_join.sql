-- tests: a student and an array of scores, for the UNNEST examples.
CREATE TABLE tests (
    student VARCHAR(32),
    scores ARRAY<INT>
) DUPLICATE KEY(student) DISTRIBUTED BY HASH(student) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO tests VALUES ('Alice', [90, 85]), ('Bob', [70, 95, 80]);
