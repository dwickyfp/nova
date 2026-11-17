-- Routine Load from Kafka through Nova connections (CREATE ROUTINE LOAD with
-- "kafka_connection"). The engine runs the job; Nova keeps who owns it and
-- which connection it reads, and pauses it when the owner role loses access.
--
-- catalog_connection_roles  the roles a security admin allowed to load from a
--                           Kafka connection; checked when a job is created or
--                           resumed and again by the reconciler.
-- routine_load_jobs         one row per job Nova created: owner principal,
--                           owner role and security-context version, the
--                           connection, the target table, and Nova's state.
--                           Rows stay after STOP as the job's history.
CREATE TABLE nova_meta.catalog_connection_roles (
    connection_name varchar(256) NOT NULL
        REFERENCES nova_meta.catalog_connections(name),
    role_name varchar(128) NOT NULL CHECK (btrim(role_name)<>'' AND role_name !~ '[[:cntrl:]]'),
    granted_by varchar(128) NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (connection_name, role_name)
);

CREATE TABLE nova_meta.routine_load_jobs (
    id uuid PRIMARY KEY,
    database_name varchar(256) NOT NULL
        CHECK (btrim(database_name)<>'' AND database_name !~ '[[:cntrl:]]'),
    job_name varchar(256) NOT NULL CHECK (btrim(job_name)<>'' AND job_name !~ '[[:cntrl:]]'),
    table_name varchar(256) NOT NULL CHECK (btrim(table_name)<>'' AND table_name !~ '[[:cntrl:]]'),
    connection_name varchar(256) NOT NULL CHECK (btrim(connection_name)<>''),
    topic varchar(512) NOT NULL CHECK (btrim(topic)<>''),
    data_format varchar(16) NOT NULL CHECK (data_format IN ('csv','json','avro')),
    owner_principal varchar(128) NOT NULL CHECK (btrim(owner_principal)<>''),
    owner_role varchar(128) NOT NULL CHECK (btrim(owner_role)<>''),
    security_context_version bigint NOT NULL CHECK (security_context_version>0),
    engine_job_id varchar(64) CHECK (engine_job_id ~ '^[0-9]{1,20}$'),
    state varchar(16) NOT NULL DEFAULT 'CREATING'
        CHECK (state IN ('CREATING','UNKNOWN','ACTIVE','PAUSED_BY_NOVA','STOPPED','FAILED')),
    pause_reason varchar(1024),
    paused_at timestamptz,
    last_checked_at timestamptz,
    last_check_error varchar(1024),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision>0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((state='PAUSED_BY_NOVA')=(pause_reason IS NOT NULL AND paused_at IS NOT NULL))
);
-- One live job per engine name; stopped and failed rows are history.
CREATE UNIQUE INDEX routine_load_jobs_live ON nova_meta.routine_load_jobs (database_name, job_name)
    WHERE state IN ('CREATING','UNKNOWN','ACTIVE','PAUSED_BY_NOVA');
CREATE INDEX routine_load_jobs_by_connection ON nova_meta.routine_load_jobs (connection_name)
    WHERE state IN ('CREATING','UNKNOWN','ACTIVE','PAUSED_BY_NOVA');

ALTER TABLE nova_meta.catalog_connection_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.catalog_connection_roles FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.routine_load_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.routine_load_jobs FORCE ROW LEVEL SECURITY;

-- ── Who sees and changes what ──────────────────────────────────────────────

