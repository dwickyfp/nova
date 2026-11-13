-- ML engine control metadata: training jobs, the model registry (models,
-- versions, aliases), durable ephemeral run descriptors and run telemetry.
-- Model and result bytes stay with the artifact store; these rows only name
-- them. The source `_PK` helper tables are import inputs reconciled into the
-- canonical tables, not tables of their own.
-- Rows carry provenance: imported source rows are kept as they were, while
-- identity, lifecycle and immutability rules apply to rows written at runtime.
CREATE TABLE nova_meta.config_ml_jobs (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    actor varchar(128) NOT NULL CHECK (octet_length(actor)<=128),
    session_fingerprint varchar(64) NOT NULL CHECK (octet_length(session_fingerprint)<=64),
    active_role varchar(128) CHECK (octet_length(active_role)<=128),
    security_context_version integer NOT NULL DEFAULT 1,
    tenant varchar(128) NOT NULL DEFAULT 'default' CHECK (octet_length(tenant)<=128),
    request_json text NOT NULL CHECK (char_length(request_json)<=65533),
    -- Ciphertext from the existing encryption owner; never plaintext SQL.
    encrypted_sql text NOT NULL CHECK (char_length(encrypted_sql)<=65533),
    status varchar(16) NOT NULL CHECK (octet_length(status)<=16),
    cancel_requested boolean NOT NULL DEFAULT false,
    result_json text CHECK (char_length(result_json)<=65533),
    error_kind varchar(32) CHECK (octet_length(error_kind)<=32),
    error_message text CHECK (char_length(error_message)<=65533),
    created_at timestamptz NOT NULL,
    started_at timestamptz,
    heartbeat_at timestamptz,
    finished_at timestamptz,
    lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_ml_jobs_queue ON nova_meta.config_ml_jobs (status,created_at,id);

CREATE TABLE nova_meta.ml_models (
    model_id varchar(64) PRIMARY KEY CHECK (octet_length(model_id)<=64),
    model_type varchar(64) NOT NULL CHECK (octet_length(model_type)<=64),
    model_name varchar(256) NOT NULL CHECK (octet_length(model_name)<=256),
    target_column varchar(128) CHECK (octet_length(target_column)<=128),
    feature_columns text CHECK (char_length(feature_columns)<=65533),
    hyperparameters text CHECK (char_length(hyperparameters)<=65533),
    -- Redacted training SQL, as the source stores it.
    training_sql text CHECK (char_length(training_sql)<=65533),
    database_name varchar(128) CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) CHECK (octet_length(schema_name)<=128),
    created_at timestamptz NOT NULL,
    created_by varchar(128) CHECK (octet_length(created_by)<=128),
    current_version integer DEFAULT 0,
    tenant_name varchar(128) NOT NULL DEFAULT 'default' CHECK (octet_length(tenant_name)<=128),
    updated_at timestamptz NOT NULL,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- One runtime model per owner scope. The source compares a missing database or
-- schema as the empty string, so the key does too. Imported duplicates are kept.
CREATE UNIQUE INDEX ml_models_runtime_scope ON nova_meta.ml_models
    (tenant_name,created_by,(coalesce(database_name,'')),(coalesce(schema_name,'')),model_name)
    WHERE provenance='runtime';

CREATE TABLE nova_meta.ml_model_versions (
    model_id varchar(64) NOT NULL CHECK (octet_length(model_id)<=64),
    version integer NOT NULL,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    training_rows bigint,
    metrics text CHECK (char_length(metrics)<=65533),
    artifact_uri varchar(2048) CHECK (octet_length(artifact_uri)<=2048),
    artifact_sha256 varchar(64) CHECK (octet_length(artifact_sha256)<=64),
    artifact_size bigint,
    task varchar(64) CHECK (octet_length(task)<=64),
    framework varchar(128) CHECK (octet_length(framework)<=128),
    framework_version varchar(64) CHECK (octet_length(framework_version)<=64),
    algorithm varchar(128) CHECK (octet_length(algorithm)<=128),
    feature_schema text CHECK (char_length(feature_schema)<=65533),
    training_duration_ms bigint,
    reservation_token varchar(64) CHECK (octet_length(reservation_token)<=64),
    ready_at timestamptz,
    failed_at timestamptz,
    failure_reason text CHECK (char_length(failure_reason)<=65533),
    -- Legacy inline model payload, imported as found; runtime never writes it.
    model_binary text,
    created_at timestamptz NOT NULL,
    created_by varchar(128) CHECK (octet_length(created_by)<=128),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (model_id,version)
);

CREATE TABLE nova_meta.ml_model_aliases (
    alias_name varchar(128) NOT NULL CHECK (octet_length(alias_name)<=128),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    database_name varchar(128) NOT NULL CHECK (octet_length(database_name)<=128),
    model_id varchar(64) NOT NULL CHECK (octet_length(model_id)<=64),
    version integer NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (alias_name,owner_name,database_name)
);
CREATE INDEX ml_model_aliases_target ON nova_meta.ml_model_aliases (model_id,version);

CREATE TABLE nova_meta.ml_ephemeral_runs (
    run_id varchar(64) PRIMARY KEY CHECK (octet_length(run_id)<=64),
    scope_key varchar(2048) NOT NULL CHECK (octet_length(scope_key)<=2048),
    fingerprint varchar(64) NOT NULL CHECK (octet_length(fingerprint)<=64),
    descriptor_json text NOT NULL CHECK (char_length(descriptor_json)<=65533),
    expires_epoch double precision NOT NULL,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX ml_ephemeral_runs_scope ON nova_meta.ml_ephemeral_runs (scope_key,fingerprint);
CREATE INDEX ml_ephemeral_runs_expiry ON nova_meta.ml_ephemeral_runs (expires_epoch);

CREATE TABLE nova_meta.ml_runs (
    run_id varchar(64) PRIMARY KEY CHECK (octet_length(run_id)<=64),
    task varchar(64) NOT NULL CHECK (octet_length(task)<=64),
    mode varchar(32) NOT NULL CHECK (octet_length(mode)<=32),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    tenant_name varchar(128) NOT NULL CHECK (octet_length(tenant_name)<=128),
    database_name varchar(128) CHECK (octet_length(database_name)<=128),
    model_id varchar(64) CHECK (octet_length(model_id)<=64),
    model_version integer,
    artifact_uri varchar(2048) CHECK (octet_length(artifact_uri)<=2048),
    fingerprint varchar(64) CHECK (octet_length(fingerprint)<=64),
    telemetry text CHECK (char_length(telemetry)<=65533),
    error_class varchar(128) CHECK (octet_length(error_class)<=128),
    error_message text CHECK (char_length(error_message)<=65533),
    created_at timestamptz NOT NULL,
    expires_at timestamptz,
    updated_at timestamptz NOT NULL,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX ml_runs_owner ON nova_meta.ml_runs (owner_name,created_at DESC);

ALTER TABLE nova_meta.config_ml_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_ml_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_models FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_model_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_model_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_model_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_model_aliases FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_ephemeral_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_ephemeral_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ml_runs FORCE ROW LEVEL SECURITY;

-- ML metadata is control-plane state reached through the owning service, per
-- workload and through a bound actor context, never anonymously. Training
-- writes run as the person who asked for it (the ML worker re-validates that
-- person's live session); retention runs as the maintenance service principal.
CREATE FUNCTION nova_meta.ml_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;
CREATE FUNCTION nova_meta.ml_actor_is(who text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.ml_actor_bound() AND who IS NOT DISTINCT FROM current_setting('nova.principal',true)
$body$;
REVOKE ALL ON FUNCTION nova_meta.ml_actor_bound(),nova_meta.ml_actor_is(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.ml_actor_bound(),nova_meta.ml_actor_is(text)
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation,
       nova_meta_ml,nova_meta_maintenance;

-- Who really reaches each table today:
--   web        API router, feature store, Decision Lab, internal predict
--   query      SQL frontend (CREATE ML_MODEL, ML_PREDICT, forecasts, materialize)
--   proxy      relayed MySQL clients: bounded prediction only, never training
--   agent,     assistant `ml_execute` and Decision Lab tools (Studio, automations)
--   automation
--   ml         the dedicated training worker
--   maintenance ephemeral retention sweep (expired descriptors, abandoned reservations)
-- Scheduler, task, ingestion, notebook and outbox have no ML grant.
DO $policies$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_ml_jobs','web,query,ml','web,query','web,query,ml'),
        ('ml_models','web,query,proxy,agent,automation,ml,maintenance','web,query,agent,automation,ml',''),
        ('ml_model_versions','web,query,proxy,agent,automation,ml,maintenance','web,query,agent,automation,ml',''),
        ('ml_model_aliases','web,query,proxy,agent,automation','web','web'),
        ('ml_ephemeral_runs','web,query,proxy,agent,automation,ml,maintenance','web,query,proxy,agent,automation,ml','web,query,proxy,agent,automation,ml'),
        ('ml_runs','','web,query,proxy,agent,automation,ml','')
    ) AS t(tbl,readers,inserters,updaters) LOOP
        IF item.readers<>'' THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO %s USING (nova_meta.ml_actor_bound())',
                item.tbl||'_read', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
        END IF;
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO %s WITH CHECK (nova_meta.ml_actor_bound())',
            item.tbl||'_insert', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r));
        IF item.updaters<>'' THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO %s USING (nova_meta.ml_actor_bound()) WITH CHECK (nova_meta.ml_actor_bound())',
                item.tbl||'_update', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.updaters,',')) r));
        END IF;
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            item.tbl||'_import', item.tbl);
    END LOOP;
