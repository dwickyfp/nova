CREATE TABLE nova_meta.config_agent_capabilities (
    agent_id varchar(64) PRIMARY KEY,
    owner_name varchar(128) NOT NULL,
    manifest jsonb NOT NULL
);
CREATE TABLE nova_meta.config_agent_messages (
    message_id varchar(64) PRIMARY KEY,
    root_run_id varchar(64) NOT NULL,
    sender_run_id varchar(64) NOT NULL,
    recipient_run_id varchar(64) NOT NULL,
    message_type varchar(32) NOT NULL,
    correlation_id varchar(64),
    reply_to varchar(64),
    content text NOT NULL CHECK (char_length(content)<=65533),
    created_at timestamptz NOT NULL,
    consumed_at timestamptz,
    origin varchar(16) NOT NULL,
    delivery_mode varchar(16) NOT NULL
);
CREATE TABLE nova_meta.config_agent_session_events (
    event_id varchar(64) PRIMARY KEY,
    root_run_id varchar(64) NOT NULL,
    session_sequence bigint,
    run_id varchar(64) NOT NULL,
    event_type varchar(32) NOT NULL,
    payload jsonb,
    created_at timestamptz NOT NULL
);

CREATE OR REPLACE FUNCTION nova_meta.retained_metadata_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user<>'nova_meta_owner' OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('config_dashboards','config_dashboard_widgets',
           'config_pinned_queries','lineage_load_history','quality_table_stats',
           'stage_file_manifest','usage_query_stats','lineage_load_history_unpartitioned',
           'quality_table_stats_unpartitioned','usage_query_stats_unpartitioned',
           'config_agent_capabilities','config_agent_messages','config_agent_session_events') THEN
        RAISE EXCEPTION 'Retained metadata requires the import identity' USING ERRCODE='42501';
    END IF;
    PERFORM pg_advisory_xact_lock(5642823696587772993);
    IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false) THEN
        RAISE EXCEPTION 'Retained metadata requires maintenance' USING ERRCODE='55000';
    END IF;
    RETURN NULL;
END
$body$;

DO $access$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['config_agent_capabilities','config_agent_messages',
        'config_agent_session_events'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY retained_import ON nova_meta.%I TO nova_meta_owner '
            'USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) '
            'WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',name);
        EXECUTE format('CREATE TRIGGER retained_metadata_write_guard '
            'BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I '
            'FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.retained_metadata_write_guard()',name);
        EXECUTE format('COMMENT ON TABLE nova_meta.%I IS %L',name,
            'Retired agent history; import only; no runtime reader or writer');
    END LOOP;
END
$access$;
