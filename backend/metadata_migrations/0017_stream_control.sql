-- Nova Stream control metadata: namespace generations and their operation
-- facts, stream definitions, the source registry and its commit index,
-- arbitration claims, and consumption operations and receipts.
--
-- The source-row journal and lineage tables are not here: they commit with
-- the analytical data in the engine and stay there. Engine observations
-- (table identity, partition versions, columns, load/transaction evidence)
-- stay engine adapters. Everything below is Nova's own control state.
--
-- Rows carry provenance: imported source rows are kept as they were, while
-- identity, immutability and chain rules apply to rows written at runtime.
CREATE TABLE nova_meta.config_stream_sources (
    source_id varchar(64) PRIMARY KEY CHECK (octet_length(source_id)<=64),
    epoch bigint NOT NULL,
    database_name varchar(128) NOT NULL CHECK (octet_length(database_name)<=128),
    table_name varchar(128) NOT NULL CHECK (octet_length(table_name)<=128),
    table_id bigint NOT NULL,
    key_columns jsonb NOT NULL CHECK (jsonb_typeof(key_columns)='array'),
    columns_json jsonb NOT NULL CHECK (jsonb_typeof(columns_json)='array'),
    schema_fingerprint varchar(64) NOT NULL CHECK (octet_length(schema_fingerprint)<=64),
    journal_table varchar(128) NOT NULL CHECK (octet_length(journal_table)<=128),
    lineage_table varchar(128) NOT NULL CHECK (octet_length(lineage_table)<=128),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    status_reason varchar(512) CHECK (octet_length(status_reason)<=512),
    registered_by varchar(128) NOT NULL CHECK (octet_length(registered_by)<=128),
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- One live registration per table (the registry routes writes by lowercased
-- name); a recreated table first invalidates the previous registration.
CREATE UNIQUE INDEX config_stream_sources_live_table ON nova_meta.config_stream_sources
    (lower(database_name),lower(table_name))
    WHERE provenance='runtime' AND status IN ('REGISTERING','READY');
CREATE INDEX config_stream_sources_table ON nova_meta.config_stream_sources
    (database_name,table_name,epoch DESC);

CREATE TABLE nova_meta.audit_stream_source_commits (
    source_id varchar(64) NOT NULL CHECK (octet_length(source_id)<=64),
    commit_seq bigint NOT NULL,
    commit_id varchar(64) NOT NULL CHECK (octet_length(commit_id)<=64),
    version_before bigint NOT NULL,
    version_after bigint NOT NULL,
    row_count bigint NOT NULL,
    principal varchar(256) NOT NULL CHECK (octet_length(principal)<=256),
    active_role varchar(128) CHECK (octet_length(active_role)<=128),
    recorded_at timestamptz,
    recorded_by varchar(128) CHECK (octet_length(recorded_by)<=128),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (source_id,commit_seq)
);

CREATE TABLE nova_meta.config_stream_definitions (
    stream_id varchar(64) PRIMARY KEY CHECK (octet_length(stream_id)<=64),
    mode varchar(16) NOT NULL CHECK (octet_length(mode)<=16),
    source_id varchar(64) NOT NULL CHECK (octet_length(source_id)<=64),
    created_by varchar(128) NOT NULL CHECK (octet_length(created_by)<=128),
    created_at timestamptz,
    show_initial_rows boolean,
    initial_commit_seq bigint,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.audit_stream_claims (
    claim_key varchar(64) PRIMARY KEY CHECK (octet_length(claim_key)<=64),
    resource_id varchar(64) NOT NULL CHECK (octet_length(resource_id)<=64),
    epoch bigint NOT NULL,
    generation bigint NOT NULL,
    attempt_id varchar(64) NOT NULL CHECK (octet_length(attempt_id)<=64),
    operation_digest varchar(64) NOT NULL CHECK (octet_length(operation_digest)<=64),
    created_at timestamptz,
    recorded_by varchar(128) CHECK (octet_length(recorded_by)<=128),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX audit_stream_claims_operation ON nova_meta.audit_stream_claims (operation_digest);

CREATE TABLE nova_meta.audit_stream_namespace_operations (
    operation_digest varchar(64) PRIMARY KEY CHECK (octet_length(operation_digest)<=64),
    catalog_name varchar(128) NOT NULL CHECK (octet_length(catalog_name)<=128),
    database_name varchar(128) NOT NULL CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) NOT NULL CHECK (octet_length(schema_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    generation bigint NOT NULL,
    stream_id varchar(64) NOT NULL CHECK (octet_length(stream_id)<=64),
    source_database varchar(128) NOT NULL CHECK (octet_length(source_database)<=128),
    source_name varchar(128) NOT NULL CHECK (octet_length(source_name)<=128),
    source_id varchar(64) NOT NULL CHECK (octet_length(source_id)<=64),
    owner_role varchar(128) NOT NULL CHECK (octet_length(owner_role)<=128),
    cursor_epoch bigint NOT NULL,
    cursor_sequence bigint NOT NULL,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    created_at timestamptz,
    recorded_by varchar(128) CHECK (octet_length(recorded_by)<=128),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

-- Immutable namespace generations: a new generation is a new row, never an
-- upsert; the newest generation is the current state of a name.
CREATE TABLE nova_meta.config_streams (
    catalog_name varchar(128) NOT NULL CHECK (octet_length(catalog_name)<=128),
    database_name varchar(128) NOT NULL CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) NOT NULL CHECK (octet_length(schema_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    generation bigint NOT NULL,
    stream_id varchar(64) NOT NULL CHECK (octet_length(stream_id)<=64),
    source_database varchar(128) NOT NULL CHECK (octet_length(source_database)<=128),
    source_name varchar(128) NOT NULL CHECK (octet_length(source_name)<=128),
    source_id varchar(64) NOT NULL CHECK (octet_length(source_id)<=64),
    owner_role varchar(128) NOT NULL CHECK (octet_length(owner_role)<=128),
    cursor_epoch bigint NOT NULL,
    cursor_sequence bigint NOT NULL,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (catalog_name,database_name,schema_name,name,generation)
);
CREATE INDEX config_streams_stream ON nova_meta.config_streams (stream_id,generation);
CREATE INDEX config_streams_source ON nova_meta.config_streams (source_id);

CREATE TABLE nova_meta.audit_stream_consumption_operations (
    consume_id varchar(64) PRIMARY KEY CHECK (octet_length(consume_id)<=64),
    stream_id varchar(64) NOT NULL CHECK (octet_length(stream_id)<=64),
    generation bigint NOT NULL,
    source_id varchar(64) NOT NULL CHECK (octet_length(source_id)<=64),
    epoch bigint NOT NULL,
    sequence_from bigint NOT NULL,
    sequence_to bigint NOT NULL,
    schema_version bigint NOT NULL,
    operation_digest varchar(64) NOT NULL CHECK (octet_length(operation_digest)<=64),
    target_database varchar(256) NOT NULL CHECK (octet_length(target_database)<=256),
    target_object varchar(256) NOT NULL CHECK (octet_length(target_object)<=256),
    engine_label varchar(128) NOT NULL CHECK (octet_length(engine_label)<=128),
    principal varchar(256) NOT NULL CHECK (octet_length(principal)<=256),
    target_load_id bigint,
    target_transaction_id bigint,
    active_role varchar(128) CHECK (octet_length(active_role)<=128),
    security_context_version bigint,
    dispatch_attempt bigint,
    submission_digest varchar(64) NOT NULL CHECK (octet_length(submission_digest)<=64),
    cancellation_digest varchar(64) NOT NULL CHECK (octet_length(cancellation_digest)<=64),
    created_at timestamptz,
    recorded_by varchar(128) CHECK (octet_length(recorded_by)<=128),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- A decision digest names exactly one operation (winning_operation refuses
-- ambiguity); runtime rows cannot introduce a second.
CREATE UNIQUE INDEX audit_stream_consumption_operations_submission
    ON nova_meta.audit_stream_consumption_operations (submission_digest) WHERE provenance='runtime';
CREATE UNIQUE INDEX audit_stream_consumption_operations_cancellation
    ON nova_meta.audit_stream_consumption_operations (cancellation_digest) WHERE provenance='runtime';

CREATE TABLE nova_meta.audit_stream_consumptions (
    event_id varchar(64) PRIMARY KEY CHECK (octet_length(event_id)<=64),
    consume_id varchar(64) NOT NULL CHECK (octet_length(consume_id)<=64),
    stream_id varchar(64) NOT NULL CHECK (octet_length(stream_id)<=64),
    epoch bigint NOT NULL,
    generation bigint NOT NULL,
    sequence_from bigint NOT NULL,
    sequence_to bigint NOT NULL,
    operation_digest varchar(64) NOT NULL CHECK (octet_length(operation_digest)<=64),
    target_database varchar(256) NOT NULL CHECK (octet_length(target_database)<=256),
    target_object varchar(256) NOT NULL CHECK (octet_length(target_object)<=256),
    engine_label varchar(128) NOT NULL CHECK (octet_length(engine_label)<=128),
    principal varchar(256) NOT NULL CHECK (octet_length(principal)<=256),
    state varchar(32) NOT NULL CHECK (octet_length(state)<=32),
    receipt jsonb,
    created_at timestamptz,
    recorded_by varchar(128) CHECK (octet_length(recorded_by)<=128),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX audit_stream_consumptions_consume ON nova_meta.audit_stream_consumptions (consume_id);
CREATE INDEX audit_stream_consumptions_offsets ON nova_meta.audit_stream_consumptions
    (stream_id,epoch,state,sequence_from);
CREATE INDEX audit_stream_consumptions_recent ON nova_meta.audit_stream_consumptions
    (stream_id,created_at DESC);

DO $rls$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_stream_sources','audit_stream_source_commits',
            'config_stream_definitions','audit_stream_claims','audit_stream_namespace_operations',
            'config_streams','audit_stream_consumption_operations','audit_stream_consumptions'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY', tbl);
    END LOOP;
END
$rls$;

-- Stream metadata is control-plane state. Stream statements run through the
-- shared SQL executor (web, query, proxy, agent, task, notebook, automation);
-- the scheduler runs stream maintenance and stream-triggered task planning.
-- The service and the engine authorize callers, so access is per workload
-- through a bound actor context, never anonymously.
CREATE FUNCTION nova_meta.stream_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;
REVOKE ALL ON FUNCTION nova_meta.stream_actor_bound() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.stream_actor_bound()
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation,nova_meta_scheduler;

DO $policies$
DECLARE item record; sql_paths text:='web,query,proxy,agent,task,notebook,automation';
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_stream_sources', sql_paths||',scheduler', sql_paths, sql_paths||',scheduler'),
        ('audit_stream_source_commits', sql_paths||',scheduler', sql_paths, ''),
        ('config_stream_definitions', sql_paths||',scheduler', sql_paths, ''),
        ('audit_stream_claims', sql_paths||',scheduler', sql_paths||',scheduler', ''),
        ('audit_stream_namespace_operations', sql_paths||',scheduler', sql_paths||',scheduler', ''),
        ('config_streams', sql_paths||',scheduler', sql_paths||',scheduler', ''),
        ('audit_stream_consumption_operations', sql_paths||',scheduler', sql_paths, ''),
        ('audit_stream_consumptions', sql_paths||',scheduler', sql_paths, '')
    ) AS t(tbl,readers,inserters,updaters) LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO %s USING (nova_meta.stream_actor_bound())',
            item.tbl||'_read', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO %s WITH CHECK (nova_meta.stream_actor_bound())',
            item.tbl||'_insert', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r));
        IF item.updaters<>'' THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO %s USING (nova_meta.stream_actor_bound()) WITH CHECK (nova_meta.stream_actor_bound())',
                item.tbl||'_update', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.updaters,',')) r));
        END IF;
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            item.tbl||'_import', item.tbl);
        EXECUTE format('GRANT SELECT ON nova_meta.%I TO %s', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
    END LOOP;
END
$policies$;

-- No workload deletes stream metadata: a dropped stream is a new generation,
-- and facts, claims and receipts are kept as evidence.
GRANT INSERT (source_id,epoch,database_name,table_name,table_id,key_columns,columns_json,
        schema_fingerprint,journal_table,lineage_table,status)
    ON nova_meta.config_stream_sources
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation;
GRANT UPDATE (status,status_reason,metadata_revision) ON nova_meta.config_stream_sources
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation,nova_meta_scheduler;
GRANT INSERT (source_id,commit_seq,commit_id,version_before,version_after,row_count,principal,
        active_role)
    ON nova_meta.audit_stream_source_commits
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation;
GRANT INSERT (stream_id,mode,source_id,show_initial_rows,initial_commit_seq)
    ON nova_meta.config_stream_definitions
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation;
GRANT INSERT (claim_key,resource_id,epoch,generation,attempt_id,operation_digest)
    ON nova_meta.audit_stream_claims
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation,nova_meta_scheduler;
GRANT INSERT (operation_digest,catalog_name,database_name,schema_name,name,generation,stream_id,
        source_database,source_name,source_id,owner_role,cursor_epoch,cursor_sequence,status)
    ON nova_meta.audit_stream_namespace_operations
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation,nova_meta_scheduler;
GRANT INSERT (catalog_name,database_name,schema_name,name,generation,stream_id,source_database,
        source_name,source_id,owner_role,cursor_epoch,cursor_sequence,status)
    ON nova_meta.config_streams
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation,nova_meta_scheduler;
GRANT INSERT (consume_id,stream_id,generation,source_id,epoch,sequence_from,sequence_to,
        schema_version,operation_digest,target_database,target_object,engine_label,principal,
        target_load_id,target_transaction_id,active_role,security_context_version,
        dispatch_attempt,submission_digest,cancellation_digest)
    ON nova_meta.audit_stream_consumption_operations
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation;
GRANT INSERT (event_id,consume_id,stream_id,epoch,generation,sequence_from,sequence_to,
        operation_digest,target_database,target_object,engine_label,principal,state,receipt)
    ON nova_meta.audit_stream_consumptions
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation;

CREATE FUNCTION nova_meta.stream_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Stream import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_query','nova_meta_proxy','nova_meta_agent',
            'nova_meta_task','nova_meta_notebook','nova_meta_automation','nova_meta_scheduler')
        AND TG_OP IN ('INSERT','UPDATE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Stream metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
DO $guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_stream_sources','audit_stream_source_commits',
            'config_stream_definitions','audit_stream_claims','audit_stream_namespace_operations',
            'config_streams','audit_stream_consumption_operations','audit_stream_consumptions'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.stream_write_guard()',
            tbl||'_write_guard', tbl);
    END LOOP;
