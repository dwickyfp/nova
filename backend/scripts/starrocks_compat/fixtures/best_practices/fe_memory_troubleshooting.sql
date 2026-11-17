-- The AuditLoader table starrocks_audit_db__.starrocks_audit_tbl__ (schema from audit_loader.md) with a few recent queries.
CREATE DATABASE IF NOT EXISTS starrocks_audit_db__;
CREATE TABLE starrocks_audit_db__.starrocks_audit_tbl__ (
  `queryId` VARCHAR(64),
  `timestamp` DATETIME NOT NULL,
  `queryType` VARCHAR(12),
  `clientIp` VARCHAR(32),
  `user` VARCHAR(64),
  `authorizedUser` VARCHAR(64),
  `resourceGroup` VARCHAR(64),
  `catalog` VARCHAR(32),
  `db` VARCHAR(96),
  `state` VARCHAR(8),
  `errorCode` VARCHAR(512),
  `queryTime` BIGINT,
  `scanBytes` BIGINT,
  `scanRows` BIGINT,
  `returnRows` BIGINT,
  `cpuCostNs` BIGINT,
  `memCostBytes` BIGINT,
  `stmtId` INT,
  `isQuery` TINYINT,
  `feIp` VARCHAR(128),
  `stmt` VARCHAR(1048576),
  `digest` VARCHAR(32),
  `planCpuCosts` DOUBLE,
  `planMemCosts` DOUBLE,
  `pendingTimeMs` BIGINT,
  `candidateMVs` VARCHAR(65533) NULL,
  `hitMvs` VARCHAR(65533) NULL,
  `QueriedRelations` ARRAY<VARCHAR(65533)> NULL,
  `warehouse` VARCHAR(32) NULL
) ENGINE = OLAP
DUPLICATE KEY (`queryId`, `timestamp`, `queryType`)
PARTITION BY date_trunc('day', `timestamp`)
DISTRIBUTED BY RANDOM BUCKETS 1
PROPERTIES ("replication_num" = "1", "partition_live_number" = "30");
INSERT INTO starrocks_audit_db__.starrocks_audit_tbl__
    (queryId, `timestamp`, queryType, user, state, queryTime, cpuCostNs, memCostBytes, isQuery, feIp, stmt)
VALUES
    ('q1', date_sub(now(), INTERVAL 10 MINUTE), 'query', 'analyst', 'EOF', 1200, 3000000000, 524288000, 1, '172.26.0.2', 'select count(*) from orders'),
    ('q2', date_sub(now(), INTERVAL 10 MINUTE), 'query', 'analyst', 'EOF', 800, 1000000000, 104857600, 1, '172.26.0.2', 'select * from orders limit 10'),
    ('q3', date_sub(now(), INTERVAL 2 HOUR), 'query', 'etl', 'OK', 60000, 90000000000, 4294967296, 0, '172.26.0.2', 'insert overwrite sales select * from sales_stage'),
    ('q4', date_sub(now(), INTERVAL 1 DAY), 'query', 'report', 'EOF', 300, 500000000, 52428800, 1, '172.26.0.2', 'select region, sum(amount) from sales group by region'),
    ('q5', date_sub(now(), INTERVAL 1 DAY), 'query', 'report', 'ERR', 10, 1000000, 1048576, 1, '172.26.0.2', 'select * from missing_table');
