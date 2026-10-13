CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ASSISTANT_UI_STATE (
    state_id VARCHAR(64) NOT NULL,
    user_name VARCHAR(128) NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    message_id VARCHAR(64) NOT NULL,
    content_id VARCHAR(64) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    program_sha256 VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    state JSON NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(state_id)
DISTRIBUTED BY HASH(state_id) BUCKETS 1
ORDER BY (user_name, thread_id, message_id)
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
