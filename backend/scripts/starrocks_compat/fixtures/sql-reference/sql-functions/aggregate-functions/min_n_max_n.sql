-- scores, from the page's numbered steps (indented blocks the probe does not extract).
CREATE TABLE scores (student_id INT, subject STRING, score INT)
DUPLICATE KEY(student_id) DISTRIBUTED BY HASH(student_id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO scores VALUES
    (1, 'math', 85), (2, 'math', 92), (3, 'math', 78), (4, 'math', 95), (5, 'math', 88),
    (6, 'math', 82), (7, 'math', 90), (8, 'math', 87), (9, 'math', 93), (10, 'math', 89);