END
$policies$;

-- A model is changed only by its owner's training; a version moves forward by
-- its owner's training, or is abandoned by the retention sweep.
CREATE POLICY ml_models_update ON nova_meta.ml_models FOR UPDATE
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_automation,nova_meta_ml
    USING (nova_meta.ml_actor_is(created_by)) WITH CHECK (nova_meta.ml_actor_is(created_by));
CREATE POLICY ml_model_versions_update ON nova_meta.ml_model_versions FOR UPDATE
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_automation,nova_meta_ml
    USING (nova_meta.ml_actor_is(created_by)) WITH CHECK (nova_meta.ml_actor_is(created_by));
CREATE POLICY ml_model_versions_abandon ON nova_meta.ml_model_versions FOR UPDATE
    TO nova_meta_maintenance USING (nova_meta.ml_actor_bound() AND status='TRAINING')
    WITH CHECK (nova_meta.ml_actor_bound());
-- Deleting a model is its owner's governed action through the web API, after
-- its artifacts are gone. Aliases pointing at it go with it.
CREATE POLICY ml_models_delete ON nova_meta.ml_models FOR DELETE TO nova_meta_web
    USING (nova_meta.ml_actor_is(created_by));
CREATE POLICY ml_model_versions_delete ON nova_meta.ml_model_versions FOR DELETE TO nova_meta_web
    USING (nova_meta.ml_actor_bound() AND EXISTS (SELECT 1 FROM nova_meta.ml_models m
        WHERE m.model_id=ml_model_versions.model_id AND nova_meta.ml_actor_is(m.created_by)));
