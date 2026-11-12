CREATE TABLE nova_meta.config_workspaces (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    name varchar(256) NOT NULL CHECK (octet_length(name)<=256),
    owner varchar(128) NOT NULL CHECK (octet_length(owner)<=128),
    created_at timestamptz DEFAULT clock_timestamp(),
    updated_at timestamptz DEFAULT clock_timestamp(),
    is_deleted boolean DEFAULT false,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0)
);
CREATE TABLE nova_meta.config_workspace_shares (
    workspace_id varchar(64) NOT NULL REFERENCES nova_meta.config_workspaces(id),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    permission varchar(16) NOT NULL CHECK (permission IN ('view','edit')),
    granted_by varchar(128) CHECK (octet_length(granted_by)<=128),
    granted_at timestamptz DEFAULT clock_timestamp(),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    is_deleted boolean NOT NULL DEFAULT false,
    PRIMARY KEY (workspace_id,role_name)
);
ALTER TABLE nova_meta.config_workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_workspace_shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_workspace_shares FORCE ROW LEVEL SECURITY;

-- These owner reads serve only the fixed permission function below. Runtime
-- logins cannot assume the owner role or read its unrestricted projection.
CREATE POLICY workspace_owner_read ON nova_meta.config_workspaces FOR SELECT
    TO nova_meta_owner USING (true);
CREATE POLICY workspace_share_owner_read ON nova_meta.config_workspace_shares FOR SELECT
    TO nova_meta_owner USING (true);

CREATE FUNCTION nova_meta.workspace_permission(identity text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE actor text; roles jsonb; workspace_owner text; result text;
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_agent','nova_meta_notebook')
       OR nova_meta.actor_context_valid() IS DISTINCT FROM true
       OR current_setting('nova.service_identity',true) IS DISTINCT FROM session_user THEN
        RETURN NULL;
    END IF;
    actor:=current_setting('nova.principal',true);
    roles:=nullif(current_setting('nova.workspace_roles',true),'')::jsonb;
    IF jsonb_typeof(roles) IS DISTINCT FROM 'array'
       OR NOT roles ? current_setting('nova.active_role',true) THEN RETURN NULL; END IF;
    SELECT w.owner INTO workspace_owner FROM nova_meta.config_workspaces w
        WHERE w.id=identity AND w.is_deleted=false;
    IF workspace_owner IS NULL THEN RETURN NULL; END IF;
    IF workspace_owner=actor THEN RETURN 'owner'; END IF;
    SELECT CASE WHEN bool_or(s.permission='edit') THEN 'edit' ELSE 'view' END INTO result
        FROM nova_meta.config_workspace_shares s
        WHERE s.workspace_id=identity AND NOT s.is_deleted AND roles ? s.role_name
        HAVING count(*)>0;
    RETURN result;
END
$body$;
GRANT EXECUTE ON FUNCTION nova_meta.workspace_permission(text)
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook;

CREATE POLICY workspace_read ON nova_meta.config_workspaces FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook
    USING (nova_meta.actor_context_valid()
        AND current_setting('nova.service_identity',true)=current_user
        AND (owner=current_setting('nova.principal',true)
            OR nova_meta.workspace_permission(id) IS NOT NULL));
CREATE POLICY workspace_insert ON nova_meta.config_workspaces FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.actor_context_valid()
        AND current_setting('nova.service_identity',true)=current_user
        AND owner=current_setting('nova.principal',true));
CREATE POLICY workspace_update ON nova_meta.config_workspaces FOR UPDATE TO nova_meta_web
    USING (nova_meta.workspace_permission(id)='owner')
    WITH CHECK (owner=current_setting('nova.principal',true));
CREATE POLICY workspace_share_read ON nova_meta.config_workspace_shares FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook
    USING (nova_meta.workspace_permission(workspace_id) IS NOT NULL);
CREATE POLICY workspace_share_insert ON nova_meta.config_workspace_shares FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.workspace_permission(workspace_id)='owner');
CREATE POLICY workspace_share_update ON nova_meta.config_workspace_shares FOR UPDATE
    TO nova_meta_web USING (nova_meta.workspace_permission(workspace_id)='owner')
    WITH CHECK (nova_meta.workspace_permission(workspace_id)='owner');

CREATE POLICY workspace_import ON nova_meta.config_workspaces FOR ALL TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));
CREATE POLICY workspace_share_import ON nova_meta.config_workspace_shares FOR ALL TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));
GRANT SELECT ON nova_meta.config_workspaces,nova_meta.config_workspace_shares
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook;
GRANT INSERT (id,name,owner),UPDATE (name,is_deleted,metadata_revision)
    ON nova_meta.config_workspaces TO nova_meta_web;
