-- Database test with table source_wiki_edit for the /dbs/test proc paths.
CREATE DATABASE IF NOT EXISTS test;
CREATE TABLE test.source_wiki_edit (event_time DATETIME, channel VARCHAR(32), user VARCHAR(128))
DUPLICATE KEY(event_time) DISTRIBUTED BY HASH(user) BUCKETS 1 PROPERTIES ("replication_num" = "1");
