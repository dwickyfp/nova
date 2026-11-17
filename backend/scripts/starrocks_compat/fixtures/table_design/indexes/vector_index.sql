-- ivfpq: a 5-dimension vector table without an index, for the append/drop vector index examples.
-- Note: the page's CREATE INDEX / ADD INDEX use "nbits" = "10" and omit M_IVFPQ, which the engine rejects (docs error).
CREATE TABLE ivfpq (
    id     BIGINT(20)   NOT NULL COMMENT "",
    vector ARRAY<FLOAT> NOT NULL COMMENT ""
) ENGINE=OLAP
DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1", "replicated_storage" = "false");
INSERT INTO ivfpq VALUES (1, [1,2,3,4,5]), (2, [4,5,6,7,8]);
