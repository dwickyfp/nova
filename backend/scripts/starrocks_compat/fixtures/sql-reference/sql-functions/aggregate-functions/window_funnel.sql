-- action, action1, action2 and action3, with the rows the page prints.
CREATE TABLE action (uid INT, event_type VARCHAR(16), `time` DATETIME)
DUPLICATE KEY(uid) DISTRIBUTED BY HASH(uid) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO action VALUES
    (1, 'Browse', '2020-01-02 11:00:00'),
    (1, 'Click', '2020-01-02 11:10:00'),
    (1, 'Order', '2020-01-02 11:20:00'),
    (1, 'Pay', '2020-01-02 11:30:00'),
    (1, 'Browse', '2020-01-02 11:00:00'),
    (2, 'Order', '2020-01-02 11:00:00'),
    (2, 'Pay', '2020-01-02 11:10:00'),
    (3, 'Browse', '2020-01-02 11:20:00'),
    (3, 'Click', '2020-01-02 12:00:00'),
    (4, 'Browse', '2020-01-02 11:50:00'),
    (4, 'Click', '2020-01-02 12:00:00'),
    (5, 'Browse', '2020-01-02 11:50:00'),
    (5, 'Click', '2020-01-02 12:00:00'),
    (5, 'Order', '2020-01-02 11:10:00'),
    (6, 'Browse', '2020-01-02 11:50:00'),
    (6, 'Click', '2020-01-02 12:00:00'),
    (6, 'Order', '2020-01-02 12:10:00');
CREATE TABLE action1 (uid INT, event_type VARCHAR(16), `time` DATETIME)
DUPLICATE KEY(uid) DISTRIBUTED BY HASH(uid) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO action1 VALUES
    (1, 'Browse', '2020-01-02 11:00:00'),
    (2, 'Browse', '2020-01-02 11:00:01'),
    (1, 'Click', '2020-01-02 11:10:00'),
    (1, 'Order', '2020-01-02 11:29:00'),
    (1, 'Click', '2020-01-02 11:29:50'),
    (1, 'Pay', '2020-01-02 11:30:00'),
    (1, 'Click', '2020-01-02 11:40:00');
CREATE TABLE action2 (uid INT, event_type VARCHAR(16), `time` DATETIME)
DUPLICATE KEY(uid) DISTRIBUTED BY HASH(uid) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO action2 VALUES
    (1, 'Browse', '2020-01-02 11:00:00'),
    (2, 'Browse', '2020-01-02 11:00:01'),
    (1, 'Click', '2020-01-02 11:10:00'),
    (1, 'Pay', '2020-01-02 11:30:00'),
    (1, 'Order', '2020-01-02 11:31:00');
CREATE TABLE action3 (uid INT, event_type VARCHAR(16), `time` DATETIME)
DUPLICATE KEY(uid) DISTRIBUTED BY HASH(uid) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO action3 VALUES
    (1, 'Browse', '2020-01-02 11:00:00'),
    (1, 'Click', '2020-01-02 11:00:01'),
    (2, 'Browse', '2020-01-02 11:00:03'),
    (1, 'Order', '2020-01-02 11:00:31'),
    (2, 'Click', '2020-01-02 11:00:03'),
    (2, 'Order', '2020-01-02 11:01:03');