CREATE POLICY ml_model_aliases_delete ON nova_meta.ml_model_aliases FOR DELETE TO nova_meta_web
    USING (nova_meta.ml_actor_is(owner_name) OR (nova_meta.ml_actor_bound() AND EXISTS
        (SELECT 1 FROM nova_meta.ml_models m WHERE m.model_id=ml_model_aliases.model_id
            AND nova_meta.ml_actor_is(m.created_by))));
-- Ephemeral descriptors are removed by their caller; the sweep removes only
-- descriptors that have expired.
CREATE POLICY ml_ephemeral_runs_delete ON nova_meta.ml_ephemeral_runs FOR DELETE
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation,nova_meta_ml
    USING (nova_meta.ml_actor_bound());
CREATE POLICY ml_ephemeral_runs_expire ON nova_meta.ml_ephemeral_runs FOR DELETE
    TO nova_meta_maintenance
    USING (nova_meta.ml_actor_bound() AND expires_epoch<=extract(epoch FROM clock_timestamp()));
-- Run telemetry is visible to and advanced by the person it belongs to.
CREATE POLICY ml_runs_read ON nova_meta.ml_runs FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation,nova_meta_ml
    USING (nova_meta.ml_actor_is(owner_name));
CREATE POLICY ml_runs_update ON nova_meta.ml_runs FOR UPDATE
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation,nova_meta_ml
    USING (nova_meta.ml_actor_is(owner_name)) WITH CHECK (nova_meta.ml_actor_is(owner_name));
