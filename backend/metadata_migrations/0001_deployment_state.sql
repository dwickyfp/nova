CREATE TABLE nova_meta.schema_migrations (
    version bigint PRIMARY KEY CHECK (version > 0),
    name text NOT NULL UNIQUE,
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    app_version text NOT NULL CHECK (app_version ~ '^[0-9a-f]{40}$')
);

CREATE TABLE nova_meta.deployment_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    schema_version bigint NOT NULL CHECK (schema_version > 0),
    metadata_epoch uuid,
    maintenance boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (metadata_epoch IS NOT NULL OR maintenance)
);

-- Schema installation cannot authorize serving an unvalidated metadata import.
INSERT INTO nova_meta.deployment_state (schema_version) VALUES (1);

REVOKE ALL ON SCHEMA nova_meta FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA nova_meta FROM PUBLIC;
GRANT USAGE ON SCHEMA nova_meta TO nova_meta_app, nova_meta_readonly;
GRANT SELECT ON nova_meta.schema_migrations, nova_meta.deployment_state
    TO nova_meta_app, nova_meta_readonly;
