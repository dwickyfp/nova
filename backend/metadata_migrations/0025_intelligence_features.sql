-- The remaining Intelligence control metadata: the entity registry, Feature
-- Views and Groups with their versions, training-set and model lineage, the
-- legacy agent semantic models (read for the one-time Semantic View migration)
-- and the redacted semantic query usage log.
-- Rows carry provenance: imported source rows are kept as they were, while
-- lifecycle rules apply to rows written at runtime. Materialized feature tables
-- stay with their engine owner; only metadata lives here.

-- ── Entity registry ─────────────────────────────────────────────────────────
CREATE TABLE nova_meta.config_entities (
    catalog_name varchar(128) NOT NULL CHECK (octet_length(catalog_name)<=128),
    database_name varchar(128) NOT NULL CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) NOT NULL CHECK (octet_length(schema_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    id varchar(64) NOT NULL UNIQUE CHECK (octet_length(id)<=64),
    description text CHECK (char_length(description)<=65533),
    relation_name varchar(512) NOT NULL CHECK (octet_length(relation_name)<=512),
    key_columns jsonb NOT NULL,
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    tags jsonb,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (catalog_name,database_name,schema_name,name)
);

-- ── Feature store ───────────────────────────────────────────────────────────
CREATE TABLE nova_meta.config_feature_views (
    name varchar(128) PRIMARY KEY CHECK (octet_length(name)<=128),
    id varchar(64) NOT NULL UNIQUE CHECK (octet_length(id)<=64),
    entity_id varchar(64) NOT NULL CHECK (octet_length(entity_id)<=64),
    source_relation varchar(512) NOT NULL CHECK (octet_length(source_relation)<=512),
    entity_keys jsonb NOT NULL,
    event_timestamp varchar(128) NOT NULL CHECK (octet_length(event_timestamp)<=128),
    feature_columns jsonb NOT NULL,
    freshness_seconds integer NOT NULL,
    ttl_seconds integer NOT NULL,
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    active_version integer,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_feature_view_versions (
    view_name varchar(128) NOT NULL CHECK (octet_length(view_name)<=128),
    version integer NOT NULL CHECK (version>0),
    relation_name varchar(256) NOT NULL CHECK (octet_length(relation_name)<=256),
    definition jsonb NOT NULL,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    row_count bigint NOT NULL DEFAULT 0 CHECK (row_count>=0),
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (view_name,version)
);

CREATE TABLE nova_meta.config_feature_groups (
    name varchar(128) PRIMARY KEY CHECK (octet_length(name)<=128),
    id varchar(64) NOT NULL UNIQUE CHECK (octet_length(id)<=64),
    entity_id varchar(64) NOT NULL CHECK (octet_length(entity_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    active_version integer,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_feature_group_versions (
    group_name varchar(128) NOT NULL CHECK (octet_length(group_name)<=128),
    version integer NOT NULL CHECK (version>0),
    members jsonb NOT NULL,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    created_at timestamptz NOT NULL,
    activated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (group_name,version)
);
CREATE INDEX config_feature_group_versions_members ON nova_meta.config_feature_group_versions
    USING gin (members jsonb_path_ops);

CREATE TABLE nova_meta.config_feature_training_sets (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    group_name varchar(128) NOT NULL CHECK (octet_length(group_name)<=128),
    group_version integer NOT NULL,
    label_relation varchar(512) NOT NULL CHECK (octet_length(label_relation)<=512),
    generated_sql text NOT NULL CHECK (char_length(generated_sql)<=65533),
    lineage jsonb NOT NULL,
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    created_at timestamptz NOT NULL,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_feature_training_sets_owner ON nova_meta.config_feature_training_sets
    (owner_name,created_at DESC);

CREATE TABLE nova_meta.config_feature_ml_links (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    model_id varchar(64) NOT NULL CHECK (octet_length(model_id)<=64),
    model_version integer NOT NULL,
    training_set_id varchar(64) NOT NULL CHECK (octet_length(training_set_id)<=64),
    group_name varchar(128) NOT NULL CHECK (octet_length(group_name)<=128),
    group_version integer NOT NULL,
    lineage jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    created_by varchar(128) CHECK (octet_length(created_by)<=128),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_feature_ml_links_training_set ON nova_meta.config_feature_ml_links
    (training_set_id);

-- ── Legacy agent semantic models ────────────────────────────────────────────
-- Historical: no runtime writer creates or edits these rows. They are read by
-- the one-time Semantic View migration; verified queries keep usage counters.
CREATE TABLE nova_meta.config_semantic_models (
    semantic_model_id varchar(64) PRIMARY KEY CHECK (octet_length(semantic_model_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    description text CHECK (char_length(description)<=65533),
    database_name varchar(128) CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) CHECK (octet_length(schema_name)<=128),
    ossie_version varchar(32) NOT NULL CHECK (octet_length(ossie_version)<=32),
    definition jsonb,
    source_file_id varchar(64) CHECK (octet_length(source_file_id)<=64),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_semantic_model_versions (
    version_id varchar(64) PRIMARY KEY CHECK (octet_length(version_id)<=64),
    semantic_model_id varchar(64) NOT NULL CHECK (octet_length(semantic_model_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    proposal_id varchar(64) NOT NULL CHECK (octet_length(proposal_id)<=64),
    model_fingerprint varchar(128) NOT NULL CHECK (octet_length(model_fingerprint)<=128),
    definition jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_semantic_verified_queries (
    verified_query_id varchar(64) PRIMARY KEY CHECK (octet_length(verified_query_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    semantic_model_id varchar(64) NOT NULL CHECK (octet_length(semantic_model_id)<=64),
    model_fingerprint varchar(64) NOT NULL CHECK (octet_length(model_fingerprint)<=64),
    question text NOT NULL CHECK (char_length(question)<=65533),
    semantic_plan jsonb NOT NULL,
    verified_sql text NOT NULL CHECK (char_length(verified_sql)<=65533),
    expected_result_signature varchar(256) CHECK (octet_length(expected_result_signature)<=256),
    verified_by varchar(128) NOT NULL CHECK (octet_length(verified_by)<=128),
    verified_at timestamptz NOT NULL,
    tags jsonb,
    usage_count bigint NOT NULL DEFAULT 0 CHECK (usage_count>=0),
    success_count bigint NOT NULL DEFAULT 0 CHECK (success_count>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_semantic_verified_queries_model ON nova_meta.config_semantic_verified_queries
    (owner_name,semantic_model_id);

-- ── Semantic query usage ────────────────────────────────────────────────────
CREATE TABLE nova_meta.audit_semantic_query_usage (
    usage_id varchar(64) PRIMARY KEY CHECK (octet_length(usage_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    semantic_model_id varchar(64) NOT NULL CHECK (octet_length(semantic_model_id)<=64),
    model_fingerprint varchar(64) NOT NULL CHECK (octet_length(model_fingerprint)<=64),
    metric_names jsonb,
    dimension_names jsonb,
    filter_shape jsonb,
    time_grain varchar(32) CHECK (octet_length(time_grain)<=32),
    execution_latency_ms bigint CHECK (execution_latency_ms>=0),
    scan_bytes bigint CHECK (scan_bytes>=0),
    succeeded boolean NOT NULL,
    created_at timestamptz NOT NULL,
    active_role varchar(128) CHECK (octet_length(active_role)<=128),
    security_context_version bigint,
    semantic_version bigint,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX audit_semantic_query_usage_scope ON nova_meta.audit_semantic_query_usage
    (owner_name,active_role,semantic_model_id,created_at DESC);

-- ── Access ──────────────────────────────────────────────────────────────────
-- Entity and feature visibility (source and column access) stays the owning
-- service's and the engine's check. PG binds the workload and actor, and
-- isolates rows by owner where the source scopes them to one principal.
DO $policies$
DECLARE item record; read_predicate text; write_predicate text;
BEGIN
    -- rule: bound = workload and actor bound; owner = also the row's owner_name;
    -- scoped = also the row's owner and active role.
    FOR item IN SELECT * FROM (VALUES
        ('config_entities','web,agent,maintenance','web','web','','bound','bound'),
        ('config_feature_views','web','web','web','web','bound','bound'),
        ('config_feature_view_versions','web','web','web','web','bound','bound'),
        ('config_feature_groups','web','web','web','web','bound','bound'),
        ('config_feature_group_versions','web','web','web','web','bound','bound'),
        ('config_feature_training_sets','web','web','','','owner','owner'),
        ('config_feature_ml_links','web','web','','','bound','bound'),
        ('config_semantic_models','web,maintenance','','','','bound','bound'),
        ('config_semantic_model_versions','web,maintenance','','','','bound','bound'),
        ('config_semantic_verified_queries','web,maintenance','','agent','','bound','owner'),
        ('audit_semantic_query_usage','web,agent','web,agent','','','scoped','scoped')
    ) AS t(tbl,readers,inserters,updaters,deleters,read_rule,write_rule) LOOP
        read_predicate:='nova_meta.intelligence_actor_bound()';
        IF item.read_rule IN ('owner','scoped') THEN
            read_predicate:=read_predicate||' AND owner_name=current_setting(''nova.principal'',true)';
        END IF;
        IF item.read_rule='scoped' THEN
            read_predicate:=read_predicate||' AND active_role=current_setting(''nova.active_role'',true)';
        END IF;
        write_predicate:='nova_meta.intelligence_actor_bound()';
        IF item.write_rule IN ('owner','scoped') THEN
            write_predicate:=write_predicate||' AND owner_name=current_setting(''nova.principal'',true)';
        END IF;
        IF item.write_rule='scoped' THEN
            write_predicate:=write_predicate||' AND active_role=current_setting(''nova.active_role'',true)';
        END IF;
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY', item.tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY', item.tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO %s USING (%s)',
            item.tbl||'_read', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r),
            read_predicate);
        IF item.inserters<>'' THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO %s WITH CHECK (%s)',
                item.tbl||'_insert', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r),
                write_predicate);
        END IF;
        IF item.updaters<>'' THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO %s USING (%s) WITH CHECK (%s)',
                item.tbl||'_update', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.updaters,',')) r),
                write_predicate, write_predicate);
        END IF;
        IF item.deleters<>'' THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR DELETE TO %s USING (%s)',
                item.tbl||'_delete', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.deleters,',')) r),
                write_predicate);
            EXECUTE format('GRANT DELETE ON nova_meta.%I TO %s', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.deleters,',')) r));
        END IF;
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            item.tbl||'_import', item.tbl);
        EXECUTE format('GRANT SELECT ON nova_meta.%I TO %s', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
    END LOOP;
END
$policies$;
-- Agent counter updates find their row through SELECT, limited to its owner.
CREATE POLICY config_semantic_verified_queries_agent_read
    ON nova_meta.config_semantic_verified_queries FOR SELECT TO nova_meta_agent
    USING (nova_meta.intelligence_actor_bound()
        AND owner_name=current_setting('nova.principal',true));
GRANT SELECT ON nova_meta.config_semantic_verified_queries TO nova_meta_agent;
-- The pin guard below must see every group version, whoever created it.
CREATE POLICY config_feature_group_versions_owner_read ON nova_meta.config_feature_group_versions
    FOR SELECT TO nova_meta_owner USING (true);

GRANT INSERT (catalog_name,database_name,schema_name,name,id,description,relation_name,
        key_columns,owner_name,tags,status),
    UPDATE (status,metadata_revision)
    ON nova_meta.config_entities TO nova_meta_web;
GRANT INSERT (name,id,entity_id,source_relation,entity_keys,event_timestamp,feature_columns,
        freshness_seconds,ttl_seconds,owner_name,status),
    UPDATE (active_version,status,metadata_revision)
    ON nova_meta.config_feature_views TO nova_meta_web;
GRANT INSERT (view_name,version,relation_name,definition,status),UPDATE (status,row_count)
    ON nova_meta.config_feature_view_versions TO nova_meta_web;
GRANT INSERT (name,id,entity_id,owner_name,status),
    UPDATE (active_version,status,metadata_revision)
    ON nova_meta.config_feature_groups TO nova_meta_web;
GRANT INSERT (group_name,version,members,status),UPDATE (status)
    ON nova_meta.config_feature_group_versions TO nova_meta_web;
GRANT INSERT (id,group_name,group_version,label_relation,generated_sql,lineage,owner_name)
    ON nova_meta.config_feature_training_sets TO nova_meta_web;
GRANT INSERT (id,model_id,model_version,training_set_id,group_name,group_version,lineage)
    ON nova_meta.config_feature_ml_links TO nova_meta_web;
GRANT UPDATE (usage_count,success_count) ON nova_meta.config_semantic_verified_queries
    TO nova_meta_agent;
GRANT INSERT (usage_id,owner_name,semantic_model_id,model_fingerprint,metric_names,
        dimension_names,filter_shape,time_grain,execution_latency_ms,scan_bytes,succeeded,
        active_role,security_context_version,semantic_version)
    ON nova_meta.audit_semantic_query_usage TO nova_meta_web,nova_meta_agent;

-- ── Statement write guard (shared with revision 0022) ───────────────────────
DO $guards$
DECLARE tbl text; kind text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_entities','config_feature_views',
            'config_feature_view_versions','config_feature_groups','config_feature_group_versions',
            'config_feature_training_sets','config_feature_ml_links','config_semantic_models',
            'config_semantic_model_versions','config_semantic_verified_queries',
            'audit_semantic_query_usage'] LOOP
        kind:='append_only';
        IF tbl IN ('config_entities','config_feature_views','config_feature_view_versions',
                   'config_feature_groups','config_feature_group_versions',
                   'config_semantic_verified_queries') THEN
            kind:='mutable';
        END IF;
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.intelligence_write_guard(%L)',
            tbl||'_write_guard', tbl, kind);
    END LOOP;
END
$guards$;

-- ── Pin guard ───────────────────────────────────────────────────────────────
-- A Feature View version pinned by any Feature Group version cannot be removed,
-- whoever owns the group (the source checked this with an unlocked read).
CREATE FUNCTION nova_meta.feature_view_pinned(view text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
    SELECT EXISTS (SELECT 1 FROM nova_meta.config_feature_group_versions g
                   WHERE g.members @> jsonb_build_array(jsonb_build_object('view_name',view)))
$body$;
REVOKE ALL ON FUNCTION nova_meta.feature_view_pinned(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.feature_view_pinned(text) TO nova_meta_web;

-- ── Row guard ───────────────────────────────────────────────────────────────
-- Imported rows bypass it on insert; a later runtime change to an imported row
-- must still be a valid transition. One branch per table: a record field that
-- a table lacks cannot appear in an expression evaluated for that table.
CREATE FUNCTION nova_meta.intelligence_feature_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE actor text:=current_setting('nova.principal',true); member jsonb; group_entity text;
        set_group text; set_version integer; set_owner text;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='config_entities' THEN
        IF NEW.status NOT IN ('ACTIVE','DEPRECATED') OR btrim(NEW.name)=''
           OR jsonb_typeof(NEW.key_columns) IS DISTINCT FROM 'array'
           OR jsonb_array_length(NEW.key_columns)=0
           OR (NEW.tags IS NOT NULL AND jsonb_typeof(NEW.tags)<>'array') THEN
            RAISE EXCEPTION 'Invalid entity' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'ACTIVE' OR NEW.owner_name IS DISTINCT FROM actor THEN
                RAISE EXCEPTION 'An entity starts active, owned by its creator' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.id,NEW.catalog_name,NEW.database_name,NEW.schema_name,NEW.name,
               NEW.description,NEW.relation_name,NEW.key_columns,NEW.owner_name,NEW.tags,
               NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.id,OLD.catalog_name,OLD.database_name,OLD.schema_name,
               OLD.name,OLD.description,OLD.relation_name,OLD.key_columns,OLD.owner_name,
               OLD.tags,OLD.created_at,OLD.provenance)
           OR NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision+1
           OR NOT (OLD.status='ACTIVE' AND NEW.status='DEPRECATED') THEN
            -- An entity is only ever retired; its identity and keys never change.
            RAISE EXCEPTION 'Invalid entity change' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_feature_views' THEN
        IF TG_OP='DELETE' THEN
            -- Lock the view first: a concurrent group version that pins it holds
            -- a share lock on this row, so the check below sees its outcome.
            PERFORM 1 FROM nova_meta.config_feature_views f WHERE f.name=OLD.name FOR UPDATE;
            IF nova_meta.feature_view_pinned(OLD.name) THEN
                RAISE EXCEPTION 'Feature View is pinned by a group' USING ERRCODE='23503';
            END IF;
            RETURN OLD;
        END IF;
        IF NEW.status NOT IN ('BUILDING','ACTIVE','DEPRECATED') OR btrim(NEW.name)=''
           OR jsonb_typeof(NEW.entity_keys) IS DISTINCT FROM 'array'
           OR jsonb_typeof(NEW.feature_columns) IS DISTINCT FROM 'array'
           OR NEW.freshness_seconds<0 OR NEW.ttl_seconds<0 THEN
            RAISE EXCEPTION 'Invalid Feature View' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'BUILDING' OR NEW.active_version IS NOT NULL
               OR NEW.owner_name IS DISTINCT FROM actor THEN
                RAISE EXCEPTION 'A Feature View starts building, owned by its creator'
                    USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.name,NEW.id,NEW.entity_id,NEW.source_relation,NEW.entity_keys,
               NEW.event_timestamp,NEW.feature_columns,NEW.freshness_seconds,NEW.ttl_seconds,
               NEW.owner_name,NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.name,OLD.id,OLD.entity_id,OLD.source_relation,
               OLD.entity_keys,OLD.event_timestamp,OLD.feature_columns,OLD.freshness_seconds,
               OLD.ttl_seconds,OLD.owner_name,OLD.created_at,OLD.provenance)
           OR NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision+1
           OR (NEW.status='ACTIVE' AND NEW.active_version IS NULL) THEN
            RAISE EXCEPTION 'Invalid Feature View change' USING ERRCODE='23514';
        END IF;
        IF NEW.active_version IS NOT NULL AND NEW.active_version IS DISTINCT FROM OLD.active_version
           AND NOT EXISTS (SELECT 1 FROM nova_meta.config_feature_view_versions v
                           WHERE v.view_name=NEW.name AND v.version=NEW.active_version
                             AND v.status='ACTIVE') THEN
            RAISE EXCEPTION 'A Feature View can only serve an activated version' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_feature_view_versions' THEN
        IF TG_OP='DELETE' THEN
            IF EXISTS (SELECT 1 FROM nova_meta.config_feature_views WHERE name=OLD.view_name) THEN
                RAISE EXCEPTION 'Remove the Feature View before its versions' USING ERRCODE='23514';
            END IF;
            RETURN OLD;
        END IF;
        IF NEW.status NOT IN ('BUILDING','READY','FAILED','ACTIVE','DEPRECATED')
           OR jsonb_typeof(NEW.definition) IS DISTINCT FROM 'object' OR btrim(NEW.relation_name)='' THEN
            RAISE EXCEPTION 'Invalid Feature View version' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'BUILDING' THEN
                RAISE EXCEPTION 'A Feature View version starts building' USING ERRCODE='23514';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM nova_meta.config_feature_views WHERE name=NEW.view_name) THEN
                RAISE EXCEPTION 'Feature View not found' USING ERRCODE='23503';
            END IF;
            IF NEW.version IS DISTINCT FROM (SELECT coalesce(max(v.version),0)+1
                    FROM nova_meta.config_feature_view_versions v WHERE v.view_name=NEW.view_name) THEN
                RAISE EXCEPTION 'A newer Feature View version exists' USING ERRCODE='23505';
            END IF;
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp(); NEW.completed_at:=NULL;
            NEW.row_count:=0;
            RETURN NEW;
        END IF;
        IF ROW(NEW.view_name,NEW.version,NEW.relation_name,NEW.definition,NEW.created_at,
               NEW.provenance)
           IS DISTINCT FROM ROW(OLD.view_name,OLD.version,OLD.relation_name,OLD.definition,
               OLD.created_at,OLD.provenance) THEN
            RAISE EXCEPTION 'Feature View versions are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.status IN ('READY','FAILED') THEN
            IF OLD.status<>'BUILDING' THEN
                RAISE EXCEPTION 'Only a building version can finish' USING ERRCODE='23514';
            END IF;
            NEW.completed_at:=clock_timestamp();
        ELSIF NEW.status IN ('ACTIVE','DEPRECATED') THEN
            IF OLD.status NOT IN ('READY','ACTIVE','DEPRECATED')
               OR (NEW.status='DEPRECATED' AND OLD.status='READY')
               OR NEW.row_count IS DISTINCT FROM OLD.row_count THEN
                RAISE EXCEPTION 'Only a built version can be served' USING ERRCODE='23514';
            END IF;
            NEW.completed_at:=OLD.completed_at;
        ELSE
            RAISE EXCEPTION 'Invalid Feature View version transition' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='config_feature_groups' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.status NOT IN ('ACTIVE','DEPRECATED') OR btrim(NEW.name)='' THEN
            RAISE EXCEPTION 'Invalid Feature Group' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            -- Version 1 is created and activated right after the group, in the
            -- same transaction; the pointer then moves to it.
            IF NEW.status<>'ACTIVE' OR NEW.active_version IS NOT NULL
               OR NEW.owner_name IS DISTINCT FROM actor THEN
                RAISE EXCEPTION 'A Feature Group starts without a version, owned by its creator'
                    USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.name,NEW.id,NEW.entity_id,NEW.owner_name,NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.name,OLD.id,OLD.entity_id,OLD.owner_name,OLD.created_at,
               OLD.provenance)
           OR NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid Feature Group change' USING ERRCODE='23514';
        END IF;
        IF NEW.active_version IS NOT NULL AND NEW.active_version IS DISTINCT FROM OLD.active_version
           AND NOT EXISTS (SELECT 1 FROM nova_meta.config_feature_group_versions v
                           WHERE v.group_name=NEW.name AND v.version=NEW.active_version
                             AND v.status='ACTIVE') THEN
            RAISE EXCEPTION 'A Feature Group can only serve an activated version' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_feature_group_versions' THEN
        IF TG_OP='DELETE' THEN
            IF EXISTS (SELECT 1 FROM nova_meta.config_feature_groups WHERE name=OLD.group_name) THEN
                RAISE EXCEPTION 'Remove the Feature Group before its versions' USING ERRCODE='23514';
            END IF;
            RETURN OLD;
        END IF;
        IF NEW.status NOT IN ('DRAFT','ACTIVE','DEPRECATED')
           OR jsonb_typeof(NEW.members) IS DISTINCT FROM 'array'
           OR jsonb_array_length(NEW.members)=0 THEN
            RAISE EXCEPTION 'Invalid Feature Group version' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            SELECT g.entity_id INTO group_entity FROM nova_meta.config_feature_groups g
                WHERE g.name=NEW.group_name;
            IF group_entity IS NULL THEN
                RAISE EXCEPTION 'Feature Group not found' USING ERRCODE='23503';
            END IF;
            IF NEW.version IS DISTINCT FROM (SELECT coalesce(max(v.version),0)+1
                    FROM nova_meta.config_feature_group_versions v WHERE v.group_name=NEW.group_name) THEN
                RAISE EXCEPTION 'A newer Feature Group version exists' USING ERRCODE='23505';
            END IF;
            IF NOT (NEW.status='DRAFT' OR (NEW.status='ACTIVE' AND NEW.version=1)) THEN
                RAISE EXCEPTION 'A Feature Group version starts as a draft' USING ERRCODE='23514';
            END IF;
            -- Each member pins a built version of a Feature View of the group's
            -- entity. Locking the view row serializes this with DROP.
            FOR member IN SELECT value FROM jsonb_array_elements(NEW.members) LOOP
                IF jsonb_typeof(member->'view_name') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(member->'version') IS DISTINCT FROM 'number' THEN
                    RAISE EXCEPTION 'Invalid Feature Group member' USING ERRCODE='23514';
                END IF;
                PERFORM 1 FROM nova_meta.config_feature_views f
                    WHERE f.name=member->>'view_name' AND f.entity_id=group_entity FOR SHARE;
                IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM nova_meta.config_feature_view_versions v
                        WHERE v.view_name=member->>'view_name'
                          AND to_jsonb(v.version)=member->'version'
                          AND v.status IN ('READY','ACTIVE','DEPRECATED')) THEN
                    RAISE EXCEPTION 'Feature View version unavailable' USING ERRCODE='23503';
                END IF;
            END LOOP;
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
            NEW.activated_at:=NULL;
            IF NEW.status='ACTIVE' THEN NEW.activated_at:=NEW.created_at; END IF;
            RETURN NEW;
        END IF;
        IF ROW(NEW.group_name,NEW.version,NEW.members,NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.group_name,OLD.version,OLD.members,OLD.created_at,
               OLD.provenance)
           OR NEW.status='DRAFT' THEN
            RAISE EXCEPTION 'Feature Group versions are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.status='ACTIVE' AND OLD.status IS DISTINCT FROM 'ACTIVE' THEN
            NEW.activated_at:=clock_timestamp();
        ELSE
            NEW.activated_at:=OLD.activated_at;
        END IF;
    ELSIF TG_TABLE_NAME='config_feature_training_sets' THEN
        IF NEW.owner_name IS DISTINCT FROM actor OR jsonb_typeof(NEW.lineage) IS DISTINCT FROM 'object'
           OR btrim(NEW.generated_sql)='' THEN
            RAISE EXCEPTION 'Invalid training set' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM nova_meta.config_feature_group_versions v
                       WHERE v.group_name=NEW.group_name AND v.version=NEW.group_version
                         AND v.status IN ('ACTIVE','DEPRECATED')) THEN
            RAISE EXCEPTION 'Feature Group version unavailable' USING ERRCODE='23503';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_feature_ml_links' THEN
        IF jsonb_typeof(NEW.lineage) IS DISTINCT FROM 'object' OR NEW.model_version<1 THEN
            RAISE EXCEPTION 'Invalid model lineage' USING ERRCODE='23514';
        END IF;
        -- The training set is read under the caller's owner policy: a model can
        -- only be linked to the caller's own training set, at its group version.
        SELECT t.group_name,t.group_version,t.owner_name INTO set_group,set_version,set_owner
            FROM nova_meta.config_feature_training_sets t WHERE t.id=NEW.training_set_id;
        IF set_owner IS DISTINCT FROM actor OR set_group IS DISTINCT FROM NEW.group_name
           OR set_version IS DISTINCT FROM NEW.group_version THEN
            RAISE EXCEPTION 'Training set unavailable' USING ERRCODE='23503';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp(); NEW.created_by:=actor;
    ELSIF TG_TABLE_NAME='config_semantic_verified_queries' THEN
        -- Legacy rows: only the usage counters move, and only forward.
        IF TG_OP<>'UPDATE'
           OR ROW(NEW.verified_query_id,NEW.owner_name,NEW.semantic_model_id,NEW.model_fingerprint,
                  NEW.question,NEW.semantic_plan,NEW.verified_sql,NEW.expected_result_signature,
                  NEW.verified_by,NEW.verified_at,NEW.tags,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.verified_query_id,OLD.owner_name,OLD.semantic_model_id,
                  OLD.model_fingerprint,OLD.question,OLD.semantic_plan,OLD.verified_sql,
                  OLD.expected_result_signature,OLD.verified_by,OLD.verified_at,OLD.tags,
                  OLD.provenance)
           OR NEW.usage_count<OLD.usage_count OR NEW.success_count<OLD.success_count
           OR NEW.success_count-OLD.success_count>NEW.usage_count-OLD.usage_count THEN
            RAISE EXCEPTION 'Invalid verified query change' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='audit_semantic_query_usage' THEN
        IF NEW.owner_name IS DISTINCT FROM actor
           OR NEW.active_role IS DISTINCT FROM current_setting('nova.active_role',true)
           OR NEW.security_context_version::text
              IS DISTINCT FROM current_setting('nova.security_context_version',true) THEN
            RAISE EXCEPTION 'Semantic usage records the caller' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    ELSE
        RAISE EXCEPTION 'Legacy semantic models are read-only' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END
