-- Workspace file entries and immutable file-version metadata. Object payloads
-- keep their existing storage owner; these rows carry reservations and receipts.
CREATE TABLE nova_meta.config_workspace_entries (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    parent_path varchar(1024) NOT NULL CHECK (octet_length(parent_path)<=1024),
    name varchar(256) NOT NULL CHECK (octet_length(name)<=256),
    entry_type varchar(32) NOT NULL CHECK (octet_length(entry_type)<=32),
    object_key varchar(1024) CHECK (octet_length(object_key)<=1024),
    size_bytes bigint DEFAULT 0,
    etag varchar(256) CHECK (octet_length(etag)<=256),
    created_at timestamptz DEFAULT clock_timestamp(),
    updated_at timestamptz DEFAULT clock_timestamp(),
    deleted_at timestamptz,
    is_deleted boolean DEFAULT false,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0)
);
-- One live entry per path keeps one object key per path. Import of an
-- unreconciled duplicate path stops here instead of discarding a source row.
CREATE UNIQUE INDEX config_workspace_entries_live_path
    ON nova_meta.config_workspace_entries (user_name,parent_path,name)
    WHERE is_deleted IS NOT TRUE;
CREATE INDEX config_workspace_entries_namespace
    ON nova_meta.config_workspace_entries (user_name,parent_path);

CREATE TABLE nova_meta.config_workspace_file_versions (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    entry_id varchar(64) NOT NULL REFERENCES nova_meta.config_workspace_entries(id),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    version bigint NOT NULL CHECK (version>0),
    object_key varchar(1024) NOT NULL CHECK (octet_length(object_key)<=1024),
    size_bytes bigint DEFAULT 0,
    etag varchar(256) CHECK (octet_length(etag)<=256),
    created_at timestamptz DEFAULT clock_timestamp(),
    state varchar(16) NOT NULL DEFAULT 'published'
        CHECK (state IN ('reserved','published','abandoned')),
    content_sha256 char(64) CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    is_deleted boolean NOT NULL DEFAULT false,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0)
);
-- Historical duplicate (entry, version) rows keep their opaque IDs. New
-- reservations are unique and always numbered above every retained row.
CREATE UNIQUE INDEX config_workspace_file_versions_runtime_number
    ON nova_meta.config_workspace_file_versions (entry_id,version) WHERE provenance='runtime';
CREATE INDEX config_workspace_file_versions_entry
    ON nova_meta.config_workspace_file_versions (entry_id,user_name,version DESC);

ALTER TABLE nova_meta.config_workspace_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_workspace_entries FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_workspace_file_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_workspace_file_versions FORCE ROW LEVEL SECURITY;

-- A personal namespace is the principal's name; a shared one is ~ws/<id>.
-- Usernames never contain '/', so the two forms cannot alias each other.
CREATE FUNCTION nova_meta.workspace_namespace_permission(namespace text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE roles jsonb;
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_agent','nova_meta_notebook')
       OR nova_meta.actor_context_valid() IS DISTINCT FROM true
       OR current_setting('nova.service_identity',true) IS DISTINCT FROM session_user
       OR namespace IS NULL THEN
        RETURN NULL;
    END IF;
    roles:=nullif(current_setting('nova.workspace_roles',true),'')::jsonb;
    IF jsonb_typeof(roles) IS DISTINCT FROM 'array'
       OR NOT roles ? current_setting('nova.active_role',true) THEN RETURN NULL; END IF;
    IF namespace=current_setting('nova.principal',true) AND position('/' IN namespace)=0 THEN
        RETURN 'owner';
    END IF;
    IF left(namespace,4)='~ws/' THEN
        RETURN nova_meta.workspace_permission(substr(namespace,5));
    END IF;
    RETURN NULL;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.workspace_namespace_permission(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.workspace_namespace_permission(text)
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook;

CREATE POLICY workspace_entry_read ON nova_meta.config_workspace_entries FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook
    USING (nova_meta.workspace_namespace_permission(user_name) IS NOT NULL);
CREATE POLICY workspace_entry_insert ON nova_meta.config_workspace_entries FOR INSERT
    TO nova_meta_web
    WITH CHECK (nova_meta.workspace_namespace_permission(user_name) IN ('owner','edit'));
CREATE POLICY workspace_entry_update ON nova_meta.config_workspace_entries FOR UPDATE
    TO nova_meta_web
    USING (nova_meta.workspace_namespace_permission(user_name) IN ('owner','edit'))
    WITH CHECK (nova_meta.workspace_namespace_permission(user_name) IN ('owner','edit'));
CREATE POLICY workspace_version_read ON nova_meta.config_workspace_file_versions FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook
    USING (nova_meta.workspace_namespace_permission(user_name) IS NOT NULL);
CREATE POLICY workspace_version_insert ON nova_meta.config_workspace_file_versions FOR INSERT
    TO nova_meta_web
    WITH CHECK (nova_meta.workspace_namespace_permission(user_name) IN ('owner','edit'));
CREATE POLICY workspace_version_update ON nova_meta.config_workspace_file_versions FOR UPDATE
    TO nova_meta_web
    USING (nova_meta.workspace_namespace_permission(user_name) IN ('owner','edit'))
    WITH CHECK (nova_meta.workspace_namespace_permission(user_name) IN ('owner','edit'));
CREATE POLICY workspace_entry_import ON nova_meta.config_workspace_entries FOR ALL
    TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));
