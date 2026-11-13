CREATE TABLE nova_meta.config_stages (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    database_name varchar(128) NOT NULL CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) NOT NULL CHECK (octet_length(schema_name)<=128),
    storage_connection varchar(128) NOT NULL CHECK (octet_length(storage_connection)<=128),
    base_prefix varchar(512) NOT NULL CHECK (octet_length(base_prefix)<=512),
    created_at timestamptz DEFAULT clock_timestamp(),
    created_by varchar(128) DEFAULT nullif(current_setting('nova.principal',true),'')
        CHECK (octet_length(created_by)<=128),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    is_deleted boolean NOT NULL DEFAULT false,
    namespace_kind text NOT NULL CHECK (namespace_kind IN ('MANAGED','MAPPED','LEGACY'))
);
CREATE UNIQUE INDEX stage_live_name ON nova_meta.config_stages (database_name,schema_name,name)
    WHERE NOT is_deleted;
ALTER TABLE nova_meta.config_stages ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_stages FORCE ROW LEVEL SECURITY;

CREATE FUNCTION nova_meta.stage_scope_allows(database_name text,schema_name text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid()
       AND current_setting('nova.service_identity',true)=current_user
       AND current_setting('nova.stage_database',true)=database_name
       AND current_setting('nova.stage_schema',true)=schema_name
       AND current_setting('nova.stage_action',true) IN ('read','write','delete')
$body$;
GRANT EXECUTE ON FUNCTION nova_meta.stage_scope_allows(text,text)
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_ml,nova_meta_notebook,nova_meta_automation;
CREATE POLICY stage_read ON nova_meta.config_stages FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_ml,nova_meta_notebook,nova_meta_automation
    USING (nova_meta.stage_scope_allows(database_name,schema_name));
CREATE POLICY stage_insert ON nova_meta.config_stages FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.stage_scope_allows(database_name,schema_name)
        AND current_setting('nova.stage_action',true)='delete'
        AND (namespace_kind='MANAGED' OR (namespace_kind='MAPPED'
            AND current_setting('nova.active_role',true)='ACCOUNTADMIN')));
CREATE POLICY stage_update ON nova_meta.config_stages FOR UPDATE TO nova_meta_web
    USING (nova_meta.stage_scope_allows(database_name,schema_name)
        AND current_setting('nova.stage_action',true)='delete')
    WITH CHECK (nova_meta.stage_scope_allows(database_name,schema_name)
        AND current_setting('nova.stage_action',true)='delete');

-- The only runtime entry into the owner projection returns names and scope.
-- Payload access stays under the calling workload's scoped RLS policy.
CREATE POLICY stage_owner_read ON nova_meta.config_stages FOR SELECT TO nova_meta_owner
    USING (true);
CREATE POLICY stage_owner_insert ON nova_meta.config_stages FOR INSERT TO nova_meta_owner
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));
CREATE POLICY stage_owner_update ON nova_meta.config_stages FOR UPDATE TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));
CREATE POLICY stage_owner_delete ON nova_meta.config_stages FOR DELETE TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));

CREATE FUNCTION nova_meta.discover_stages(identity text,stage_name text,database_filter text,
    schema_filter text)
RETURNS TABLE(id varchar,name varchar,database_name varchar,schema_name varchar)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_proxy','nova_meta_agent',
        'nova_meta_task','nova_meta_ml','nova_meta_notebook','nova_meta_automation')
       OR nova_meta.actor_context_valid() IS DISTINCT FROM true
       OR current_setting('nova.service_identity',true) IS DISTINCT FROM session_user THEN
        RAISE EXCEPTION 'Stage discovery is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN QUERY SELECT s.id,s.name,s.database_name,s.schema_name FROM nova_meta.config_stages s
        WHERE NOT s.is_deleted AND (identity IS NULL OR s.id=identity)
            AND (stage_name IS NULL OR s.name=stage_name)
            AND (database_filter IS NULL OR s.database_name=database_filter)
            AND (schema_filter IS NULL OR s.schema_name=schema_filter)
        ORDER BY s.database_name,s.schema_name,s.name,s.id;