END
$guards$;

-- A commit receipt as the journal writes it: the verified outcome, a reason,
-- and nonnegative integer engine identities (never arbitrary engine columns).
CREATE FUNCTION nova_meta.stream_receipt_valid(receipt jsonb, outcomes text[]) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $body$
DECLARE field text;
BEGIN
    IF receipt IS NULL OR jsonb_typeof(receipt)<>'object'
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(receipt) k
                  WHERE k NOT IN ('outcome','reason','load_id','sink_rows','transaction_id'))
       OR coalesce(receipt->>'outcome','')<>ALL(outcomes)
       OR jsonb_typeof(receipt->'reason') IS DISTINCT FROM 'string' THEN
        RETURN false;
    END IF;
    FOREACH field IN ARRAY ARRAY['load_id','sink_rows','transaction_id'] LOOP
        IF jsonb_typeof(receipt->field) IS NULL OR jsonb_typeof(receipt->field)='null' THEN
            CONTINUE;
        END IF;
        IF jsonb_typeof(receipt->field)<>'number' OR (receipt->>field) !~ '^[0-9]+$' THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.stream_receipt_valid(jsonb,text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.stream_receipt_valid(jsonb,text[])
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_notebook,nova_meta_automation,nova_meta_scheduler;

-- Row rules for runtime writes. Imported rows bypass them on insert. Each
-- table has its own branch: a record field that a table lacks never appears
-- in an expression evaluated for that table.
CREATE FUNCTION nova_meta.stream_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE
    hex64 constant text:='^[0-9a-f]{64}$';
    principal constant text:=current_setting('nova.principal',true);
    prior record;
    operation record;
    digest text;
    invalid_receipt boolean;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND TG_TABLE_NAME<>'config_stream_sources') THEN
        RAISE EXCEPTION 'Stream facts are immutable' USING ERRCODE='23514';
    END IF;

    IF TG_TABLE_NAME='audit_stream_claims' THEN
        IF NEW.resource_id !~ hex64 OR NEW.attempt_id !~ hex64 OR NEW.operation_digest !~ hex64
           OR NEW.epoch<0 OR NEW.generation<0
           OR NEW.claim_key IS DISTINCT FROM encode(sha256(convert_to(
                NEW.resource_id||':'||NEW.epoch||':'||NEW.generation,'UTF8')),'hex') THEN
            RAISE EXCEPTION 'Invalid stream claim' USING ERRCODE='23514';
        END IF;
        -- The first claim of a key wins; a later claimant reads the winner.
        IF EXISTS (SELECT 1 FROM nova_meta.audit_stream_claims WHERE claim_key=NEW.claim_key) THEN
            RETURN NULL;
        END IF;
        NEW.created_at:=clock_timestamp(); NEW.recorded_by:=principal;

    ELSIF TG_TABLE_NAME='audit_stream_namespace_operations' THEN
        IF NEW.operation_digest !~ hex64 OR NEW.stream_id !~ hex64
           OR NEW.catalog_name<>'default_catalog' OR NEW.schema_name<>'default'
           OR btrim(NEW.database_name)='' OR btrim(NEW.name)='' OR btrim(NEW.source_database)=''
           OR btrim(NEW.source_name)='' OR btrim(NEW.source_id)='' OR btrim(NEW.owner_role)=''
           OR NEW.generation<0 OR NEW.cursor_epoch<0 OR NEW.cursor_sequence<0
           OR NEW.status NOT IN ('READY','DROPPED','STALE') THEN
            RAISE EXCEPTION 'Invalid stream namespace operation' USING ERRCODE='23514';
        END IF;
        SELECT * INTO prior FROM nova_meta.audit_stream_namespace_operations
            WHERE operation_digest=NEW.operation_digest;
        IF FOUND THEN
            IF ROW(prior.catalog_name,prior.database_name,prior.schema_name,prior.name,
                   prior.generation,prior.stream_id,prior.source_database,prior.source_name,
                   prior.source_id,prior.owner_role,prior.cursor_epoch,prior.cursor_sequence,
                   prior.status)
               IS NOT DISTINCT FROM ROW(NEW.catalog_name,NEW.database_name,NEW.schema_name,
                   NEW.name,NEW.generation,NEW.stream_id,NEW.source_database,NEW.source_name,
                   NEW.source_id,NEW.owner_role,NEW.cursor_epoch,NEW.cursor_sequence,NEW.status)
            THEN RETURN NULL; END IF;
            RAISE EXCEPTION 'A different namespace operation has this identity' USING ERRCODE='23505';
        END IF;
        NEW.created_at:=clock_timestamp(); NEW.recorded_by:=principal;

    ELSIF TG_TABLE_NAME='config_streams' THEN
        IF NEW.stream_id !~ hex64
           OR NEW.catalog_name<>'default_catalog' OR NEW.schema_name<>'default'
           OR btrim(NEW.database_name)='' OR btrim(NEW.name)='' OR btrim(NEW.source_database)=''
           OR btrim(NEW.source_name)='' OR btrim(NEW.source_id)='' OR btrim(NEW.owner_role)=''
           OR NEW.generation<0 OR NEW.cursor_epoch<0 OR NEW.cursor_sequence<0
           OR NEW.status NOT IN ('READY','DROPPED','STALE') THEN
            RAISE EXCEPTION 'Invalid stream generation' USING ERRCODE='23514';
        END IF;
        SELECT * INTO prior FROM nova_meta.config_streams
            WHERE catalog_name=NEW.catalog_name AND database_name=NEW.database_name
              AND schema_name=NEW.schema_name AND name=NEW.name AND generation=NEW.generation;
        IF FOUND THEN
            IF ROW(prior.stream_id,prior.source_database,prior.source_name,prior.source_id,
                   prior.owner_role,prior.cursor_epoch,prior.cursor_sequence,prior.status)
               IS NOT DISTINCT FROM ROW(NEW.stream_id,NEW.source_database,NEW.source_name,
                   NEW.source_id,NEW.owner_role,NEW.cursor_epoch,NEW.cursor_sequence,NEW.status)
            THEN RETURN NULL; END IF;
            RAISE EXCEPTION 'This stream generation is already published' USING ERRCODE='23505';
        END IF;
        IF NEW.generation<>coalesce((SELECT max(generation) FROM nova_meta.config_streams
                WHERE catalog_name=NEW.catalog_name AND database_name=NEW.database_name
                  AND schema_name=NEW.schema_name AND name=NEW.name),-1)+1 THEN
            RAISE EXCEPTION 'Stream generations are published in order' USING ERRCODE='23514';
        END IF;
        -- Only the namespace claim winner publishes, and only the facts it
        -- prepared: the operation digest binds every published value.
        SELECT o.operation_digest INTO digest FROM nova_meta.audit_stream_namespace_operations o
            WHERE o.catalog_name=NEW.catalog_name AND o.database_name=NEW.database_name
              AND o.schema_name=NEW.schema_name AND o.name=NEW.name
              AND o.generation=NEW.generation AND o.stream_id=NEW.stream_id
              AND o.source_database=NEW.source_database AND o.source_name=NEW.source_name
              AND o.source_id=NEW.source_id AND o.owner_role=NEW.owner_role
              AND o.cursor_epoch=NEW.cursor_epoch AND o.cursor_sequence=NEW.cursor_sequence
              AND o.status=NEW.status
            ORDER BY o.operation_digest LIMIT 1;
        IF digest IS NULL OR NOT EXISTS (SELECT 1 FROM nova_meta.audit_stream_claims c
                WHERE c.operation_digest=digest AND c.epoch=0 AND c.generation=NEW.generation) THEN
            RAISE EXCEPTION 'Only the namespace claim winner can publish' USING ERRCODE='23514';
        END IF;
        NEW.created_at:=clock_timestamp();

    ELSIF TG_TABLE_NAME='config_stream_definitions' THEN
        IF NEW.stream_id !~ hex64 OR NEW.mode NOT IN ('APPEND_ONLY','STANDARD')
           OR btrim(NEW.source_id)=''
           OR (NEW.initial_commit_seq IS NOT NULL
               AND (NEW.show_initial_rows IS NOT TRUE OR NEW.initial_commit_seq<1)) THEN
            RAISE EXCEPTION 'Invalid stream definition' USING ERRCODE='23514';
        END IF;
        NEW.created_by:=principal;
        SELECT * INTO prior FROM nova_meta.config_stream_definitions WHERE stream_id=NEW.stream_id;
        IF FOUND THEN
            IF ROW(prior.mode,prior.source_id,prior.created_by,prior.show_initial_rows,
                   prior.initial_commit_seq)
               IS NOT DISTINCT FROM ROW(NEW.mode,NEW.source_id,NEW.created_by,
                   NEW.show_initial_rows,NEW.initial_commit_seq)
            THEN RETURN NULL; END IF;
            RAISE EXCEPTION 'A different definition has this stream id' USING ERRCODE='23505';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM nova_meta.config_stream_sources WHERE source_id=NEW.source_id)
           OR (NEW.initial_commit_seq IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM nova_meta.audit_stream_source_commits
                WHERE source_id=NEW.source_id AND commit_seq=NEW.initial_commit_seq)) THEN
            RAISE EXCEPTION 'A stream definition names a recorded source and snapshot'
                USING ERRCODE='23514';
        END IF;
        NEW.created_at:=clock_timestamp();

    ELSIF TG_TABLE_NAME='config_stream_sources' THEN
        IF NEW.status NOT IN ('REGISTERING','READY','BLOCKED','SOURCE_INVALID','DROPPED') THEN
            RAISE EXCEPTION 'Invalid stream source status' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.source_id !~ '^[0-9a-f]{32}$' OR NEW.epoch<1 OR NEW.table_id<0
               OR btrim(NEW.database_name)='' OR btrim(NEW.table_name)=''
               OR jsonb_array_length(NEW.key_columns)=0 OR jsonb_array_length(NEW.columns_json)=0
               OR NEW.schema_fingerprint !~ hex64
               OR NEW.journal_table<>'__nova_stream_changes_'||left(NEW.source_id,16)
               OR NEW.lineage_table<>'__nova_stream_lineage_'||left(NEW.source_id,16)
               OR NEW.status<>'REGISTERING' THEN
                RAISE EXCEPTION 'Invalid stream source registration' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.status_reason:=NULL; NEW.registered_by:=principal;
            NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSE
            IF ROW(NEW.source_id,NEW.epoch,NEW.database_name,NEW.table_name,NEW.table_id,
                   NEW.key_columns,NEW.columns_json,NEW.schema_fingerprint,NEW.journal_table,
                   NEW.lineage_table,NEW.registered_by,NEW.created_at,NEW.provenance)
               IS DISTINCT FROM ROW(OLD.source_id,OLD.epoch,OLD.database_name,OLD.table_name,
                   OLD.table_id,OLD.key_columns,OLD.columns_json,OLD.schema_fingerprint,
                   OLD.journal_table,OLD.lineage_table,OLD.registered_by,OLD.created_at,
                   OLD.provenance)
               OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
                RAISE EXCEPTION 'Invalid stream source change' USING ERRCODE='23514';
            END IF;
            -- Status moves forward: a dropped source is final, READY needs a
            -- registration with its baseline commit, BLOCKED comes from READY.
            IF OLD.status='DROPPED' OR NEW.status='REGISTERING'
               OR (NEW.status='READY' AND (OLD.status<>'REGISTERING' OR NOT EXISTS (
                    SELECT 1 FROM nova_meta.audit_stream_source_commits
                    WHERE source_id=NEW.source_id AND commit_seq=0)))
               OR (NEW.status='BLOCKED' AND OLD.status NOT IN ('READY','BLOCKED')) THEN
                RAISE EXCEPTION 'Invalid stream source transition' USING ERRCODE='23514';
            END IF;
            IF NEW.status='READY' THEN NEW.status_reason:=NULL; END IF;
            NEW.updated_at:=clock_timestamp();
        END IF;

    ELSIF TG_TABLE_NAME='audit_stream_source_commits' THEN
        IF NEW.commit_seq<0 OR NEW.version_before<0 OR NEW.row_count<0
           OR NEW.version_after NOT IN (NEW.version_before,NEW.version_before+1)
           OR btrim(NEW.principal)=''
           OR (NEW.commit_seq=0 AND (NEW.commit_id<>'baseline'
               OR NEW.version_after<>NEW.version_before OR NEW.row_count<>0))
           OR (NEW.commit_seq>0 AND NEW.commit_id !~ '^[0-9a-f]{32}$') THEN
            RAISE EXCEPTION 'Invalid stream source commit' USING ERRCODE='23514';
        END IF;
        SELECT * INTO prior FROM nova_meta.audit_stream_source_commits
            WHERE source_id=NEW.source_id AND commit_seq=NEW.commit_seq;
        IF FOUND THEN
            IF ROW(prior.commit_id,prior.version_before,prior.version_after,prior.row_count,
                   prior.principal,prior.active_role)
               IS NOT DISTINCT FROM ROW(NEW.commit_id,NEW.version_before,NEW.version_after,
                   NEW.row_count,NEW.principal,NEW.active_role)
            THEN RETURN NULL; END IF;
            RAISE EXCEPTION 'The capture commit index conflicts' USING ERRCODE='23505';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM nova_meta.config_stream_sources WHERE source_id=NEW.source_id)
        THEN RAISE EXCEPTION 'Unknown stream source' USING ERRCODE='23514'; END IF;
        -- An unbroken commit chain: each commit starts at the version the
        -- previous one ended at.
        IF NEW.commit_seq>0 AND NOT EXISTS (SELECT 1 FROM nova_meta.audit_stream_source_commits
                WHERE source_id=NEW.source_id AND commit_seq=NEW.commit_seq-1
                  AND version_after=NEW.version_before) THEN
            RAISE EXCEPTION 'The capture commit chain is broken' USING ERRCODE='23514';
        END IF;
        NEW.recorded_at:=clock_timestamp(); NEW.recorded_by:=principal;

    ELSIF TG_TABLE_NAME='audit_stream_consumption_operations' THEN
        IF NEW.consume_id !~ hex64 OR NEW.stream_id !~ hex64 OR NEW.operation_digest !~ hex64
           OR NEW.submission_digest !~ hex64 OR NEW.cancellation_digest !~ hex64
           OR NEW.submission_digest=NEW.cancellation_digest
           OR NEW.generation<0 OR NEW.epoch<0 OR NEW.sequence_from<0
           OR NEW.sequence_to<=NEW.sequence_from OR NEW.schema_version<1
           OR NEW.dispatch_attempt IS NULL OR NEW.dispatch_attempt<0
           OR btrim(NEW.target_database)='' OR btrim(NEW.target_object)=''
           OR btrim(NEW.engine_label)='' OR btrim(NEW.principal)=''
           OR btrim(NEW.active_role)='' OR NEW.security_context_version<1
           OR NEW.target_load_id<0 OR NEW.target_transaction_id<0 THEN
            RAISE EXCEPTION 'Invalid consumption operation' USING ERRCODE='23514';
        END IF;
        SELECT * INTO prior FROM nova_meta.audit_stream_consumption_operations
            WHERE consume_id=NEW.consume_id;
        IF FOUND THEN
            IF ROW(prior.stream_id,prior.generation,prior.source_id,prior.epoch,
                   prior.sequence_from,prior.sequence_to,prior.schema_version,
                   prior.operation_digest,prior.target_database,prior.target_object,
                   prior.engine_label,prior.principal,prior.target_load_id,
                   prior.target_transaction_id,prior.active_role,prior.security_context_version,
                   prior.dispatch_attempt,prior.submission_digest,prior.cancellation_digest)
               IS NOT DISTINCT FROM ROW(NEW.stream_id,NEW.generation,NEW.source_id,NEW.epoch,
                   NEW.sequence_from,NEW.sequence_to,NEW.schema_version,NEW.operation_digest,
                   NEW.target_database,NEW.target_object,NEW.engine_label,NEW.principal,
                   NEW.target_load_id,NEW.target_transaction_id,NEW.active_role,
                   NEW.security_context_version,NEW.dispatch_attempt,NEW.submission_digest,
                   NEW.cancellation_digest)
            THEN RETURN NULL; END IF;
            RAISE EXCEPTION 'A different consumption operation has this identity'
                USING ERRCODE='23505';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM nova_meta.config_streams
                WHERE stream_id=NEW.stream_id AND generation=NEW.generation
                  AND source_id=NEW.source_id AND cursor_epoch=NEW.epoch AND status='READY') THEN
            RAISE EXCEPTION 'A consumption names a published stream generation'
                USING ERRCODE='23514';
        END IF;
        NEW.created_at:=clock_timestamp(); NEW.recorded_by:=principal;

    ELSIF TG_TABLE_NAME='audit_stream_consumptions' THEN
        IF NEW.event_id !~ hex64 OR NEW.consume_id !~ hex64
           OR NEW.state NOT IN ('PREPARED','TARGET_SUBMITTED','TARGET_COMMITTED',
                                'OFFSET_COMMITTED','FAILED','CANCELLED','VERIFICATION_REQUIRED')
           OR NEW.event_id IS DISTINCT FROM encode(sha256(convert_to(
                '["'||NEW.consume_id||'","'||NEW.state||'"]','UTF8')),'hex') THEN
            RAISE EXCEPTION 'Invalid consumption event' USING ERRCODE='23514';
        END IF;
        -- (IF reads its condition only up to the first THEN, so the CASE is
        -- evaluated into a variable first.)
        invalid_receipt:=CASE
            WHEN NEW.state IN ('TARGET_COMMITTED','OFFSET_COMMITTED') THEN
                NOT nova_meta.stream_receipt_valid(NEW.receipt,ARRAY['COMMITTED','VISIBLE'])
            WHEN NEW.state='FAILED' THEN
                NOT nova_meta.stream_receipt_valid(NEW.receipt,ARRAY['FAILED'])
            WHEN NEW.state='VERIFICATION_REQUIRED' THEN
                NEW.receipt IS NOT NULL AND NOT nova_meta.stream_receipt_valid(NEW.receipt,
                    ARRAY['COMMITTED','VISIBLE','FAILED','VERIFICATION_REQUIRED'])
            ELSE NEW.receipt IS NOT NULL END;
        IF invalid_receipt THEN
            RAISE EXCEPTION 'Verified receipt evidence is required' USING ERRCODE='23514';
        END IF;
        SELECT * INTO prior FROM nova_meta.audit_stream_consumptions WHERE event_id=NEW.event_id;
        IF FOUND THEN
            -- A repeated transition is idempotent; a terminal receipt must be
            -- exactly the one already recorded.
            IF ROW(prior.consume_id,prior.stream_id,prior.epoch,prior.generation,
                   prior.sequence_from,prior.sequence_to,prior.operation_digest,
                   prior.target_database,prior.target_object,prior.engine_label,prior.principal,
                   prior.state)
               IS NOT DISTINCT FROM ROW(NEW.consume_id,NEW.stream_id,NEW.epoch,NEW.generation,
                   NEW.sequence_from,NEW.sequence_to,NEW.operation_digest,NEW.target_database,
                   NEW.target_object,NEW.engine_label,NEW.principal,NEW.state)
               AND (NEW.state NOT IN ('TARGET_COMMITTED','OFFSET_COMMITTED','FAILED')
                    OR prior.receipt IS NOT DISTINCT FROM NEW.receipt)
            THEN RETURN NULL; END IF;
            RAISE EXCEPTION 'A different consumption event has this identity' USING ERRCODE='23505';
        END IF;
        SELECT * INTO operation FROM nova_meta.audit_stream_consumption_operations
            WHERE consume_id=NEW.consume_id;
        IF NOT FOUND OR ROW(operation.stream_id,operation.epoch,operation.generation,
                operation.sequence_from,operation.sequence_to,operation.operation_digest,
                operation.target_database,operation.target_object,operation.engine_label,
                operation.principal)
            IS DISTINCT FROM ROW(NEW.stream_id,NEW.epoch,NEW.generation,NEW.sequence_from,
                NEW.sequence_to,NEW.operation_digest,NEW.target_database,NEW.target_object,
                NEW.engine_label,NEW.principal) THEN
            RAISE EXCEPTION 'A consumption event repeats its prepared operation exactly'
                USING ERRCODE='23514';
        END IF;
        -- Only the dispatch claim winner moves past preparation: submit for
        -- execution and its receipts, cancel for a cancellation.
        IF (NEW.state IN ('TARGET_SUBMITTED','TARGET_COMMITTED','OFFSET_COMMITTED','FAILED')
            AND NOT EXISTS (SELECT 1 FROM nova_meta.audit_stream_claims
                WHERE operation_digest=operation.submission_digest))
           OR (NEW.state='CANCELLED' AND NOT EXISTS (SELECT 1 FROM nova_meta.audit_stream_claims
                WHERE operation_digest=operation.cancellation_digest)) THEN
            RAISE EXCEPTION 'Only the dispatch claim winner records this state'
                USING ERRCODE='23514';
        END IF;
        IF NEW.state IN ('TARGET_COMMITTED','OFFSET_COMMITTED','FAILED') THEN
            IF EXISTS (SELECT 1 FROM nova_meta.audit_stream_consumptions
                    WHERE consume_id=NEW.consume_id
                      AND state IN ('TARGET_COMMITTED','OFFSET_COMMITTED','FAILED')
                      AND receipt IS DISTINCT FROM NEW.receipt) THEN
                RAISE EXCEPTION 'Conflicting terminal receipts' USING ERRCODE='23505';
            END IF;
        END IF;
        IF NEW.state='OFFSET_COMMITTED' THEN
            -- One writer at a time per stream, so two receipts cannot both
            -- miss each other.
            PERFORM pg_advisory_xact_lock(hashtextextended('nova.stream.offset:'||NEW.stream_id,0));
            IF NOT EXISTS (SELECT 1 FROM nova_meta.audit_stream_consumptions
                    WHERE consume_id=NEW.consume_id AND state='TARGET_COMMITTED') THEN
                RAISE EXCEPTION 'An offset receipt follows its target commit'
                    USING ERRCODE='23514';
            END IF;
            IF EXISTS (SELECT 1 FROM nova_meta.audit_stream_consumptions
                    WHERE stream_id=NEW.stream_id AND epoch=NEW.epoch
                      AND state='OFFSET_COMMITTED' AND consume_id<>NEW.consume_id
                      AND (sequence_from=NEW.sequence_from
                           OR (sequence_from<NEW.sequence_to AND NEW.sequence_from<sequence_to))) THEN
                RAISE EXCEPTION 'Offset receipts overlap' USING ERRCODE='23505';
            END IF;
            -- The chain starts at the stream's creation cursor and each
            -- receipt starts where an earlier one ended.
            IF NOT EXISTS (SELECT 1 FROM nova_meta.config_streams
                    WHERE stream_id=NEW.stream_id AND cursor_epoch=NEW.epoch
                      AND cursor_sequence=NEW.sequence_from)
               AND NOT EXISTS (SELECT 1 FROM nova_meta.audit_stream_consumptions
                    WHERE stream_id=NEW.stream_id AND epoch=NEW.epoch
                      AND state='OFFSET_COMMITTED' AND sequence_to=NEW.sequence_from) THEN
                RAISE EXCEPTION 'Offset receipts must form one unbroken chain'
                    USING ERRCODE='23514';
            END IF;
        END IF;
        NEW.created_at:=clock_timestamp(); NEW.recorded_by:=principal;
    END IF;
    IF TG_OP='INSERT' THEN NEW.provenance:='runtime'; END IF;
    RETURN NEW;
