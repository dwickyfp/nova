-- test(c1, c2 with a default) in partitions p1/p2, sources test2/test3, and the wiki_edit tables from loading/InsertInto.md.
CREATE TABLE test (c1 INT, c2 INT DEFAULT "0") DUPLICATE KEY(c1)
PARTITION BY RANGE(c1) (PARTITION p1 VALUES LESS THAN ("2"), PARTITION p2 VALUES LESS THAN ("100"))
DISTRIBUTED BY HASH(c1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE test2 (c1 INT, c2 INT) DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE test3 (c1 INT, c2 INT) DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO test2 VALUES (1, 10), (3, 30);
INSERT INTO test3 VALUES (1, 11), (3, 33);
CREATE TABLE insert_wiki_edit (
    event_time      DATETIME,
    channel         VARCHAR(32)      DEFAULT '',
    user            VARCHAR(128)     DEFAULT '',
    is_anonymous    TINYINT          DEFAULT '0',
    is_minor        TINYINT          DEFAULT '0',
    is_new          TINYINT          DEFAULT '0',
    is_robot        TINYINT          DEFAULT '0',
    is_unpatrolled  TINYINT          DEFAULT '0',
    delta           INT              DEFAULT '0',
    added           INT              DEFAULT '0',
    deleted         INT              DEFAULT '0'
)
DUPLICATE KEY(event_time, channel, user, is_anonymous, is_minor, is_new, is_robot, is_unpatrolled)
PARTITION BY RANGE(event_time)(
    PARTITION p06 VALUES LESS THAN ('2015-09-12 06:00:00'),
    PARTITION p12 VALUES LESS THAN ('2015-09-12 12:00:00'),
    PARTITION p18 VALUES LESS THAN ('2015-09-12 18:00:00'),
    PARTITION p24 VALUES LESS THAN ('2015-09-13 00:00:00')
)
DISTRIBUTED BY HASH(user) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE source_wiki_edit LIKE insert_wiki_edit;
INSERT INTO source_wiki_edit VALUES
    ("2015-09-12 00:00:00","#en.wikipedia","AustinFF",0,0,0,0,0,21,5,0),
    ("2015-09-12 00:00:00","#ca.wikipedia","helloSR",0,1,0,1,0,3,23,0);