END
$body$;
GRANT EXECUTE ON FUNCTION nova_meta.discover_stages(text,text,text,text)
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_ml,nova_meta_notebook,nova_meta_automation;
GRANT SELECT ON nova_meta.config_stages
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_task,
       nova_meta_ml,nova_meta_notebook,nova_meta_automation;
GRANT INSERT (id,name,database_name,schema_name,storage_connection,base_prefix,namespace_kind),
    UPDATE (metadata_revision,is_deleted) ON nova_meta.config_stages TO nova_meta_web;

CREATE FUNCTION nova_meta.stage_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Stage import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user='nova_meta_web' AND TG_OP IN ('INSERT','UPDATE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Stage mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
CREATE TRIGGER stage_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_stages FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.stage_write_guard();

CREATE FUNCTION nova_meta.stage_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN RETURN NEW; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
           OR btrim(NEW.name)='' OR btrim(NEW.database_name)='' OR btrim(NEW.schema_name)=''
           OR btrim(NEW.storage_connection)='' OR NEW.metadata_revision<>1 OR NEW.is_deleted
           OR NEW.namespace_kind='LEGACY'
           OR (NEW.namespace_kind='MANAGED' AND NEW.base_prefix<>'nova/stages/'||NEW.id)
           OR (NEW.namespace_kind='MAPPED' AND (NEW.base_prefix=''
                OR NEW.base_prefix LIKE '/%' OR NEW.base_prefix LIKE '%/'
                OR position(chr(92) IN NEW.base_prefix)>0
                OR NEW.base_prefix ~ '(^|/)(\.|\.\.|)(/|$)')) THEN
            RAISE EXCEPTION 'Invalid stage registration' USING ERRCODE='23514';
        END IF;
        NEW.created_by:=current_setting('nova.principal',true);
        NEW.created_at:=clock_timestamp();
    ELSIF ROW(NEW.id,NEW.name,NEW.database_name,NEW.schema_name,NEW.storage_connection,
            NEW.base_prefix,NEW.created_at,NEW.created_by,NEW.namespace_kind)
        IS DISTINCT FROM ROW(OLD.id,OLD.name,OLD.database_name,OLD.schema_name,
            OLD.storage_connection,OLD.base_prefix,OLD.created_at,OLD.created_by,OLD.namespace_kind)
        OR NEW.metadata_revision<>OLD.metadata_revision+1 OR OLD.is_deleted OR NOT NEW.is_deleted
    THEN RAISE EXCEPTION 'Invalid stage revision' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER stage_revision_guard BEFORE INSERT OR UPDATE ON nova_meta.config_stages
    FOR EACH ROW EXECUTE FUNCTION nova_meta.stage_revision_guard();

CREATE OR REPLACE FUNCTION nova_meta.audit_metadata_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE object_key jsonb; object_identity text; revision bigint; deleted boolean;
BEGIN
    IF NOT pg_has_role(session_user,'nova_meta_app','MEMBER') OR EXISTS
        (SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper) THEN RETURN NULL; END IF;
    IF TG_TABLE_SCHEMA<>'nova_meta' OR TG_TABLE_NAME NOT IN
        ('config_user_preferences','config_ai_providers','config_ai_models','config_model_aliases',
         'config_stages')
       OR TG_OP NOT IN ('INSERT','UPDATE') OR NOT nova_meta.actor_context_valid() THEN
        RAISE EXCEPTION 'Invalid metadata audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='config_user_preferences' THEN
        object_key:=jsonb_build_object('user_name',NEW.user_name,'pref_key',NEW.pref_key);
        object_identity:=NEW.user_name||'/'||NEW.pref_key;
        revision:=NEW.revision;
    ELSE
        object_key:=jsonb_build_object('id',NEW.id);
        object_identity:=NEW.id;
        revision:=NEW.metadata_revision;
    END IF;
    deleted:=NEW.is_deleted;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,object_identity,
        CASE WHEN deleted THEN 'DELETE' WHEN TG_OP='INSERT' THEN 'CREATE' ELSE 'UPDATE' END,
        'SUCCESS','ALLOW',TG_TABLE_NAME,object_key,revision);
    RETURN NULL;
END
$body$;
CREATE TRIGGER stage_audit AFTER INSERT OR UPDATE ON nova_meta.config_stages
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_metadata_change();
