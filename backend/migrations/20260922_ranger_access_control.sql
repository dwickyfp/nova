-- Ranger-backed authorization control-plane metadata.
-- Runtime object authorization remains in Ranger; these rows only track
-- projection lifecycle, desired hashes, scope bindings, and drift.

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SECURITY_ROLE_PROJECTIONS (
    role_name VARCHAR(128) NOT NULL,
    state VARCHAR(32) NOT NULL,
    marker_exists BOOLEAN NOT NULL DEFAULT FALSE,
    ranger_exists BOOLEAN NOT NULL DEFAULT FALSE,
    message VARCHAR(2048) NULL,
    updated_at DATETIME NOT NULL
)
PRIMARY KEY (role_name)
DISTRIBUTED BY HASH(role_name) BUCKETS 1
PROPERTIES ("replication_num" = "1", "enable_persistent_index" = "true");

ALTER TABLE NOVA_SYSTEM.AUDIT_LOG
    ADD COLUMN IF NOT EXISTS active_role VARCHAR(128);
ALTER TABLE NOVA_SYSTEM.AUDIT_LOG
    ADD COLUMN IF NOT EXISTS security_context_version BIGINT;
ALTER TABLE NOVA_SYSTEM.AUDIT_LOG
    ADD COLUMN IF NOT EXISTS decision VARCHAR(32);
ALTER TABLE NOVA_SYSTEM.AUDIT_LOG
    ADD COLUMN IF NOT EXISTS ranger_policy_ids VARCHAR(2048);

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SECURITY_POLICIES (
    policy_name VARCHAR(512) NOT NULL,
    ranger_id BIGINT NULL,
    ranger_guid VARCHAR(128) NULL,
    state VARCHAR(32) NOT NULL,
    desired_hash VARCHAR(65533) NULL,
    updated_at DATETIME NOT NULL
)
PRIMARY KEY (policy_name)
DISTRIBUTED BY HASH(policy_name) BUCKETS 1
PROPERTIES ("replication_num" = "1", "enable_persistent_index" = "true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_DATA_SCOPE_ASSIGNMENTS (
    assignment_id VARCHAR(128) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    dimension_key VARCHAR(128) NOT NULL,
    scope_values_json VARCHAR(65533) NOT NULL,
    state VARCHAR(32) NOT NULL,
    updated_at DATETIME NOT NULL
)
PRIMARY KEY (assignment_id)
DISTRIBUTED BY HASH(assignment_id) BUCKETS 1
PROPERTIES ("replication_num" = "1", "enable_persistent_index" = "true");