-- Every signed-in person reads which jobs Nova manages and which role owns
-- them (the engine shows the jobs themselves to whoever may use the table), so
-- a statement on someone else's job is refused rather than passed through.
-- Only the owner role and administrators change a job. The reconciler acts as
-- the routine-load SERVICE actor on the maintenance login. Data access stays
-- the engine's and Ranger's.
CREATE FUNCTION nova_meta.routine_load_visible(owner text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.catalog_actor_bound()
       AND current_setting('nova.principal_type',true)='USER'
       AND (owner=current_setting('nova.active_role',true) OR nova_meta.actor_is_administrator())
$body$;
CREATE FUNCTION nova_meta.routine_load_reconciler() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.catalog_actor_bound()
       AND current_setting('nova.principal_type',true)='SERVICE'
       AND current_setting('nova.principal',true)='nova-routine-load'
$body$;
REVOKE ALL ON FUNCTION nova_meta.routine_load_visible(text),nova_meta.routine_load_reconciler()
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.routine_load_visible(text)
    TO nova_meta_web,nova_meta_query,nova_meta_proxy;
GRANT EXECUTE ON FUNCTION nova_meta.routine_load_reconciler() TO nova_meta_maintenance;

CREATE POLICY routine_load_jobs_read ON nova_meta.routine_load_jobs FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_proxy
    USING (nova_meta.catalog_actor_bound() AND current_setting('nova.principal_type',true)='USER');
CREATE POLICY routine_load_jobs_create ON nova_meta.routine_load_jobs FOR INSERT
    TO nova_meta_web,nova_meta_query,nova_meta_proxy
    WITH CHECK (nova_meta.routine_load_visible(owner_role)
        AND owner_role=current_setting('nova.active_role',true)
        AND owner_principal=current_setting('nova.principal',true));
CREATE POLICY routine_load_jobs_change ON nova_meta.routine_load_jobs FOR UPDATE
    TO nova_meta_web,nova_meta_query,nova_meta_proxy
    USING (nova_meta.routine_load_visible(owner_role))
    WITH CHECK (nova_meta.routine_load_visible(owner_role));
CREATE POLICY routine_load_jobs_reconciler_read ON nova_meta.routine_load_jobs FOR SELECT
    TO nova_meta_maintenance USING (nova_meta.routine_load_reconciler());
CREATE POLICY routine_load_jobs_reconciler_change ON nova_meta.routine_load_jobs FOR UPDATE
    TO nova_meta_maintenance
    USING (nova_meta.routine_load_reconciler()) WITH CHECK (nova_meta.routine_load_reconciler());

-- Every bound workload that plans or reconciles a job reads the grants; only a
-- security admin changes them.
CREATE POLICY catalog_connection_roles_read ON nova_meta.catalog_connection_roles FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance
    USING (nova_meta.catalog_actor_bound());
CREATE POLICY catalog_connection_roles_grant ON nova_meta.catalog_connection_roles FOR INSERT
    TO nova_meta_web,nova_meta_query,nova_meta_proxy
    WITH CHECK (nova_meta.catalog_actor_bound() AND nova_meta.catalog_actor_is_steward()
        AND current_setting('nova.principal_type',true)='USER');
CREATE POLICY catalog_connection_roles_revoke ON nova_meta.catalog_connection_roles FOR DELETE
    TO nova_meta_web,nova_meta_query,nova_meta_proxy
    USING (nova_meta.catalog_actor_bound() AND nova_meta.catalog_actor_is_steward()
        AND current_setting('nova.principal_type',true)='USER');
CREATE POLICY catalog_connection_roles_import ON nova_meta.catalog_connection_roles FOR ALL
    TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));
CREATE POLICY routine_load_jobs_import ON nova_meta.routine_load_jobs FOR ALL
    TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));

GRANT SELECT ON nova_meta.catalog_connection_roles
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT INSERT (connection_name,role_name), DELETE ON nova_meta.catalog_connection_roles
    TO nova_meta_web,nova_meta_query,nova_meta_proxy;
GRANT SELECT ON nova_meta.routine_load_jobs
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT INSERT (id,database_name,job_name,table_name,connection_name,topic,data_format,
        owner_principal,owner_role,security_context_version)
    ON nova_meta.routine_load_jobs TO nova_meta_web,nova_meta_query,nova_meta_proxy;
GRANT UPDATE (engine_job_id,state,pause_reason,paused_at,security_context_version,
        last_checked_at,last_check_error,revision)
    ON nova_meta.routine_load_jobs TO nova_meta_web,nova_meta_query,nova_meta_proxy;
GRANT UPDATE (engine_job_id,state,pause_reason,paused_at,last_checked_at,last_check_error,
        revision)
    ON nova_meta.routine_load_jobs TO nova_meta_maintenance;

