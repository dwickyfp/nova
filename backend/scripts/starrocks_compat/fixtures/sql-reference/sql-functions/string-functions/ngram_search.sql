-- string_table (rowkey), with the rows the page prints.
CREATE TABLE string_table (rowkey VARCHAR(64))
DUPLICATE KEY(rowkey) DISTRIBUTED BY HASH(rowkey) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO string_table VALUES
    ('31dc496b-760d-6f1a-4521-050073a70000'), ('31dc496b-760d-6f1a-4521-050073a40000'),
    ('31dc496b-760d-6f1a-4521-05007fa70000'), ('31dc496b-760d-6f1a-4521-050073a30000'),
    ('31dc496b-760d-6f1a-4521-0500c3a70000'), ('9a8b7c6d-1111-2222-3333-444455556666');
