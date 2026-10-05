-- Per-reader reactions to News stories. Private to each reader.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_STORY_FEEDBACK (
    user_name VARCHAR(128) NOT NULL,
    story_id VARCHAR(64) NOT NULL,
    reaction VARCHAR(8) NOT NULL,
    features JSON NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(user_name, story_id)
DISTRIBUTED BY HASH(user_name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
