-- Retain the retired Smart/Auto columns byte-for-byte. Runtime writers have no
-- INSERT/UPDATE grants on these columns; importing them does not restart runs.
ALTER TABLE nova_meta.config_agent_runs
    ADD COLUMN root_run_id varchar(64),
    ADD COLUMN parent_run_id varchar(64),
    ADD COLUMN depth integer,
    ADD COLUMN objective text CHECK (char_length(objective)<=65533),
    ADD COLUMN payload jsonb,
    ADD COLUMN checkpoint jsonb,
    ADD COLUMN result_summary text CHECK (char_length(result_summary)<=65533),
    ADD COLUMN prompt_tokens bigint,
    ADD COLUMN completion_tokens bigint,
    ADD COLUMN lease_owner varchar(64),
    ADD COLUMN generation integer,
    ADD COLUMN error_class varchar(64);

-- The old source serialized these JSON documents into VARCHAR. Keep their
-- exact original text alongside the typed projection used by Stream writers.
ALTER TABLE nova_meta.config_stream_sources
    ADD COLUMN source_key_columns_text text,
    ADD COLUMN source_columns_json_text text;

CREATE FUNCTION nova_meta.metadata_import_authorized() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT current_user='nova_meta_owner' AND session_user='nova_meta_migrator'
        AND EXISTS (SELECT 1 FROM nova_meta.deployment_state
            WHERE singleton AND maintenance AND metadata_epoch IS NULL)
$body$;
-- These policies apply only before publication, to the migrator acting as
-- owner. They do not grant application logins access to imported private rows.
DO $policies$
DECLARE target record;
BEGIN
    FOR target IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='nova_meta' AND c.relkind IN ('r','p') AND c.relrowsecurity
            AND NOT c.relispartition LOOP
        EXECUTE format('CREATE POLICY import_snapshot_owner ON nova_meta.%I TO nova_meta_owner
            USING (nova_meta.metadata_import_authorized())
            WITH CHECK (nova_meta.metadata_import_authorized())', target.relname);
    END LOOP;
END
$policies$;
ALTER POLICY audit_actor ON nova_meta.audit_log USING (
    nova_meta.actor_context_valid() OR nova_meta.metadata_import_authorized()
);