CREATE POLICY workspace_version_import ON nova_meta.config_workspace_file_versions FOR ALL
    TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));

GRANT SELECT ON nova_meta.config_workspace_entries,nova_meta.config_workspace_file_versions
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook;
GRANT INSERT (id,user_name,parent_path,name,entry_type,object_key,size_bytes,etag),
    UPDATE (parent_path,name,object_key,size_bytes,etag,is_deleted,metadata_revision)
    ON nova_meta.config_workspace_entries TO nova_meta_web;
GRANT INSERT (id,entry_id,user_name,version,object_key),
    UPDATE (state,size_bytes,etag,content_sha256,is_deleted,metadata_revision)
    ON nova_meta.config_workspace_file_versions TO nova_meta_web;

CREATE TRIGGER workspace_entry_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_workspace_entries FOR EACH STATEMENT
    EXECUTE FUNCTION nova_meta.workspace_write_guard();
CREATE TRIGGER workspace_version_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_workspace_file_versions FOR EACH STATEMENT
    EXECUTE FUNCTION nova_meta.workspace_write_guard();

CREATE FUNCTION nova_meta.workspace_entry_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN RETURN NEW; END IF;
    IF btrim(NEW.name)='' OR NEW.name IN ('.','..') OR position('/' IN NEW.name)>0
       OR NEW.parent_path<>btrim(NEW.parent_path,'/') OR position('//' IN NEW.parent_path)>0
       OR NEW.entry_type NOT IN ('file','folder')
       OR (NEW.entry_type='folder' AND (NEW.object_key IS NOT NULL OR NEW.size_bytes<>0))
       OR (NEW.entry_type='file' AND (NEW.object_key IS NULL OR NEW.object_key=''))
       OR NEW.size_bytes IS NULL OR NEW.size_bytes<0 THEN
        RAISE EXCEPTION 'Invalid workspace entry' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR NEW.metadata_revision<>1 OR NEW.is_deleted IS DISTINCT FROM false
           OR NEW.deleted_at IS NOT NULL THEN
            RAISE EXCEPTION 'Invalid workspace entry' USING ERRCODE='23514';
        END IF;
        NEW.created_at:=clock_timestamp();
    ELSIF ROW(NEW.id,NEW.user_name,NEW.entry_type,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id,OLD.user_name,OLD.entry_type,OLD.created_at)
        OR NEW.metadata_revision<>OLD.metadata_revision+1 OR OLD.is_deleted IS TRUE
        OR NEW.is_deleted IS NULL THEN
        RAISE EXCEPTION 'Invalid workspace entry revision' USING ERRCODE='23514';
    ELSIF NEW.is_deleted THEN
        IF ROW(NEW.parent_path,NEW.name,NEW.object_key,NEW.size_bytes,NEW.etag)
           IS DISTINCT FROM ROW(OLD.parent_path,OLD.name,OLD.object_key,OLD.size_bytes,OLD.etag) THEN
            RAISE EXCEPTION 'Invalid workspace entry deletion' USING ERRCODE='23514';
        END IF;
        NEW.deleted_at:=clock_timestamp();
    ELSIF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
        RAISE EXCEPTION 'Invalid workspace entry revision' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER workspace_entry_guard BEFORE INSERT OR UPDATE ON nova_meta.config_workspace_entries
    FOR EACH ROW EXECUTE FUNCTION nova_meta.workspace_entry_guard();