-- Finished jobs are pruned by the ML worker a day after they finish. An
-- interrupted job may have left a reservation or artifact behind, so it stays
-- until it is reconciled.
CREATE POLICY config_ml_jobs_prune ON nova_meta.config_ml_jobs FOR DELETE TO nova_meta_ml
    USING (nova_meta.ml_actor_bound() AND status IN ('succeeded','failed','cancelled')
        AND finished_at<clock_timestamp()-interval '1 day');

-- Jobs: the submitting workloads never read the encrypted SQL back.
GRANT SELECT (id,actor,session_fingerprint,active_role,security_context_version,tenant,
        request_json,status,cancel_requested,result_json,error_kind,error_message,created_at,
        started_at,heartbeat_at,finished_at,lease_generation,provenance)
    ON nova_meta.config_ml_jobs TO nova_meta_web,nova_meta_query;
GRANT SELECT,DELETE ON nova_meta.config_ml_jobs TO nova_meta_ml;
GRANT INSERT (id,actor,session_fingerprint,active_role,security_context_version,tenant,
        request_json,encrypted_sql,status)
    ON nova_meta.config_ml_jobs TO nova_meta_web,nova_meta_query;
GRANT UPDATE (status,cancel_requested,error_kind,error_message)
    ON nova_meta.config_ml_jobs TO nova_meta_web,nova_meta_query;
GRANT UPDATE (status,result_json,error_kind,error_message,heartbeat_at,lease_generation)
    ON nova_meta.config_ml_jobs TO nova_meta_ml;

GRANT SELECT ON nova_meta.ml_models,nova_meta.ml_model_versions
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation,
       nova_meta_ml,nova_meta_maintenance;
GRANT INSERT (model_id,model_type,model_name,target_column,feature_columns,hyperparameters,
        training_sql,database_name,schema_name,created_by,tenant_name),
    UPDATE (model_type,target_column,feature_columns,hyperparameters,training_sql,
        current_version,metadata_revision)
    ON nova_meta.ml_models
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_automation,nova_meta_ml;
GRANT INSERT (model_id,version,status,reservation_token,created_by),
    UPDATE (status,training_rows,metrics,artifact_uri,artifact_sha256,artifact_size,task,
        framework,framework_version,algorithm,feature_schema,training_duration_ms,failure_reason)
    ON nova_meta.ml_model_versions
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_automation,nova_meta_ml;
GRANT UPDATE (status,failure_reason) ON nova_meta.ml_model_versions TO nova_meta_maintenance;
GRANT DELETE ON nova_meta.ml_models,nova_meta.ml_model_versions,nova_meta.ml_model_aliases
    TO nova_meta_web;

GRANT SELECT ON nova_meta.ml_model_aliases
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation;
GRANT INSERT (alias_name,owner_name,database_name,model_id,version),
    UPDATE (model_id,version,metadata_revision)
    ON nova_meta.ml_model_aliases TO nova_meta_web;

GRANT SELECT ON nova_meta.ml_ephemeral_runs
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation,
       nova_meta_ml,nova_meta_maintenance;
GRANT INSERT (run_id,scope_key,fingerprint,descriptor_json,expires_epoch),
    UPDATE (fingerprint,descriptor_json,expires_epoch),DELETE
    ON nova_meta.ml_ephemeral_runs
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation,nova_meta_ml;
GRANT DELETE ON nova_meta.ml_ephemeral_runs TO nova_meta_maintenance;