GRANT INSERT (workspace_id,role_name,permission),UPDATE (permission,is_deleted,metadata_revision)
    ON nova_meta.config_workspace_shares TO nova_meta_web;

CREATE FUNCTION nova_meta.workspace_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Workspace import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user='nova_meta_web' AND TG_OP IN ('INSERT','UPDATE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Workspace mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
CREATE TRIGGER workspace_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_workspaces FOR EACH STATEMENT
    EXECUTE FUNCTION nova_meta.workspace_write_guard();
CREATE TRIGGER workspace_share_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_workspace_shares FOR EACH STATEMENT
    EXECUTE FUNCTION nova_meta.workspace_write_guard();

CREATE FUNCTION nova_meta.workspace_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME='config_workspaces' THEN
        IF TG_OP='INSERT' THEN
            IF NEW.id !~ '^[0-9a-f]{32}$' OR btrim(NEW.name)='' OR char_length(NEW.name)>120
               OR position('/' IN NEW.name)>0 OR NEW.owner IS DISTINCT FROM
                    current_setting('nova.principal',true)
               OR NEW.metadata_revision<>1 OR NEW.is_deleted IS DISTINCT FROM false THEN
                RAISE EXCEPTION 'Invalid workspace' USING ERRCODE='23514';
            END IF;
            NEW.created_at:=clock_timestamp();
        ELSIF ROW(NEW.id,NEW.owner,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id,OLD.owner,OLD.created_at)
            OR NEW.metadata_revision<>OLD.metadata_revision+1 OR OLD.is_deleted
            OR NEW.is_deleted IS NULL OR btrim(NEW.name)='' OR char_length(NEW.name)>120
            OR position('/' IN NEW.name)>0 THEN
            RAISE EXCEPTION 'Invalid workspace revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    ELSE
        IF btrim(NEW.role_name)='' OR NEW.role_name<>btrim(NEW.role_name) THEN
            RAISE EXCEPTION 'Invalid workspace role' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.metadata_revision<>1 OR NEW.is_deleted THEN
                RAISE EXCEPTION 'Invalid workspace share' USING ERRCODE='23514';
            END IF;
        ELSIF ROW(NEW.workspace_id,NEW.role_name)
            IS DISTINCT FROM ROW(OLD.workspace_id,OLD.role_name)
            OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid workspace share revision' USING ERRCODE='23514';
        END IF;
        NEW.granted_by:=current_setting('nova.principal',true);
        NEW.granted_at:=clock_timestamp();
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER workspace_revision_guard BEFORE INSERT OR UPDATE ON nova_meta.config_workspaces
    FOR EACH ROW EXECUTE FUNCTION nova_meta.workspace_revision_guard();
CREATE TRIGGER workspace_share_revision_guard BEFORE INSERT OR UPDATE
    ON nova_meta.config_workspace_shares FOR EACH ROW
    EXECUTE FUNCTION nova_meta.workspace_revision_guard();

CREATE FUNCTION nova_meta.audit_workspace_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE object_key jsonb; object_identity text;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user<>'nova_meta_web' OR NOT nova_meta.actor_context_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_TABLE_NAME NOT IN
            ('config_workspaces','config_workspace_shares') OR TG_OP NOT IN ('INSERT','UPDATE') THEN
        RAISE EXCEPTION 'Invalid workspace audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='config_workspaces' THEN
        object_key:=jsonb_build_object('id',NEW.id); object_identity:=NEW.id;
    ELSE
        object_key:=jsonb_build_object('workspace_id',NEW.workspace_id,'role_name',NEW.role_name);
        object_identity:=NEW.workspace_id||'/'||NEW.role_name;
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,object_identity,
        CASE WHEN NEW.is_deleted THEN 'DELETE' WHEN TG_OP='INSERT' THEN 'CREATE' ELSE 'UPDATE' END,
        'SUCCESS','ALLOW',TG_TABLE_NAME,object_key,NEW.metadata_revision);
    RETURN NULL;
END
$body$;
CREATE TRIGGER workspace_audit AFTER INSERT OR UPDATE ON nova_meta.config_workspaces
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_workspace_change();
CREATE TRIGGER workspace_share_audit AFTER INSERT OR UPDATE ON nova_meta.config_workspace_shares
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_workspace_change();