CREATE FUNCTION nova_meta.workspace_version_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE entry record; found_entry boolean; highest bigint;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; NEW.state:='published'; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='INSERT' THEN
        SELECT e.user_name,e.entry_type,e.is_deleted INTO entry
            FROM nova_meta.config_workspace_entries e WHERE e.id=NEW.entry_id;
        found_entry:=FOUND;
        SELECT max(v.version) INTO highest
            FROM nova_meta.config_workspace_file_versions v WHERE v.entry_id=NEW.entry_id;
        IF NOT found_entry OR entry.is_deleted IS TRUE OR entry.entry_type<>'file'
           OR entry.user_name<>NEW.user_name
           OR NEW.version<>coalesce(highest,0)+1 OR btrim(NEW.object_key)=''
           OR NEW.id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR NEW.metadata_revision<>1 OR NEW.is_deleted THEN
            RAISE EXCEPTION 'Invalid workspace file version' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.state:='reserved';
        NEW.size_bytes:=NULL; NEW.etag:=NULL; NEW.content_sha256:=NULL;
        NEW.created_at:=clock_timestamp();
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.entry_id,NEW.user_name,NEW.version,NEW.object_key,NEW.created_at,
           NEW.provenance)
       IS DISTINCT FROM ROW(OLD.id,OLD.entry_id,OLD.user_name,OLD.version,OLD.object_key,
           OLD.created_at,OLD.provenance)
       OR NEW.metadata_revision<>OLD.metadata_revision+1 OR OLD.is_deleted THEN
        RAISE EXCEPTION 'Invalid workspace file version revision' USING ERRCODE='23514';
    END IF;
    IF NEW.is_deleted THEN
        -- Retirement hides a version without rewriting its receipt.
        IF ROW(NEW.state,NEW.size_bytes,NEW.etag,NEW.content_sha256)
           IS DISTINCT FROM ROW(OLD.state,OLD.size_bytes,OLD.etag,OLD.content_sha256) THEN
            RAISE EXCEPTION 'Invalid workspace file version retirement' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.state='reserved' AND NEW.state='published' THEN
        IF NEW.size_bytes IS NULL OR NEW.size_bytes<0 OR NEW.etag IS NULL OR btrim(NEW.etag)=''
           OR NEW.content_sha256 IS NULL THEN
            RAISE EXCEPTION 'A published version requires its object receipt' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.state='reserved' AND NEW.state='abandoned' THEN
        IF ROW(NEW.size_bytes,NEW.etag,NEW.content_sha256) IS DISTINCT FROM ROW(NULL,NULL,NULL) THEN
            RAISE EXCEPTION 'An abandoned version has no receipt' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'Workspace file versions are immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER workspace_version_guard BEFORE INSERT OR UPDATE
    ON nova_meta.config_workspace_file_versions FOR EACH ROW
    EXECUTE FUNCTION nova_meta.workspace_version_guard();

-- Success audit records object identity and revision only: file names, paths,
-- object keys and contents are not copied into audit metadata.
CREATE FUNCTION nova_meta.audit_workspace_file_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE action text;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user<>'nova_meta_web' OR NOT nova_meta.actor_context_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_TABLE_NAME NOT IN
            ('config_workspace_entries','config_workspace_file_versions')
       OR TG_OP NOT IN ('INSERT','UPDATE') THEN
        RAISE EXCEPTION 'Invalid workspace audit source' USING ERRCODE='42501';
    END IF;
    action:=CASE WHEN NEW.is_deleted THEN 'DELETE' WHEN TG_OP='INSERT' THEN 'CREATE'
        ELSE 'UPDATE' END;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,NEW.id,action,'SUCCESS','ALLOW',
        TG_TABLE_NAME,jsonb_build_object('id',NEW.id),NEW.metadata_revision);
    RETURN NULL;
END
$body$;
CREATE TRIGGER workspace_entry_audit AFTER INSERT OR UPDATE
    ON nova_meta.config_workspace_entries
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_workspace_file_change();
CREATE TRIGGER workspace_version_audit AFTER INSERT OR UPDATE
    ON nova_meta.config_workspace_file_versions
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_workspace_file_change();
