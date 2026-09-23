CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RUNS (
    run_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    last_sequence BIGINT NOT NULL,
    started_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(run_id)
DISTRIBUTED BY HASH(run_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS (
    run_id VARCHAR(64) NOT NULL,
    sequence BIGINT NOT NULL,
    frame TEXT NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(run_id, sequence)
DISTRIBUTED BY HASH(run_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
