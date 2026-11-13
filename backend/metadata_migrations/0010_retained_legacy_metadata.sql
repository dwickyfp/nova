CREATE TABLE nova_meta.config_dashboards (
    id varchar(64) PRIMARY KEY,
    name varchar(256) NOT NULL,
    description text CHECK (char_length(description)<=65533),
    is_shared boolean,
    created_by varchar(128),
    created_at timestamptz
);
CREATE TABLE nova_meta.config_dashboard_widgets (
    id varchar(64) PRIMARY KEY,
    dashboard_id varchar(64),
    title varchar(256),
    sql_text text CHECK (char_length(sql_text)<=65533),
    chart_type varchar(32),
    x_axis varchar(64),
    y_axis varchar(64),
    position_x integer,
    position_y integer,
    width integer,
    height integer,
    refresh_seconds integer,
    created_at timestamptz
);
CREATE TABLE nova_meta.config_pinned_queries (
    id varchar(64) PRIMARY KEY,
    user_name varchar(128) NOT NULL,
    name varchar(256) NOT NULL,
    sql_text text NOT NULL CHECK (char_length(sql_text)<=65533),
    database_name varchar(128),
    schema_name varchar(128),
    is_shared boolean,
    created_at timestamptz
);
CREATE TABLE nova_meta.lineage_load_history (
    record_id uuid NOT NULL CHECK (record_id<>'00000000-0000-0000-0000-000000000000'::uuid),
    load_id bigint NOT NULL,
    target_table varchar(512) NOT NULL,
    started_at timestamptz NOT NULL,
    stage_id varchar(64),
    file_path varchar(1024),
    file_format varchar(32),
    rows_loaded bigint,
    rows_rejected bigint,
    load_time_ms bigint,
    status varchar(32),
    error_message text CHECK (char_length(error_message)<=65533),
    completed_at timestamptz,
    PRIMARY KEY (started_at,record_id)
) PARTITION BY RANGE (started_at);
CREATE TABLE nova_meta.lineage_load_history_unpartitioned
    PARTITION OF nova_meta.lineage_load_history DEFAULT;
CREATE TABLE nova_meta.quality_table_stats (
    record_id uuid NOT NULL CHECK (record_id<>'00000000-0000-0000-0000-000000000000'::uuid),
    stat_id bigint NOT NULL,
    table_name varchar(512) NOT NULL,
    database_name varchar(128),
    schema_name varchar(128),
    row_count bigint,
    data_size_bytes bigint,
    index_size_bytes bigint,
    partition_count integer,
    collected_at timestamptz NOT NULL,
    PRIMARY KEY (collected_at,record_id)
) PARTITION BY RANGE (collected_at);
CREATE TABLE nova_meta.quality_table_stats_unpartitioned
    PARTITION OF nova_meta.quality_table_stats DEFAULT;
CREATE TABLE nova_meta.stage_file_manifest (
    record_id uuid PRIMARY KEY CHECK (record_id<>'00000000-0000-0000-0000-000000000000'::uuid),
    file_id bigint NOT NULL,
    stage_id varchar(64) NOT NULL,
    file_path varchar(1024) NOT NULL,
    file_name varchar(256) NOT NULL,
    file_size bigint,
    file_format varchar(32),
    uploaded_at timestamptz NOT NULL,
    uploaded_by varchar(128),
    checksum varchar(128)
);
CREATE TABLE nova_meta.usage_query_stats (
    record_id uuid NOT NULL CHECK (record_id<>'00000000-0000-0000-0000-000000000000'::uuid),
    stat_id bigint NOT NULL,
    user_name varchar(128),
    started_at timestamptz NOT NULL,
    query_id varchar(128),
    database_name varchar(128),
    sql_text text CHECK (char_length(sql_text)<=65533),
    query_type varchar(32),
    execution_time_ms bigint,
    rows_scanned bigint,
    rows_returned bigint,
    memory_used_bytes bigint,
    cpu_time_ms bigint,
    spill_bytes bigint,
    completed_at timestamptz,
    PRIMARY KEY (started_at,record_id)
) PARTITION BY RANGE (started_at);
CREATE TABLE nova_meta.usage_query_stats_unpartitioned
    PARTITION OF nova_meta.usage_query_stats DEFAULT;

CREATE FUNCTION nova_meta.retained_metadata_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user<>'nova_meta_owner' OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('config_dashboards','config_dashboard_widgets',
           'config_pinned_queries','lineage_load_history','quality_table_stats',
           'stage_file_manifest','usage_query_stats','lineage_load_history_unpartitioned',
           'quality_table_stats_unpartitioned','usage_query_stats_unpartitioned') THEN
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
    FOREACH name IN ARRAY ARRAY['config_dashboards','config_dashboard_widgets',
        'config_pinned_queries','lineage_load_history','quality_table_stats',
        'stage_file_manifest','usage_query_stats'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY retained_import ON nova_meta.%I TO nova_meta_owner '
            'USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) '
            'WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',name);
        EXECUTE format('CREATE TRIGGER retained_metadata_write_guard '
            'BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I '
            'FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.retained_metadata_write_guard()',name);
        EXECUTE format('COMMENT ON TABLE nova_meta.%I IS %L',name,
            'Retained legacy metadata; no current application data consumer');
    END LOOP;
    FOREACH name IN ARRAY ARRAY['lineage_load_history_unpartitioned',
        'quality_table_stats_unpartitioned','usage_query_stats_unpartitioned'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE TRIGGER retained_metadata_write_guard '
            'BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I '
            'FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.retained_metadata_write_guard()',name);
    END LOOP;
END
$access$;