-- ── Row rules ──────────────────────────────────────────────────────────────
-- Identity never changes. A job starts CREATING (intent recorded before the
-- engine call) and moves only along its lifecycle; every change names the
-- revision it read; times come from the database clock.
CREATE FUNCTION nova_meta.routine_load_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='catalog_connection_roles' THEN
        IF TG_OP='INSERT' THEN
            NEW.granted_by:=current_setting('nova.principal',true);
            NEW.granted_at:=clock_timestamp();
            RETURN NEW;
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'CREATING' THEN
            RAISE EXCEPTION 'A routine load starts as CREATING' USING ERRCODE='23514';
        END IF;
        NEW.engine_job_id:=NULL; NEW.pause_reason:=NULL; NEW.paused_at:=NULL;
        NEW.last_checked_at:=NULL; NEW.last_check_error:=NULL; NEW.revision:=1;
        NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.database_name,NEW.job_name,NEW.table_name,NEW.connection_name,NEW.topic,
           NEW.data_format,NEW.owner_principal,NEW.owner_role,NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id,OLD.database_name,OLD.job_name,OLD.table_name,
           OLD.connection_name,OLD.topic,OLD.data_format,OLD.owner_principal,OLD.owner_role,
           OLD.created_at) THEN
        RAISE EXCEPTION 'A routine load''s identity cannot change' USING ERRCODE='23514';
    END IF;
    IF OLD.engine_job_id IS NOT NULL AND NEW.engine_job_id IS DISTINCT FROM OLD.engine_job_id THEN
        RAISE EXCEPTION 'A routine load keeps its engine job' USING ERRCODE='23514';
    END IF;
    IF NEW.revision<>OLD.revision+1 THEN
        RAISE EXCEPTION 'A routine load change needs the revision it read' USING ERRCODE='40001';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
           (OLD.state='CREATING' AND NEW.state IN ('ACTIVE','UNKNOWN','FAILED'))
        OR (OLD.state='UNKNOWN' AND NEW.state IN ('ACTIVE','FAILED'))
        OR (OLD.state='ACTIVE' AND NEW.state IN ('PAUSED_BY_NOVA','STOPPED'))
        OR (OLD.state='PAUSED_BY_NOVA' AND NEW.state IN ('ACTIVE','STOPPED'))) THEN
        RAISE EXCEPTION 'Invalid routine load transition' USING ERRCODE='23514';
    END IF;
    IF NEW.state='PAUSED_BY_NOVA' THEN
        IF OLD.state<>'PAUSED_BY_NOVA' THEN NEW.paused_at:=clock_timestamp();
        ELSE NEW.paused_at:=OLD.paused_at; END IF;
    ELSE
        NEW.pause_reason:=NULL; NEW.paused_at:=NULL;
    END IF;
    IF NEW.security_context_version IS DISTINCT FROM OLD.security_context_version
       AND NOT (NEW.state='ACTIVE' AND current_setting('nova.principal_type',true)='USER'
                AND NEW.owner_principal=current_setting('nova.principal',true)
                AND NEW.owner_role=current_setting('nova.active_role',true)) THEN
        RAISE EXCEPTION 'Only the owner renews a routine load''s security context'
            USING ERRCODE='42501';
    END IF;
    IF NEW.last_checked_at IS DISTINCT FROM OLD.last_checked_at THEN
        NEW.last_checked_at:=clock_timestamp();
    END IF;
    NEW.updated_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER routine_load_jobs_rows BEFORE INSERT OR UPDATE ON nova_meta.routine_load_jobs
    FOR EACH ROW EXECUTE FUNCTION nova_meta.routine_load_row_guard();
CREATE TRIGGER catalog_connection_roles_rows BEFORE INSERT ON nova_meta.catalog_connection_roles
    FOR EACH ROW EXECUTE FUNCTION nova_meta.routine_load_row_guard();
-- The same statement guard as the other catalog tables: a workload login and an
-- active epoch, or the migrator during maintenance.
CREATE TRIGGER routine_load_jobs_write_guard
    BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.routine_load_jobs
    FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.catalog_write_guard();
CREATE TRIGGER catalog_connection_roles_write_guard
    BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.catalog_connection_roles
    FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.catalog_write_guard();