GRANT SELECT,
    INSERT (run_id,task,mode,status,owner_name,tenant_name,database_name,model_id,model_version,
        artifact_uri,fingerprint,telemetry,error_class,error_message,expires_at),
    UPDATE (status,model_id,model_version,artifact_uri,fingerprint,telemetry,error_class,
        error_message,expires_at)
    ON nova_meta.ml_runs
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_automation,nova_meta_ml;

CREATE FUNCTION nova_meta.ml_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'ML metadata import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_query','nova_meta_proxy','nova_meta_agent',
            'nova_meta_automation','nova_meta_ml','nova_meta_maintenance')
        AND TG_OP IN ('INSERT','UPDATE','DELETE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'ML metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
DO $guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_ml_jobs','ml_models','ml_model_versions',
                               'ml_model_aliases','ml_ephemeral_runs','ml_runs'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.ml_write_guard()',
            tbl||'_write_guard', tbl);
    END LOOP;
END
$guards$;

-- Row rules for runtime writes. Imported rows bypass them on insert; a later
-- runtime change to an imported row must still be a valid transition. One
-- branch per table: a field a table lacks never appears in its expressions.
CREATE FUNCTION nova_meta.ml_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE principal text := current_setting('nova.principal',true);
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='config_ml_jobs' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.status NOT IN ('queued','running','succeeded','failed','cancelled','interrupted') THEN
            RAISE EXCEPTION 'Invalid ML job' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            -- A job names the person, role and security version that queued it.
            IF NEW.status<>'queued' OR NEW.actor IS DISTINCT FROM principal
               OR NEW.active_role IS DISTINCT FROM current_setting('nova.active_role',true)
               OR NEW.security_context_version::text
                  IS DISTINCT FROM current_setting('nova.security_context_version',true)
               OR btrim(NEW.session_fingerprint)='' OR btrim(NEW.encrypted_sql)=''
               OR NOT pg_input_is_valid(NEW.request_json,'jsonb')
               OR jsonb_typeof(NEW.request_json::jsonb)<>'object' THEN
                RAISE EXCEPTION 'Invalid ML job' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.lease_generation:=0; NEW.cancel_requested:=false;
            NEW.result_json:=NULL; NEW.error_kind:=NULL; NEW.error_message:=NULL;
            NEW.created_at:=clock_timestamp();
            NEW.started_at:=NULL; NEW.heartbeat_at:=NULL; NEW.finished_at:=NULL;
            RETURN NEW;
        END IF;
        IF ROW(NEW.id,NEW.actor,NEW.session_fingerprint,NEW.active_role,
               NEW.security_context_version,NEW.tenant,NEW.request_json,NEW.encrypted_sql,
               NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.id,OLD.actor,OLD.session_fingerprint,OLD.active_role,
               OLD.security_context_version,OLD.tenant,OLD.request_json,OLD.encrypted_sql,
               OLD.created_at,OLD.provenance)
           OR OLD.status IN ('succeeded','failed','cancelled','interrupted')
           OR NEW.lease_generation<OLD.lease_generation
           OR (OLD.cancel_requested AND NOT NEW.cancel_requested) THEN
            RAISE EXCEPTION 'Invalid ML job transition' USING ERRCODE='23514';
        END IF;
        IF current_user IN ('nova_meta_web','nova_meta_query') THEN
            -- The waiting caller may ask to cancel, or withdraw a job nobody claimed.
            IF NOT ((NEW.status=OLD.status AND NEW.cancel_requested AND NOT OLD.cancel_requested
                     AND ROW(NEW.error_kind,NEW.error_message)
                         IS NOT DISTINCT FROM ROW(OLD.error_kind,OLD.error_message))
                    OR (OLD.status='queued' AND NEW.status IN ('failed','cancelled')
                        AND NEW.cancel_requested=OLD.cancel_requested)) THEN
                RAISE EXCEPTION 'Invalid ML job transition' USING ERRCODE='23514';
            END IF;
        ELSIF OLD.status='queued' THEN
            -- A claim takes the next lease generation.
            IF NEW.status<>'running' OR OLD.cancel_requested
               OR NEW.lease_generation<>OLD.lease_generation+1 THEN
                RAISE EXCEPTION 'Invalid ML job claim' USING ERRCODE='23514';
            END IF;
            NEW.started_at:=clock_timestamp(); NEW.heartbeat_at:=NEW.started_at;
        ELSIF NEW.lease_generation<>OLD.lease_generation OR NEW.status='queued' THEN
            RAISE EXCEPTION 'Invalid ML job transition' USING ERRCODE='23514';
        END IF;
        IF NEW.status IN ('succeeded','failed','cancelled','interrupted') THEN
            NEW.finished_at:=clock_timestamp();
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME='ml_models' THEN
        IF TG_OP='DELETE' THEN
            IF EXISTS (SELECT 1 FROM nova_meta.ml_model_versions v WHERE v.model_id=OLD.model_id) THEN
                RAISE EXCEPTION 'A model with versions cannot be deleted' USING ERRCODE='23503';
            END IF;
            RETURN OLD;
        END IF;
        IF btrim(NEW.model_name)='' OR coalesce(NEW.current_version,0)<0
           OR NEW.model_type NOT IN ('classification','regression','forecast','anomaly_detection',
               'clustering','route_optimization','simulation','analysis') THEN
            RAISE EXCEPTION 'Invalid ML model' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.created_by IS DISTINCT FROM principal THEN
                RAISE EXCEPTION 'A model belongs to the person who trains it' USING ERRCODE='42501';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1; NEW.current_version:=0;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.model_id,NEW.model_name,NEW.database_name,NEW.schema_name,NEW.tenant_name,
               NEW.created_at,NEW.created_by,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.model_id,OLD.model_name,OLD.database_name,OLD.schema_name,
               OLD.tenant_name,OLD.created_at,OLD.created_by,OLD.provenance) THEN
            RAISE EXCEPTION 'Invalid ML model change' USING ERRCODE='23514';
        END IF;
        IF NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'A model change needs the next revision' USING ERRCODE='23514';
        END IF;
        IF coalesce(NEW.current_version,0)<coalesce(OLD.current_version,0) THEN
            RAISE EXCEPTION 'The current model version cannot move back' USING ERRCODE='23514';
        END IF;
        IF NEW.current_version IS DISTINCT FROM OLD.current_version AND NOT EXISTS
            (SELECT 1 FROM nova_meta.ml_model_versions v WHERE v.model_id=NEW.model_id
                AND v.version=NEW.current_version AND v.status='READY') THEN
            RAISE EXCEPTION 'The current version must be a published version' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
        RETURN NEW;
    ELSIF TG_TABLE_NAME='ml_model_versions' THEN
        IF TG_OP='DELETE' THEN
            IF EXISTS (SELECT 1 FROM nova_meta.ml_model_aliases a
                       WHERE a.model_id=OLD.model_id AND a.version=OLD.version) THEN
                RAISE EXCEPTION 'An aliased version cannot be deleted' USING ERRCODE='23503';
            END IF;
            RETURN OLD;
        END IF;
        IF NEW.status NOT IN ('TRAINING','READY','FAILED') THEN
            RAISE EXCEPTION 'Invalid ML model version' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            -- A version starts as a reservation of the owner's existing model.
            IF NEW.status<>'TRAINING' OR NEW.version<1 OR NEW.created_by IS DISTINCT FROM principal
               OR coalesce(btrim(NEW.reservation_token),'')=''
               OR NOT EXISTS (SELECT 1 FROM nova_meta.ml_models m WHERE m.model_id=NEW.model_id
                              AND m.created_by=NEW.created_by) THEN
                RAISE EXCEPTION 'Invalid ML model version reservation' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
            NEW.ready_at:=NULL; NEW.failed_at:=NULL; NEW.model_binary:=NULL;
            RETURN NEW;
        END IF;
        -- Published and failed versions are provenance: they never change.
        IF OLD.status<>'TRAINING' OR NEW.status='TRAINING'
           OR ROW(NEW.model_id,NEW.version,NEW.reservation_token,NEW.created_at,NEW.created_by,
                  NEW.model_binary,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.model_id,OLD.version,OLD.reservation_token,OLD.created_at,
                  OLD.created_by,OLD.model_binary,OLD.provenance) THEN
            RAISE EXCEPTION 'A published model version is immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.status='READY' THEN
            IF current_user='nova_meta_maintenance'
               OR coalesce(btrim(NEW.artifact_uri),'')=''
               OR coalesce(NEW.artifact_sha256,'') !~ '^[0-9a-f]{64}$'
               OR coalesce(NEW.artifact_size,-1)<0 OR NEW.failure_reason IS NOT NULL THEN
                RAISE EXCEPTION 'A published version needs its verified artifact' USING ERRCODE='23514';
            END IF;
            NEW.ready_at:=clock_timestamp(); NEW.failed_at:=NULL;
        ELSE
            IF coalesce(btrim(NEW.failure_reason),'')='' THEN
                RAISE EXCEPTION 'A failed version needs its reason' USING ERRCODE='23514';
            END IF;
            NEW.failed_at:=clock_timestamp(); NEW.ready_at:=NULL;
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME='ml_model_aliases' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF btrim(NEW.alias_name)='' OR NEW.owner_name IS DISTINCT FROM principal
           OR NOT EXISTS (SELECT 1 FROM nova_meta.ml_model_versions v
                          JOIN nova_meta.ml_models m ON m.model_id=v.model_id
                          WHERE v.model_id=NEW.model_id AND v.version=NEW.version
                            AND v.status='READY' AND m.created_by=NEW.owner_name) THEN
            RAISE EXCEPTION 'An alias names a published version of its owner''s model'
                USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.alias_name,NEW.owner_name,NEW.database_name,NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.alias_name,OLD.owner_name,OLD.database_name,OLD.created_at,
               OLD.provenance)
           OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid alias revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
        RETURN NEW;
    ELSIF TG_TABLE_NAME='ml_ephemeral_runs' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF btrim(NEW.run_id)='' OR btrim(NEW.scope_key)=''
           OR NEW.expires_epoch IN ('NaN','Infinity','-Infinity')
           OR NOT pg_input_is_valid(NEW.descriptor_json,'jsonb') THEN
            RAISE EXCEPTION 'Invalid ephemeral ML run' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime';
        ELSIF ROW(NEW.run_id,NEW.scope_key,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.run_id,OLD.scope_key,OLD.provenance) THEN
            RAISE EXCEPTION 'An ephemeral run keeps its scope' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    ELSE
        IF NEW.status NOT IN ('RUNNING','COMPLETE','FAILED','succeeded','failed','cancelled')
           OR btrim(NEW.task)='' OR btrim(NEW.mode)='' THEN
            RAISE EXCEPTION 'Invalid ML run' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.owner_name IS DISTINCT FROM principal THEN
                RAISE EXCEPTION 'A run belongs to the person who ran it' USING ERRCODE='42501';
            END IF;
            NEW.provenance:='runtime';
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        -- Only a running record moves, once, to its outcome.
        IF OLD.status<>'RUNNING'
           OR ROW(NEW.run_id,NEW.task,NEW.mode,NEW.owner_name,NEW.tenant_name,NEW.database_name,
                  NEW.created_at,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.run_id,OLD.task,OLD.mode,OLD.owner_name,OLD.tenant_name,
                  OLD.database_name,OLD.created_at,OLD.provenance) THEN
            RAISE EXCEPTION 'Invalid ML run transition' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
        RETURN NEW;
    END IF;