END
$body$;
DO $rows$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_stream_sources','audit_stream_source_commits',
            'config_stream_definitions','audit_stream_claims','audit_stream_namespace_operations',
            'config_streams','audit_stream_consumption_operations','audit_stream_consumptions'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.stream_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$rows$;

-- Publishing a stream generation (create, replace, drop, stale) and adding a
-- stream definition are governed definition changes and are audited. Claims,
-- operation facts, receipts, the commit index and source status are
-- execution evidence, as in the source, and write no success audit.
CREATE FUNCTION nova_meta.audit_stream_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity text; object_key jsonb; revision bigint; verb text;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_proxy','nova_meta_agent',
            'nova_meta_task','nova_meta_notebook','nova_meta_automation','nova_meta_scheduler')
       OR NOT nova_meta.actor_context_valid() OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_OP<>'INSERT'
       OR TG_TABLE_NAME NOT IN ('config_streams','config_stream_definitions') THEN
        RAISE EXCEPTION 'Invalid stream audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='config_streams' THEN
        identity:=NEW.database_name||'.'||NEW.name;
        object_key:=jsonb_build_object('catalog_name',NEW.catalog_name,
            'database_name',NEW.database_name,'schema_name',NEW.schema_name,'name',NEW.name,
            'generation',NEW.generation);
        revision:=NEW.generation+1;
        verb:=CASE NEW.status WHEN 'READY' THEN 'CREATE' WHEN 'DROPPED' THEN 'DELETE'
            ELSE 'UPDATE' END;
    ELSE
        identity:=NEW.stream_id;
        object_key:=jsonb_build_object('stream_id',NEW.stream_id);
        revision:=1;
        verb:='CREATE';
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,identity,verb,
        'SUCCESS','ALLOW',TG_TABLE_NAME,object_key,revision);
    RETURN NULL;
END
$body$;
CREATE TRIGGER config_streams_audit AFTER INSERT ON nova_meta.config_streams
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_stream_change();
CREATE TRIGGER config_stream_definitions_audit AFTER INSERT ON nova_meta.config_stream_definitions
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_stream_change();
