-- Catalog control metadata: Nova's catalog definitions, the inventory a sync
-- recorded (objects, columns, securable ids), sync runs, source connections
-- with their encrypted credentials, business metadata (annotations, tags),
-- lineage edges, Semantic View/entity links and the retained legacy catalog
-- table. What the engine lists for a caller stays an engine observation; these
-- rows never decide visibility.
--
-- Rows carry provenance: imported source rows are kept as they were, while
-- identity, lifecycle and fencing rules apply to rows written at runtime.

CREATE TABLE nova_meta.catalog_catalogs (
    name varchar(256) PRIMARY KEY CHECK (octet_length(name)<=256),
    kind varchar(32) NOT NULL CHECK (octet_length(kind)<=32),
    catalog_type varchar(64) NOT NULL CHECK (octet_length(catalog_type)<=64),
    managed boolean NOT NULL,
    source varchar(32) NOT NULL CHECK (octet_length(source)<=32),
    metastore_type varchar(32) CHECK (octet_length(metastore_type)<=32),
    metastore_uri varchar(1024) CHECK (octet_length(metastore_uri)<=1024),
    storage_connection varchar(256) CHECK (octet_length(storage_connection)<=256),
    comment varchar(1024) CHECK (octet_length(comment)<=1024),
    properties_json text CHECK (octet_length(properties_json)<=65533),
    status varchar(32) CHECK (octet_length(status)<=32),
    last_sync_at timestamptz,
    last_sync_error varchar(2048) CHECK (octet_length(last_sync_error)<=2048),
    created_by varchar(128) CHECK (octet_length(created_by)<=128),
    created_at timestamptz,
    updated_at timestamptz,
    dropped_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    sync_generation bigint NOT NULL DEFAULT 0 CHECK (sync_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.catalog_objects (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    catalog_name varchar(256) NOT NULL CHECK (octet_length(catalog_name)<=256),
    database_name varchar(256) NOT NULL CHECK (octet_length(database_name)<=256),
    object_name varchar(256) NOT NULL CHECK (octet_length(object_name)<=256),
    object_type varchar(32) NOT NULL CHECK (octet_length(object_type)<=32),
    engine varchar(64) CHECK (octet_length(engine)<=64),
    comment varchar(2048) CHECK (octet_length(comment)<=2048),
    row_count bigint,
    data_size bigint,
    fingerprint varchar(64) CHECK (octet_length(fingerprint)<=64),
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    dropped_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX catalog_objects_scope ON nova_meta.catalog_objects
    (catalog_name,database_name,object_name);

CREATE TABLE nova_meta.catalog_columns (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    catalog_name varchar(256) NOT NULL CHECK (octet_length(catalog_name)<=256),
    database_name varchar(256) NOT NULL CHECK (octet_length(database_name)<=256),
    object_name varchar(256) NOT NULL CHECK (octet_length(object_name)<=256),
    ordinal integer NOT NULL,
    column_name varchar(256) NOT NULL CHECK (octet_length(column_name)<=256),
    data_type varchar(1024) CHECK (octet_length(data_type)<=1024),
    is_nullable boolean,
    column_comment varchar(2048) CHECK (octet_length(column_comment)<=2048),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX catalog_columns_scope ON nova_meta.catalog_columns
    (catalog_name,database_name,object_name,ordinal);

-- "trigger" is a non-reserved keyword in PostgreSQL; the source name is kept.
CREATE TABLE nova_meta.catalog_sync_runs (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    catalog_name varchar(256) NOT NULL CHECK (octet_length(catalog_name)<=256),
    trigger varchar(32) NOT NULL CHECK (octet_length(trigger)<=32),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    started_at timestamptz,
    finished_at timestamptz,
    counts_json text CHECK (octet_length(counts_json)<=65533),
    error varchar(2048) CHECK (octet_length(error)<=2048),
    sync_generation bigint CHECK (sync_generation>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX catalog_sync_runs_catalog ON nova_meta.catalog_sync_runs
    (catalog_name,started_at DESC);

CREATE TABLE nova_meta.catalog_connections (
    name varchar(256) PRIMARY KEY CHECK (octet_length(name)<=256),
    connector_type varchar(64) NOT NULL CHECK (octet_length(connector_type)<=64),
    host varchar(512) NOT NULL CHECK (octet_length(host)<=512),
    port integer,
    database_name varchar(256) CHECK (octet_length(database_name)<=256),
    options_json text CHECK (octet_length(options_json)<=65533),
    secret_ref varchar(1024) NOT NULL CHECK (octet_length(secret_ref)<=1024),
    owner varchar(128) CHECK (octet_length(owner)<=128),
    comment varchar(1024) CHECK (octet_length(comment)<=1024),
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

-- Ciphertext only. The values are opaque here: Nova's existing encryption owner
-- writes and reads them, and no response, log or audit row carries them.
CREATE TABLE nova_meta.catalog_connection_credentials (
    connection_name varchar(256) PRIMARY KEY CHECK (octet_length(connection_name)<=256),
    username_enc text NOT NULL CHECK (octet_length(username_enc)<=65533),
    password_enc text NOT NULL CHECK (octet_length(password_enc)<=65533),
    updated_by varchar(128) CHECK (octet_length(updated_by)<=128),
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE TABLE nova_meta.catalog_connection_write_credentials (
    connection_name varchar(256) PRIMARY KEY CHECK (octet_length(connection_name)<=256),
    username_enc text NOT NULL CHECK (octet_length(username_enc)<=65533),
    password_enc text NOT NULL CHECK (octet_length(password_enc)<=65533),
    updated_by varchar(128) CHECK (octet_length(updated_by)<=128),
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.catalog_securables (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    securable_type varchar(32) NOT NULL CHECK (octet_length(securable_type)<=32),
    catalog_name varchar(256) NOT NULL CHECK (octet_length(catalog_name)<=256),
    database_name varchar(256) NOT NULL CHECK (octet_length(database_name)<=256),
    object_name varchar(256) NOT NULL CHECK (octet_length(object_name)<=256),
    column_name varchar(256) NOT NULL CHECK (octet_length(column_name)<=256),
    parent_id varchar(64) CHECK (octet_length(parent_id)<=64),
    data_type varchar(1024) CHECK (octet_length(data_type)<=1024),
    ordinal integer,
    first_seen_at timestamptz,
    updated_at timestamptz,
    dropped_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX catalog_securables_catalog ON nova_meta.catalog_securables (catalog_name);
CREATE INDEX catalog_securables_parent ON nova_meta.catalog_securables (parent_id);
CREATE INDEX catalog_securables_type ON nova_meta.catalog_securables (securable_type);

CREATE TABLE nova_meta.catalog_annotations (
    securable_id varchar(64) PRIMARY KEY CHECK (octet_length(securable_id)<=64),
    securable_type varchar(32) NOT NULL CHECK (octet_length(securable_type)<=32),
    comment varchar(4096) CHECK (octet_length(comment)<=4096),
    owner_name varchar(128) CHECK (octet_length(owner_name)<=128),
    owner_type varchar(16) CHECK (octet_length(owner_type)<=16),
    classification varchar(32) CHECK (octet_length(classification)<=32),
    updated_at timestamptz,
    updated_by varchar(128) CHECK (octet_length(updated_by)<=128),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_object_tags (
    securable_id varchar(64) NOT NULL CHECK (octet_length(securable_id)<=64),
    tag_key varchar(128) NOT NULL CHECK (octet_length(tag_key)<=128),
    tag_value varchar(512) CHECK (octet_length(tag_value)<=512),
    securable_type varchar(32) NOT NULL CHECK (octet_length(securable_type)<=32),
    created_at timestamptz,
    created_by varchar(128) CHECK (octet_length(created_by)<=128),
    updated_at timestamptz,
    updated_by varchar(128) CHECK (octet_length(updated_by)<=128),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (securable_id,tag_key)
);
CREATE INDEX config_object_tags_key ON nova_meta.config_object_tags (tag_key);

CREATE TABLE nova_meta.catalog_lineage (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    source_id varchar(64) NOT NULL CHECK (octet_length(source_id)<=64),
    target_id varchar(64) NOT NULL CHECK (octet_length(target_id)<=64),
    source_name varchar(1024) NOT NULL CHECK (octet_length(source_name)<=1024),
    target_name varchar(1024) NOT NULL CHECK (octet_length(target_name)<=1024),
    statement_kind varchar(32) NOT NULL CHECK (octet_length(statement_kind)<=32),
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    last_query_id varchar(64) CHECK (octet_length(last_query_id)<=64),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX catalog_lineage_source ON nova_meta.catalog_lineage (source_id);
CREATE INDEX catalog_lineage_target ON nova_meta.catalog_lineage (target_id);

CREATE TABLE nova_meta.catalog_securable_links (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    owner_type varchar(32) NOT NULL CHECK (octet_length(owner_type)<=32),
    owner_id varchar(64) NOT NULL CHECK (octet_length(owner_id)<=64),
    owner_label varchar(512) CHECK (octet_length(owner_label)<=512),
    securable_id varchar(64) NOT NULL CHECK (octet_length(securable_id)<=64),
    relation_name varchar(1024) NOT NULL CHECK (octet_length(relation_name)<=1024),
    updated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX catalog_securable_links_securable ON nova_meta.catalog_securable_links (securable_id);
CREATE INDEX catalog_securable_links_owner ON nova_meta.catalog_securable_links (owner_type,owner_id);

-- Retained legacy state. Its rows (properties may predate the credential
-- validator) are imported as they were and never read or written by a workload.
CREATE TABLE nova_meta.config_external_catalogs (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    name varchar(256) NOT NULL CHECK (octet_length(name)<=256),
    catalog_type varchar(32) NOT NULL CHECK (octet_length(catalog_type)<=32),
    metastore_type varchar(32) CHECK (octet_length(metastore_type)<=32),
    metastore_uri varchar(1024) CHECK (octet_length(metastore_uri)<=1024),
    storage_connection varchar(256) CHECK (octet_length(storage_connection)<=256),
    comment varchar(1024) CHECK (octet_length(comment)<=1024),
    properties_json text CHECK (octet_length(properties_json)<=65533),
    created_at timestamptz,
    created_by varchar(128) CHECK (octet_length(created_by)<=128)
);

DO $rls$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['catalog_catalogs','catalog_objects','catalog_columns',
            'catalog_sync_runs','catalog_connections','catalog_connection_credentials',
            'catalog_connection_write_credentials','catalog_securables','catalog_annotations',
            'config_object_tags','catalog_lineage','catalog_securable_links',
            'config_external_catalogs'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY', tbl);
    END LOOP;
END
$rls$;

-- ── Helpers ────────────────────────────────────────────────────────────────

-- Catalog metadata is control-plane state: the owning service authorizes the
-- caller (and the engine/Ranger govern data), so access is per workload,
-- through a bound actor context, never anonymous.
CREATE FUNCTION nova_meta.catalog_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;

-- The security-admin roles of AccessControlService.SECURITY_ADMIN_ROLES:
-- the stewards who set owners and edit catalog- and database-level metadata.
CREATE FUNCTION nova_meta.catalog_actor_is_steward() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT current_setting('nova.active_role',true) IN ('ACCOUNTADMIN','SECURITYADMIN','security_admin')
$body$;

-- repository.row_id: sha256 of the natural-key parts joined by U+001F.
CREATE FUNCTION nova_meta.catalog_row_id(VARIADIC parts text[]) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path=pg_catalog AS $body$
    SELECT encode(sha256(convert_to(array_to_string(parts, chr(31)),'UTF8')),'hex')
$body$;

-- A JSON object of non-secret properties: the credential-key rule of
-- sql_guard.is_credential_property (CREDENTIAL_PARAM_SUFFIXES), case-insensitive.
CREATE FUNCTION nova_meta.catalog_properties_safe(document text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $body$
BEGIN
    IF document IS NULL THEN RETURN true; END IF;
    IF NOT pg_input_is_valid(document,'jsonb') THEN RETURN false; END IF;
    IF jsonb_typeof(document::jsonb)<>'object' THEN RETURN false; END IF;
    RETURN NOT EXISTS (
        SELECT 1 FROM jsonb_object_keys(document::jsonb) AS k(key)
        WHERE k.key ~* ('^(?:[A-Za-z_][\w$]*[._])*(?:api[._]key|access[._]key|secret[._]key|'
            'session[._]token|account[._]key|sas[._]token|service[._]account[._]key|'
            'shared[._]key|private[._]key|password|credential)$'));
END
$body$;

-- The sync fence. A harvest claims a catalog by advancing its sync_generation
-- and names that generation for its writes (transaction settings
-- nova.catalog_sync_catalog / nova.catalog_sync_generation). Each inventory
-- row write locks the catalog row FOR SHARE and must name the current
-- generation, so a claim waits for in-flight writes and every later write of an
-- older harvest is refused. With no catalog row (an imported orphan) only a
-- tombstone or column removal is allowed.
CREATE FUNCTION nova_meta.catalog_sync_fenced(catalog text, tombstone boolean) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE held bigint;
BEGIN
    SELECT sync_generation INTO held FROM nova_meta.catalog_catalogs WHERE name=catalog FOR SHARE;
    IF NOT FOUND THEN
        IF NOT tombstone THEN
            RAISE EXCEPTION 'Catalog inventory needs a registered catalog' USING ERRCODE='23514';
        END IF;
        RETURN;
    END IF;
    IF current_setting('nova.catalog_sync_catalog',true) IS DISTINCT FROM catalog
       OR current_setting('nova.catalog_sync_generation',true) IS DISTINCT FROM held::text THEN
        RAISE EXCEPTION 'Catalog sync generation is stale' USING ERRCODE='40001';
    END IF;
END
$body$;

REVOKE ALL ON FUNCTION nova_meta.catalog_actor_bound(),nova_meta.catalog_actor_is_steward(),
    nova_meta.catalog_row_id(text[]),nova_meta.catalog_properties_safe(text),
    nova_meta.catalog_sync_fenced(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.catalog_actor_bound(),nova_meta.catalog_actor_is_steward(),
    nova_meta.catalog_row_id(text[]),nova_meta.catalog_properties_safe(text),
    nova_meta.catalog_sync_fenced(text,boolean)
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_maintenance,
       nova_meta_notebook,nova_meta_task;

-- ── Workload access ────────────────────────────────────────────────────────
-- Writers and readers are the workloads that call the source repositories:
-- web (API), query and proxy (catalog SQL, including REFRESH), maintenance
-- (the catalog refresher), agent (catalog search, Semantic View links) and
-- notebook/task (write-back resolves its connection and write credential).

DO $policies$
DECLARE item record; readers text; inserters text; updaters text; deleters text;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('catalog_catalogs','web,query,proxy,agent,maintenance,notebook,task',
            'web,query,proxy,maintenance','web,query,proxy,maintenance',''),
        ('catalog_objects','web,query,proxy,agent,maintenance',
            'web,query,proxy,maintenance','web,query,proxy,maintenance',''),
        ('catalog_columns','web,query,proxy,agent,maintenance',
            'web,query,proxy,maintenance','web,query,proxy,maintenance','web,query,proxy,maintenance'),
        ('catalog_sync_runs','web,query,proxy,maintenance',
            'web,query,proxy,maintenance','web,query,proxy,maintenance',''),
        ('catalog_connections','web,query,proxy,maintenance,notebook,task',
            'web,query,proxy','web,query,proxy','web,query,proxy'),
        ('catalog_connection_credentials','web,query,proxy,maintenance',
            'web,query,proxy','web,query,proxy','web,query,proxy'),
        ('catalog_connection_write_credentials','web,query,proxy,notebook,task',
            'web','web','web,query,proxy'),
        ('catalog_securables','web,query,proxy,agent,maintenance',
            'web,query,proxy,maintenance','web,query,proxy,maintenance',''),
        ('catalog_annotations','web,agent','web','web',''),
        ('config_object_tags','web,agent','web','web','web'),
        ('catalog_lineage','web,query,proxy,agent,maintenance',
            'web,query,proxy,maintenance','web,query,proxy,maintenance',''),
        ('catalog_securable_links','web,query,proxy,agent,maintenance',
            'web,query,proxy,agent,maintenance','web,query,proxy,agent,maintenance',
            'web,query,proxy,agent,maintenance'),
        ('config_external_catalogs','','','','')
    ) AS t(tbl,r,i,u,d) LOOP
        SELECT string_agg('nova_meta_'||x,',') INTO readers FROM unnest(string_to_array(item.r,',')) x;
        SELECT string_agg('nova_meta_'||x,',') INTO inserters FROM unnest(string_to_array(item.i,',')) x;
        SELECT string_agg('nova_meta_'||x,',') INTO updaters FROM unnest(string_to_array(item.u,',')) x;
        SELECT string_agg('nova_meta_'||x,',') INTO deleters FROM unnest(string_to_array(item.d,',')) x;
        IF readers IS NOT NULL THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO %s USING (nova_meta.catalog_actor_bound())',
                item.tbl||'_read', item.tbl, readers);
        END IF;
        IF inserters IS NOT NULL THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO %s WITH CHECK (nova_meta.catalog_actor_bound())',
                item.tbl||'_insert', item.tbl, inserters);
        END IF;
        IF updaters IS NOT NULL THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO %s USING (nova_meta.catalog_actor_bound()) WITH CHECK (nova_meta.catalog_actor_bound())',
                item.tbl||'_update', item.tbl, updaters);
        END IF;
        IF deleters IS NOT NULL THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR DELETE TO %s USING (nova_meta.catalog_actor_bound())',
                item.tbl||'_delete', item.tbl, deleters);
            EXECUTE format('GRANT DELETE ON nova_meta.%I TO %s', item.tbl, deleters);
        END IF;
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            item.tbl||'_import', item.tbl);
    END LOOP;
END
$policies$;

-- Table reads, except the two credential tables, whose ciphertext columns are
-- readable only by the workloads that resolve them.
GRANT SELECT ON nova_meta.catalog_catalogs
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_maintenance,
       nova_meta_notebook,nova_meta_task;
GRANT SELECT ON nova_meta.catalog_objects,nova_meta.catalog_columns,nova_meta.catalog_securables,
        nova_meta.catalog_lineage,nova_meta.catalog_securable_links
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_maintenance;
GRANT SELECT ON nova_meta.catalog_sync_runs
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT SELECT ON nova_meta.catalog_connections
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance,nova_meta_notebook,
       nova_meta_task;
GRANT SELECT ON nova_meta.catalog_annotations,nova_meta.config_object_tags
    TO nova_meta_web,nova_meta_agent;
-- Entry: web, query and proxy; resolution (foreign catalog creation and the
-- sync's source classification): the same plus maintenance.
GRANT SELECT ON nova_meta.catalog_connection_credentials
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
-- Write-back credentials: management sees only that one exists; only the
-- notebook and task write-back workloads read the ciphertext.
GRANT SELECT (connection_name,updated_by,updated_at,metadata_revision,provenance)
    ON nova_meta.catalog_connection_write_credentials TO nova_meta_web,nova_meta_query,nova_meta_proxy;
GRANT SELECT ON nova_meta.catalog_connection_write_credentials TO nova_meta_notebook,nova_meta_task;

GRANT INSERT (name,kind,catalog_type,managed,source,metastore_type,metastore_uri,
        storage_connection,comment,properties_json),
    UPDATE (kind,catalog_type,managed,source,metastore_type,metastore_uri,storage_connection,
        comment,properties_json,status,last_sync_at,last_sync_error,dropped_at,
        metadata_revision,sync_generation)
    ON nova_meta.catalog_catalogs
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT INSERT (id,catalog_name,database_name,object_name,object_type,engine,comment,row_count,
        data_size,fingerprint),
    UPDATE (object_type,engine,comment,row_count,data_size,fingerprint,dropped_at)
    ON nova_meta.catalog_objects
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT INSERT (id,catalog_name,database_name,object_name,ordinal,column_name,data_type,
        is_nullable,column_comment),
    UPDATE (column_name,data_type,is_nullable,column_comment)
    ON nova_meta.catalog_columns
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT INSERT (id,catalog_name,trigger,status,sync_generation),
    UPDATE (status,counts_json,error)
    ON nova_meta.catalog_sync_runs
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT INSERT (name,connector_type,host,port,database_name,options_json,secret_ref,comment),
    UPDATE (connector_type,host,port,database_name,options_json,secret_ref,comment,
        metadata_revision)
    ON nova_meta.catalog_connections TO nova_meta_web,nova_meta_query,nova_meta_proxy;
GRANT INSERT (connection_name,username_enc,password_enc),
    UPDATE (username_enc,password_enc,metadata_revision)
    ON nova_meta.catalog_connection_credentials TO nova_meta_web,nova_meta_query,nova_meta_proxy;
GRANT INSERT (connection_name,username_enc,password_enc),
    UPDATE (username_enc,password_enc,metadata_revision)
    ON nova_meta.catalog_connection_write_credentials TO nova_meta_web;
GRANT INSERT (id,securable_type,catalog_name,database_name,object_name,column_name,parent_id,
        data_type,ordinal),
    UPDATE (securable_type,column_name,data_type,ordinal,dropped_at)
    ON nova_meta.catalog_securables
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT INSERT (securable_id,securable_type,comment,owner_name,owner_type,classification),
    UPDATE (comment,owner_name,owner_type,classification,metadata_revision)
    ON nova_meta.catalog_annotations TO nova_meta_web;
GRANT INSERT (securable_id,tag_key,tag_value,securable_type),
    UPDATE (tag_value,metadata_revision)
    ON nova_meta.config_object_tags TO nova_meta_web;
GRANT INSERT (id,source_id,target_id,source_name,target_name,statement_kind,first_seen_at,
        last_seen_at,last_query_id),
    UPDATE (source_name,target_name,statement_kind,first_seen_at,last_seen_at,last_query_id)
    ON nova_meta.catalog_lineage
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_maintenance;
GRANT INSERT (id,owner_type,owner_id,owner_label,securable_id,relation_name),
    UPDATE (owner_label,relation_name)
    ON nova_meta.catalog_securable_links
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_agent,nova_meta_maintenance;

-- ── Statement guard: epoch/maintenance for workloads, import for the migrator ─

CREATE FUNCTION nova_meta.catalog_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Catalog import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_query','nova_meta_proxy','nova_meta_agent',
                           'nova_meta_maintenance')
        AND TG_OP IN ('INSERT','UPDATE','DELETE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Catalog metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
DO $guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['catalog_catalogs','catalog_objects','catalog_columns',
            'catalog_sync_runs','catalog_connections','catalog_connection_credentials',
            'catalog_connection_write_credentials','catalog_securables','catalog_annotations',
            'config_object_tags','catalog_lineage','catalog_securable_links',
            'config_external_catalogs'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.catalog_write_guard()',
            tbl||'_write_guard', tbl);
    END LOOP;
END
$guards$;

-- ── Row rules for runtime writes ───────────────────────────────────────────
-- Imported rows bypass them on insert; a later runtime change to an imported
-- row must still be a valid transition. One branch per table: a record field a
-- table lacks never appears in an expression evaluated for that table.

CREATE FUNCTION nova_meta.catalog_row_guard() RETURNS trigger
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
           OR NEW.connector_type NOT IN ('starrocks','postgres','snowflake')
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
DO $rows$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['catalog_catalogs','catalog_objects','catalog_sync_runs',
            'catalog_securables','catalog_annotations','catalog_lineage'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.catalog_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
    FOREACH tbl IN ARRAY ARRAY['catalog_columns','catalog_connections',
            'catalog_connection_credentials','catalog_connection_write_credentials',
            'config_object_tags','catalog_securable_links'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.catalog_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
    EXECUTE 'CREATE TRIGGER config_external_catalogs_row_guard BEFORE INSERT OR UPDATE OR DELETE ON nova_meta.config_external_catalogs FOR EACH ROW EXECUTE FUNCTION nova_meta.catalog_row_guard()';
END
$rows$;

-- ── Success audit for governed changes ─────────────────────────────────────
-- Catalog definitions, connections, credential entry/removal, annotations and
-- tags are governed actions. Inventory, runs, lineage and links are derived
-- state (as in the source) and are not. An audit row names the object and the
-- changed fields, never a tag value, comment, endpoint or credential.

CREATE FUNCTION nova_meta.audit_catalog_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity text; revision bigint; detail jsonb; fields text[] := ARRAY[]::text[];
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_proxy','nova_meta_maintenance')
       OR NOT nova_meta.actor_context_valid() OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('catalog_catalogs','catalog_connections',
            'catalog_connection_credentials','catalog_connection_write_credentials',
            'catalog_annotations','config_object_tags') THEN
        RAISE EXCEPTION 'Invalid catalog audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='catalog_catalogs' THEN
        IF TG_OP='UPDATE' AND NEW.metadata_revision IS NOT DISTINCT FROM OLD.metadata_revision THEN
            RETURN NULL;  -- sync status and generation are bookkeeping, not a change
        END IF;
        identity:=NEW.name; revision:=NEW.metadata_revision;
        detail:=jsonb_build_object('id',identity);
    ELSIF TG_TABLE_NAME='catalog_connections' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.name; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.name; revision:=NEW.metadata_revision; END IF;
        detail:=jsonb_build_object('id',identity);
    ELSIF TG_TABLE_NAME IN ('catalog_connection_credentials','catalog_connection_write_credentials') THEN
        IF TG_OP='DELETE' THEN identity:=OLD.connection_name; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.connection_name; revision:=NEW.metadata_revision; END IF;
        detail:=jsonb_build_object('connection',identity);
    ELSIF TG_TABLE_NAME='catalog_annotations' THEN
        identity:=NEW.securable_id; revision:=NEW.metadata_revision;
        IF TG_OP='INSERT' OR NEW.comment IS DISTINCT FROM OLD.comment THEN
            IF NEW.comment IS NOT NULL OR TG_OP='UPDATE' THEN fields:=array_append(fields,'comment'); END IF;
        END IF;
        IF (TG_OP='INSERT' AND NEW.owner_name IS NOT NULL) OR (TG_OP='UPDATE'
            AND ROW(NEW.owner_name,NEW.owner_type) IS DISTINCT FROM ROW(OLD.owner_name,OLD.owner_type)) THEN
            fields:=array_append(fields,'owner');
        END IF;
        IF (TG_OP='INSERT' AND NEW.classification IS NOT NULL) OR (TG_OP='UPDATE'
            AND NEW.classification IS DISTINCT FROM OLD.classification) THEN
            fields:=array_append(fields,'classification');
        END IF;
        detail:=jsonb_build_object('id',identity,'fields',to_jsonb(fields));
    ELSE
        IF TG_OP='DELETE' THEN
            identity:=OLD.securable_id; revision:=OLD.metadata_revision;
            detail:=jsonb_build_object('id',identity,'tag',OLD.tag_key);
        ELSE
            identity:=NEW.securable_id; revision:=NEW.metadata_revision;
            detail:=jsonb_build_object('id',identity,'tag',NEW.tag_key);
        END IF;
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,identity,
        CASE TG_OP WHEN 'INSERT' THEN 'CREATE' WHEN 'DELETE' THEN 'DELETE' ELSE 'UPDATE' END,
        'SUCCESS','ALLOW',TG_TABLE_NAME,detail,coalesce(revision,1));
    RETURN NULL;
END
$body$;
CREATE TRIGGER catalog_catalogs_audit AFTER INSERT OR UPDATE ON nova_meta.catalog_catalogs
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_catalog_change();
CREATE TRIGGER catalog_connections_audit AFTER INSERT OR UPDATE OR DELETE
    ON nova_meta.catalog_connections FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_catalog_change();
CREATE TRIGGER catalog_connection_credentials_audit AFTER INSERT OR UPDATE OR DELETE
    ON nova_meta.catalog_connection_credentials
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_catalog_change();
CREATE TRIGGER catalog_connection_write_credentials_audit AFTER INSERT OR UPDATE OR DELETE
    ON nova_meta.catalog_connection_write_credentials
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_catalog_change();
CREATE TRIGGER catalog_annotations_audit AFTER INSERT OR UPDATE ON nova_meta.catalog_annotations
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_catalog_change();
CREATE TRIGGER config_object_tags_audit AFTER INSERT OR UPDATE OR DELETE
    ON nova_meta.config_object_tags FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_catalog_change();