END
$body$;
DO $rows$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_ml_jobs','ml_models','ml_model_versions',
                               'ml_model_aliases','ml_ephemeral_runs','ml_runs'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.ml_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$rows$;

-- Governed changes write a success audit row in the same transaction: queuing
-- and cancelling training, model definitions and deletion, version outcomes and
-- aliases. Claims, heartbeats, ephemeral descriptors and run telemetry are
-- execution state, as in the source, and are not.
CREATE FUNCTION nova_meta.audit_ml_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity text; object_key jsonb; revision bigint; verb text;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_agent',
            'nova_meta_automation','nova_meta_ml','nova_meta_maintenance')
       OR NOT nova_meta.actor_context_valid() OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('config_ml_jobs','ml_models','ml_model_versions',
                                'ml_model_aliases') THEN
        RAISE EXCEPTION 'Invalid ML audit source' USING ERRCODE='42501';
    END IF;
    verb:=CASE TG_OP WHEN 'INSERT' THEN 'CREATE' WHEN 'DELETE' THEN 'DELETE' ELSE 'UPDATE' END;
    IF TG_TABLE_NAME='config_ml_jobs' THEN
        IF TG_OP='UPDATE' THEN
            IF NEW.cancel_requested AND NOT OLD.cancel_requested THEN verb:='CANCEL';
            ELSIF OLD.status='queued' AND NEW.status IN ('failed','cancelled')
                  AND session_user IN ('nova_meta_web','nova_meta_query') THEN verb:='WITHDRAW';
            ELSE RETURN NULL;  -- claims, heartbeats and outcomes are execution state
            END IF;
        END IF;
        identity:=NEW.id; object_key:=jsonb_build_object('id',NEW.id);
        revision:=NEW.lease_generation+1;
    ELSIF TG_TABLE_NAME='ml_models' THEN
        IF TG_OP='DELETE' THEN
            identity:=OLD.model_id; revision:=OLD.metadata_revision;
        ELSE
            identity:=NEW.model_id; revision:=NEW.metadata_revision;
        END IF;
        object_key:=jsonb_build_object('model_id',identity);
    ELSIF TG_TABLE_NAME='ml_model_versions' THEN
        IF TG_OP='DELETE' THEN
            identity:=OLD.model_id||'/'||OLD.version; revision:=OLD.version;
            object_key:=jsonb_build_object('model_id',OLD.model_id,'version',OLD.version);
        ELSE
            IF TG_OP='UPDATE' THEN
                verb:=CASE NEW.status WHEN 'READY' THEN 'PUBLISH' ELSE 'ABANDON' END;
            ELSE
                verb:='RESERVE';
            END IF;
            identity:=NEW.model_id||'/'||NEW.version; revision:=NEW.version;
            object_key:=jsonb_build_object('model_id',NEW.model_id,'version',NEW.version);
        END IF;
    ELSE
        IF TG_OP='DELETE' THEN
            identity:=OLD.alias_name; revision:=OLD.metadata_revision;
            object_key:=jsonb_build_object('alias_name',OLD.alias_name,'owner_name',OLD.owner_name,
                'database_name',OLD.database_name);
        ELSE
            identity:=NEW.alias_name; revision:=NEW.metadata_revision;
            object_key:=jsonb_build_object('alias_name',NEW.alias_name,'owner_name',NEW.owner_name,
                'database_name',NEW.database_name);
        END IF;
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,identity,verb,
        'SUCCESS','ALLOW',TG_TABLE_NAME,object_key,greatest(coalesce(revision,1),1));
    RETURN NULL;
END
$body$;
CREATE TRIGGER config_ml_jobs_audit AFTER INSERT OR UPDATE ON nova_meta.config_ml_jobs
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_ml_change();
CREATE TRIGGER ml_models_audit AFTER INSERT OR UPDATE OR DELETE ON nova_meta.ml_models
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_ml_change();
CREATE TRIGGER ml_model_versions_audit AFTER INSERT OR UPDATE OR DELETE ON nova_meta.ml_model_versions
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_ml_change();
CREATE TRIGGER ml_model_aliases_audit AFTER INSERT OR UPDATE OR DELETE ON nova_meta.ml_model_aliases
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_ml_change();