REVOKE ALL ON FUNCTION nova_meta.routine_load_row_guard() FROM PUBLIC;

-- Connections may now be Iceberg REST catalogs and Kafka clusters. The rest of
-- the catalog row guard is unchanged from 0019.
CREATE OR REPLACE FUNCTION nova_meta.catalog_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE principal text := current_setting('nova.principal',true);
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' AND TG_TABLE_NAME<>'config_external_catalogs' THEN
            NEW.provenance:='imported';
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME='catalog_catalogs' THEN
        IF btrim(NEW.name)='' OR NEW.name ~ '[[:cntrl:]]'
           OR NEW.kind NOT IN ('native','external','foreign')
           OR NEW.source NOT IN ('nova','engine')
           OR coalesce(NEW.status,'ok') NOT IN ('ok','error')
           OR btrim(NEW.catalog_type)=''
           OR NOT nova_meta.catalog_properties_safe(NEW.properties_json) THEN
            RAISE EXCEPTION 'Invalid catalog' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1; NEW.sync_generation:=0;
            NEW.created_by:=principal; NEW.created_at:=clock_timestamp();
            NEW.updated_at:=NEW.created_at; NEW.dropped_at:=NULL;
            NEW.status:=NULL; NEW.last_sync_at:=NULL; NEW.last_sync_error:=NULL;
        ELSIF ROW(NEW.name,NEW.created_by,NEW.created_at,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.name,OLD.created_by,OLD.created_at,OLD.provenance)
              OR NEW.sync_generation NOT IN (OLD.sync_generation,OLD.sync_generation+1) THEN
            RAISE EXCEPTION 'Invalid catalog change' USING ERRCODE='23514';
        ELSIF NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision THEN
            -- A definition change advances the revision by exactly one.
            IF NEW.metadata_revision<>OLD.metadata_revision+1 THEN
                RAISE EXCEPTION 'Invalid catalog revision' USING ERRCODE='23514';
            END IF;
            IF NEW.dropped_at IS NOT NULL AND OLD.dropped_at IS NULL THEN
                NEW.dropped_at:=clock_timestamp();
            ELSIF NEW.dropped_at IS NOT NULL THEN
                NEW.dropped_at:=OLD.dropped_at;
            END IF;
            NEW.updated_at:=clock_timestamp();
        ELSIF ROW(NEW.kind,NEW.catalog_type,NEW.managed,NEW.source,NEW.metastore_type,
                  NEW.metastore_uri,NEW.storage_connection,NEW.comment,NEW.properties_json,
                  NEW.dropped_at)
              IS DISTINCT FROM ROW(OLD.kind,OLD.catalog_type,OLD.managed,OLD.source,
                  OLD.metastore_type,OLD.metastore_uri,OLD.storage_connection,OLD.comment,
                  OLD.properties_json,OLD.dropped_at) THEN
            RAISE EXCEPTION 'A catalog definition change needs a new revision' USING ERRCODE='23514';
        ELSIF ROW(NEW.status,NEW.last_sync_at,NEW.last_sync_error)
              IS DISTINCT FROM ROW(OLD.status,OLD.last_sync_at,OLD.last_sync_error) THEN
            -- Sync bookkeeping, recorded by the holder of the current generation.
            IF NEW.sync_generation<>OLD.sync_generation
               OR current_setting('nova.catalog_sync_catalog',true) IS DISTINCT FROM OLD.name
               OR current_setting('nova.catalog_sync_generation',true)
                  IS DISTINCT FROM OLD.sync_generation::text THEN
                RAISE EXCEPTION 'Catalog sync generation is stale' USING ERRCODE='40001';
            END IF;
            NEW.last_sync_at:=CASE WHEN NEW.status='ok' THEN clock_timestamp() ELSE OLD.last_sync_at END;
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='catalog_objects' THEN
        IF TG_OP='INSERT' THEN
            PERFORM nova_meta.catalog_sync_fenced(NEW.catalog_name,false);
            IF NEW.id IS DISTINCT FROM (CASE NEW.object_type WHEN 'database'
                    THEN nova_meta.catalog_row_id(NEW.catalog_name,NEW.database_name,'','database')
                    ELSE nova_meta.catalog_row_id(NEW.catalog_name,NEW.database_name,
                        NEW.object_name,'relation') END)
               OR NEW.object_type NOT IN ('database','table','view','materialized_view')
               OR (NEW.object_type='database')<>(NEW.object_name='') THEN
                RAISE EXCEPTION 'Invalid catalog object' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.first_seen_at:=clock_timestamp();
            NEW.last_seen_at:=NEW.first_seen_at; NEW.dropped_at:=NULL;
        ELSIF ROW(NEW.id,NEW.catalog_name,NEW.database_name,NEW.object_name,NEW.provenance,
                  NEW.first_seen_at,NEW.last_seen_at)
              IS DISTINCT FROM ROW(OLD.id,OLD.catalog_name,OLD.database_name,OLD.object_name,
                  OLD.provenance,OLD.first_seen_at,OLD.last_seen_at)
              OR NEW.object_type NOT IN ('database','table','view','materialized_view')
              OR (NEW.object_type='database')<>(OLD.object_type='database') THEN
            RAISE EXCEPTION 'Invalid catalog object change' USING ERRCODE='23514';
        ELSIF NEW.dropped_at IS NOT NULL THEN
            -- A tombstone changes nothing but the drop time.
            IF ROW(NEW.object_type,NEW.engine,NEW.comment,NEW.row_count,NEW.data_size,NEW.fingerprint)
               IS DISTINCT FROM ROW(OLD.object_type,OLD.engine,OLD.comment,OLD.row_count,
                   OLD.data_size,OLD.fingerprint) THEN
                RAISE EXCEPTION 'A dropped catalog object is unchanged' USING ERRCODE='23514';
            END IF;
            PERFORM nova_meta.catalog_sync_fenced(NEW.catalog_name,true);
            NEW.dropped_at:=coalesce(OLD.dropped_at,clock_timestamp());
        ELSE
            -- Seen by the current harvest; a returning object is first seen again.
            PERFORM nova_meta.catalog_sync_fenced(NEW.catalog_name,false);
            IF OLD.dropped_at IS NOT NULL THEN NEW.first_seen_at:=clock_timestamp(); END IF;
            NEW.last_seen_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='catalog_columns' THEN
        IF TG_OP='DELETE' THEN
            PERFORM nova_meta.catalog_sync_fenced(OLD.catalog_name,true);
            RETURN OLD;
        END IF;
        PERFORM nova_meta.catalog_sync_fenced(NEW.catalog_name,false);
        IF TG_OP='INSERT' THEN
            IF NEW.id IS DISTINCT FROM nova_meta.catalog_row_id(NEW.catalog_name,NEW.database_name,
                    NEW.object_name,NEW.ordinal::text)
               OR btrim(NEW.column_name)='' OR NEW.object_name='' THEN
                RAISE EXCEPTION 'Invalid catalog column' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime';
        ELSIF ROW(NEW.id,NEW.catalog_name,NEW.database_name,NEW.object_name,NEW.ordinal,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.id,OLD.catalog_name,OLD.database_name,OLD.object_name,
                  OLD.ordinal,OLD.provenance)
              OR btrim(NEW.column_name)='' THEN
            RAISE EXCEPTION 'Invalid catalog column change' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='catalog_sync_runs' THEN
        IF NEW.trigger NOT IN ('schedule','manual','create','alter','lineage')
           OR NEW.status NOT IN ('running','ok','error')
           OR NOT (NEW.counts_json IS NULL OR (pg_input_is_valid(NEW.counts_json,'jsonb')
                   AND jsonb_typeof(NEW.counts_json::jsonb)='object')) THEN
            RAISE EXCEPTION 'Invalid sync run' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'running' OR btrim(NEW.catalog_name)=''
               OR (NEW.trigger='lineage')<>(NEW.catalog_name='lineage:audit')
               OR (NEW.trigger='lineage')<>(NEW.sync_generation IS NULL) THEN
                RAISE EXCEPTION 'A sync run starts running' USING ERRCODE='23514';
            END IF;
            IF NEW.sync_generation IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM nova_meta.catalog_catalogs
                WHERE name=NEW.catalog_name AND sync_generation=NEW.sync_generation) THEN
                RAISE EXCEPTION 'Catalog sync generation is stale' USING ERRCODE='40001';
            END IF;
            NEW.provenance:='runtime'; NEW.started_at:=clock_timestamp();
            NEW.finished_at:=NULL; NEW.counts_json:=NULL; NEW.error:=NULL;
        ELSIF ROW(NEW.id,NEW.catalog_name,NEW.trigger,NEW.started_at,NEW.sync_generation,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.id,OLD.catalog_name,OLD.trigger,OLD.started_at,
                  OLD.sync_generation,OLD.provenance)
              OR OLD.status<>'running' OR NEW.status NOT IN ('ok','error') THEN
            -- Created once, finished once: a recorded outcome is never rewritten.
            RAISE EXCEPTION 'Invalid sync run transition' USING ERRCODE='23514';
        ELSE
            NEW.finished_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='catalog_connections' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.name !~ '^[A-Za-z_][A-Za-z0-9_]*$'
           OR NEW.connector_type NOT IN ('starrocks','postgres','snowflake','iceberg','kafka')
           OR btrim(NEW.host)='' OR NEW.host ~ '[[:space:][:cntrl:]]'
           OR NOT coalesce(NEW.port BETWEEN 1 AND 65535,true)
           OR NOT nova_meta.catalog_properties_safe(NEW.options_json)
           OR NOT (NEW.secret_ref='nova://'||NEW.name
                OR NEW.secret_ref ~ '^(aws|aws-sm|secretsmanager|aws_secrets_manager)://[[:alnum:]_/+=.@:-]+$'
                OR NEW.secret_ref ~ '^arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[[:alnum:]_/+=.@-]+$') THEN
            RAISE EXCEPTION 'Invalid catalog connection' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1; NEW.owner:=principal;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.name,NEW.owner,NEW.created_at,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.name,OLD.owner,OLD.created_at,OLD.provenance)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid catalog connection revision' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME IN ('catalog_connection_credentials','catalog_connection_write_credentials') THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        -- Ciphertext of app.common.crypto.encrypt (an "enc:" Fernet token);
        -- anything else could be a plaintext secret and is refused.
        IF NEW.username_enc !~ '^enc:gAAAAA[A-Za-z0-9_-]+=*$'
           OR NEW.password_enc !~ '^enc:gAAAAA[A-Za-z0-9_-]+=*$'
           OR NOT EXISTS (SELECT 1 FROM nova_meta.catalog_connections WHERE name=NEW.connection_name) THEN
            RAISE EXCEPTION 'Invalid connection credential' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
        ELSIF ROW(NEW.connection_name,NEW.provenance) IS DISTINCT FROM ROW(OLD.connection_name,OLD.provenance)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid connection credential revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_by:=principal; NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='catalog_securables' THEN
        IF NEW.securable_type NOT IN ('catalog','database','table','view','materialized_view','column') THEN
            RAISE EXCEPTION 'Invalid securable' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            PERFORM nova_meta.catalog_sync_fenced(NEW.catalog_name,false);
            IF (NEW.securable_type='catalog' AND (NEW.id IS DISTINCT FROM
                    nova_meta.catalog_row_id(NEW.catalog_name,'','','catalog')
                    OR ROW(NEW.database_name,NEW.object_name,NEW.column_name)<>ROW('','','')
                    OR NEW.parent_id IS NOT NULL))
               OR (NEW.securable_type='database' AND (NEW.id IS DISTINCT FROM
                    nova_meta.catalog_row_id(NEW.catalog_name,NEW.database_name,'','database')
                    OR NEW.database_name='' OR ROW(NEW.object_name,NEW.column_name)<>ROW('','')
                    OR NEW.parent_id IS DISTINCT FROM nova_meta.catalog_row_id(NEW.catalog_name,'','','catalog')))
               OR (NEW.securable_type IN ('table','view','materialized_view') AND (NEW.id IS DISTINCT FROM
                    nova_meta.catalog_row_id(NEW.catalog_name,NEW.database_name,NEW.object_name,'relation')
                    OR NEW.database_name='' OR NEW.object_name='' OR NEW.column_name<>''
                    OR NEW.parent_id IS DISTINCT FROM
                       nova_meta.catalog_row_id(NEW.catalog_name,NEW.database_name,'','database')))
               OR (NEW.securable_type='column' AND (NEW.database_name='' OR NEW.object_name=''
                    OR NEW.column_name=''
                    OR NEW.parent_id IS DISTINCT FROM nova_meta.catalog_row_id(NEW.catalog_name,
                       NEW.database_name,NEW.object_name,'relation'))) THEN
                RAISE EXCEPTION 'Invalid securable identity' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.first_seen_at:=clock_timestamp();
            NEW.updated_at:=NEW.first_seen_at; NEW.dropped_at:=NULL;
        ELSIF ROW(NEW.id,NEW.catalog_name,NEW.database_name,NEW.object_name,NEW.parent_id,
                  NEW.provenance,NEW.first_seen_at)
              IS DISTINCT FROM ROW(OLD.id,OLD.catalog_name,OLD.database_name,OLD.object_name,
                  OLD.parent_id,OLD.provenance,OLD.first_seen_at)
              OR ((NEW.securable_type IN ('table','view','materialized_view'))
                  <> (OLD.securable_type IN ('table','view','materialized_view')))
              OR (NEW.securable_type NOT IN ('table','view','materialized_view')
                  AND NEW.securable_type<>OLD.securable_type)
              -- A column id is case-insensitive: only its spelling may change.
              OR (NEW.securable_type='column' AND lower(NEW.column_name)<>lower(OLD.column_name))
              OR (NEW.securable_type<>'column' AND NEW.column_name<>OLD.column_name) THEN
            RAISE EXCEPTION 'Invalid securable change' USING ERRCODE='23514';
        ELSIF NEW.dropped_at IS NOT NULL THEN
            IF ROW(NEW.securable_type,NEW.column_name,NEW.data_type,NEW.ordinal)
               IS DISTINCT FROM ROW(OLD.securable_type,OLD.column_name,OLD.data_type,OLD.ordinal) THEN
                RAISE EXCEPTION 'A dropped securable is unchanged' USING ERRCODE='23514';
            END IF;
            PERFORM nova_meta.catalog_sync_fenced(NEW.catalog_name,true);
            IF OLD.dropped_at IS NULL THEN
                NEW.dropped_at:=clock_timestamp(); NEW.updated_at:=NEW.dropped_at;
            ELSE
                NEW.dropped_at:=OLD.dropped_at;
            END IF;
        ELSE
            PERFORM nova_meta.catalog_sync_fenced(NEW.catalog_name,false);
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='catalog_annotations' THEN
        IF NEW.securable_type NOT IN ('catalog','database','table','view','materialized_view','column')
           OR (NEW.owner_name IS NULL)<>(NEW.owner_type IS NULL)
           OR NEW.owner_type NOT IN ('user','role')
           OR NEW.owner_name !~ '^[A-Za-z_][A-Za-z0-9_.@-]{0,127}$'
           OR NEW.classification NOT IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')
           OR (NEW.classification IS NOT NULL AND NEW.securable_type<>'column')
           OR NEW.comment ~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]' THEN
            RAISE EXCEPTION 'Invalid annotation' USING ERRCODE='23514';
        END IF;
        -- Stewardship: owners are governance assignments, and catalogs and
        -- databases are edited by security admins only.
        IF NOT nova_meta.catalog_actor_is_steward() AND (
               NEW.securable_type IN ('catalog','database')
               OR (TG_OP='INSERT' AND NEW.owner_name IS NOT NULL)
               OR (TG_OP='UPDATE' AND ROW(NEW.owner_name,NEW.owner_type)
                   IS DISTINCT FROM ROW(OLD.owner_name,OLD.owner_type))) THEN
            RAISE EXCEPTION 'Catalog stewardship requires a security-admin role' USING ERRCODE='42501';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
        ELSIF ROW(NEW.securable_id,NEW.securable_type,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.securable_id,OLD.securable_type,OLD.provenance)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid annotation revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_by:=principal; NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_object_tags' THEN
        IF TG_OP='DELETE' THEN
            IF OLD.securable_type IN ('catalog','database') AND NOT nova_meta.catalog_actor_is_steward() THEN
                RAISE EXCEPTION 'Catalog stewardship requires a security-admin role' USING ERRCODE='42501';
            END IF;
            RETURN OLD;
        END IF;
        IF NEW.securable_type NOT IN ('catalog','database','table','view','materialized_view','column')
           OR NEW.tag_key !~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'
           OR char_length(NEW.tag_value)>256
           OR NEW.tag_value ~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]' THEN
            RAISE EXCEPTION 'Invalid tag' USING ERRCODE='23514';
        END IF;
        IF NEW.securable_type IN ('catalog','database') AND NOT nova_meta.catalog_actor_is_steward() THEN
            RAISE EXCEPTION 'Catalog stewardship requires a security-admin role' USING ERRCODE='42501';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_by:=principal; NEW.created_at:=clock_timestamp();
        ELSIF ROW(NEW.securable_id,NEW.tag_key,NEW.securable_type,NEW.created_by,NEW.created_at,
                  NEW.provenance)
              IS DISTINCT FROM ROW(OLD.securable_id,OLD.tag_key,OLD.securable_type,OLD.created_by,
                  OLD.created_at,OLD.provenance)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid tag revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_by:=principal; NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='catalog_lineage' THEN
        IF NEW.statement_kind NOT IN ('insert','create_table_as_select','create_view',
                'create_materialized_view','merge')
           OR btrim(NEW.source_name)='' OR btrim(NEW.target_name)=''
           OR NEW.first_seen_at IS NULL OR NEW.last_seen_at IS NULL
           OR NEW.first_seen_at>NEW.last_seen_at THEN
            RAISE EXCEPTION 'Invalid lineage edge' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.id IS DISTINCT FROM nova_meta.catalog_row_id(NEW.source_id,NEW.target_id,'lineage') THEN
                RAISE EXCEPTION 'Invalid lineage edge' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime';
        ELSIF ROW(NEW.id,NEW.source_id,NEW.target_id,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.id,OLD.source_id,OLD.target_id,OLD.provenance) THEN
            RAISE EXCEPTION 'Invalid lineage edge change' USING ERRCODE='23514';
        ELSE
            -- Harvests may overlap or arrive out of order: the span only widens
            -- and the newest statement keeps its query id.
            IF OLD.last_seen_at IS NOT NULL AND NEW.last_seen_at<OLD.last_seen_at THEN
                NEW.last_seen_at:=OLD.last_seen_at; NEW.last_query_id:=OLD.last_query_id;
                NEW.source_name:=OLD.source_name; NEW.target_name:=OLD.target_name;
                NEW.statement_kind:=OLD.statement_kind;
            END IF;
            NEW.first_seen_at:=least(NEW.first_seen_at,OLD.first_seen_at);
        END IF;
    ELSIF TG_TABLE_NAME='catalog_securable_links' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.owner_type NOT IN ('semantic_view','entity') OR btrim(NEW.owner_id)=''
           OR NEW.relation_name !~ '^[^.]+\.[^.]+\.[^.]+$'
           OR NEW.securable_id IS DISTINCT FROM nova_meta.catalog_row_id(
                  split_part(NEW.relation_name,'.',1),split_part(NEW.relation_name,'.',2),
                  split_part(NEW.relation_name,'.',3),'relation') THEN
            RAISE EXCEPTION 'Invalid securable link' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.id IS DISTINCT FROM nova_meta.catalog_row_id(NEW.owner_type,NEW.owner_id,NEW.securable_id) THEN
                RAISE EXCEPTION 'Invalid securable link' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime';
        ELSIF ROW(NEW.id,NEW.owner_type,NEW.owner_id,NEW.securable_id,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.id,OLD.owner_type,OLD.owner_id,OLD.securable_id,OLD.provenance) THEN
            RAISE EXCEPTION 'Invalid securable link change' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    ELSE
        RAISE EXCEPTION 'Catalog metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$body$;