$body$;
DO $rows$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_entities','config_feature_groups',
            'config_feature_training_sets','config_feature_ml_links','config_semantic_models',
            'config_semantic_model_versions','config_semantic_verified_queries',
            'audit_semantic_query_usage'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.intelligence_feature_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
    FOREACH tbl IN ARRAY ARRAY['config_feature_views','config_feature_view_versions',
            'config_feature_group_versions'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.intelligence_feature_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$rows$;

-- ── Success audit for governed changes ──────────────────────────────────────
-- Entities, Feature View and Group definitions, versions and activation,
-- training sets and model lineage are governed, as in the source. Usage rows
-- and verified-query counters are not.
CREATE FUNCTION nova_meta.audit_intelligence_feature_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity text; revision bigint;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user<>'nova_meta_web' OR NOT nova_meta.actor_context_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('config_entities','config_feature_views',
            'config_feature_view_versions','config_feature_groups','config_feature_group_versions',
            'config_feature_training_sets','config_feature_ml_links') THEN
        RAISE EXCEPTION 'Invalid Intelligence audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='config_entities' THEN
        identity:=NEW.id; revision:=NEW.metadata_revision;
    ELSIF TG_TABLE_NAME='config_feature_views' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.name; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.name; revision:=NEW.metadata_revision; END IF;
    ELSIF TG_TABLE_NAME='config_feature_groups' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.name; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.name; revision:=NEW.metadata_revision; END IF;
    ELSIF TG_TABLE_NAME='config_feature_view_versions' THEN
        IF TG_OP='UPDATE' AND NOT (NEW.status IN ('ACTIVE','DEPRECATED')
                                   AND NEW.status IS DISTINCT FROM OLD.status) THEN
            RETURN NULL;  -- build results are execution, not governance
        END IF;
        IF TG_OP='DELETE' THEN identity:=OLD.view_name||':'||OLD.version; revision:=OLD.version;
        ELSE identity:=NEW.view_name||':'||NEW.version; revision:=NEW.version; END IF;
    ELSIF TG_TABLE_NAME='config_feature_group_versions' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.group_name||':'||OLD.version; revision:=OLD.version;
        ELSE identity:=NEW.group_name||':'||NEW.version; revision:=NEW.version; END IF;
    ELSE
        identity:=NEW.id; revision:=1;
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,identity,
        CASE TG_OP WHEN 'INSERT' THEN 'CREATE' WHEN 'DELETE' THEN 'DELETE' ELSE 'UPDATE' END,
        'SUCCESS','ALLOW',TG_TABLE_NAME,jsonb_build_object('id',identity),coalesce(revision,1));
    RETURN NULL;
END
$body$;
DO $audit$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_entities','config_feature_training_sets',
            'config_feature_ml_links'] LOOP
        EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_intelligence_feature_change()',
            tbl||'_audit', tbl);
    END LOOP;
    FOREACH tbl IN ARRAY ARRAY['config_feature_views','config_feature_view_versions',
            'config_feature_groups','config_feature_group_versions'] LOOP
        EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_intelligence_feature_change()',
            tbl||'_audit', tbl);
    END LOOP;
END
$audit$;
