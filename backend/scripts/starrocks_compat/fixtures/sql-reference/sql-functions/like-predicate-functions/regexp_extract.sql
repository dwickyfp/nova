-- quickstart.weatherdata (HourlySkyConditions), with the overcast rows the page prints.
CREATE DATABASE IF NOT EXISTS quickstart;
CREATE TABLE quickstart.weatherdata (id INT, HourlySkyConditions VARCHAR(64))
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO quickstart.weatherdata VALUES
    (1, 'OVC:08 110'), (2, 'OVC:08 120'), (3, 'OVC:08 120'), (4, 'OVC:08 30'), (5, 'OVC:08 29'),
    (6, 'OVC:08 27'), (7, 'OVC:08 26'), (8, 'OVC:08 22'), (9, 'OVC:08 23'), (10, 'OVC:08 22'),
    (11, 'FEW:02 50'), (12, 'CLR:00');
