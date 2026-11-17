-- logs: fixed fields plus a JSON column remain_json, as the page's opening query assumes.
CREATE TABLE logs (
    `time` DATETIME,
    event VARCHAR(64),
    `user` VARCHAR(64),
    remain_json JSON
) DUPLICATE KEY(`time`) DISTRIBUTED BY RANDOM BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO logs VALUES
    ('2024-01-01 10:00:00', 'login', 'alice', parse_json('{"from_system": "web", "tag": "a"}')),
    ('2024-01-01 10:05:00', 'click', 'bob', parse_json('{"from_system": "app", "tag": "b"}'));
