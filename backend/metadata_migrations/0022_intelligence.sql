-- Intelligence control metadata: Semantic Views and their versions, the
-- append-only Intelligence and Business Event revision stores, News reader
-- feedback and AI Search index metadata.
-- Rows carry provenance: imported source rows are kept as they were (including
-- same-revision branches), while lifecycle rules apply to rows written at
-- runtime. Vector/full-text index payloads stay with their engine owner, and
-- News access proofs stay in Redis; only control metadata lives here.

-- ── Semantic Views ──────────────────────────────────────────────────────────
CREATE TABLE nova_meta.config_semantic_views (
    catalog_name varchar(128) NOT NULL CHECK (octet_length(catalog_name)<=128),
    database_name varchar(128) NOT NULL CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) NOT NULL CHECK (octet_length(schema_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    id varchar(64) NOT NULL UNIQUE CHECK (octet_length(id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    visibility varchar(16) CHECK (octet_length(visibility)<=16),
    active_version integer,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    news_enabled boolean,
    news_config jsonb,
    news_updated_by varchar(128) CHECK (octet_length(news_updated_by)<=128),
    news_updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (catalog_name,database_name,schema_name,name)
);
CREATE INDEX config_semantic_views_news ON nova_meta.config_semantic_views (name)
    WHERE news_enabled;

CREATE TABLE nova_meta.config_semantic_view_versions (
    view_id varchar(64) NOT NULL CHECK (octet_length(view_id)<=64),
    version integer NOT NULL CHECK (version>0),
    definition jsonb NOT NULL,
    fingerprint varchar(64) NOT NULL CHECK (octet_length(fingerprint)<=64),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    validation jsonb,
    created_at timestamptz NOT NULL,
    validated_at timestamptz,
    activated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (view_id,version)
);

-- ── Revision stores ─────────────────────────────────────────────────────────
-- The source key (id, revision, operation_id) is kept so imported same-revision
-- branches survive for reconciliation (Wave E). Runtime revisions are unique per
-- id and must extend the newest revision by exactly one.
DO $tables$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_context_nodes','principal'),('config_context_edges','principal'),
        ('config_intelligence_monitors','principal'),('config_intelligence_observations','principal'),
        ('config_intelligence_news','principal'),('config_intelligence_investigations','principal'),
        ('config_intelligence_decisions','principal'),('config_decision_events','principal'),
        ('config_decision_outcomes','principal'),('config_intelligence_actions','principal'),
        ('config_intelligence_action_events','principal'),('config_intelligence_comparisons','principal'),
        ('config_intelligence_editions','principal'),('config_intelligence_stories','principal'),
        ('config_intelligence_signals','actor'),('config_intelligence_incidents','actor'),
        ('config_intelligence_investigation_updates','actor')
    ) AS t(tbl,writer) LOOP
        EXECUTE format($ddl$CREATE TABLE nova_meta.%1$I (
            id varchar(64) NOT NULL CHECK (octet_length(id)<=64),
            revision bigint NOT NULL CHECK (revision>0),
            operation_id varchar(32) NOT NULL CHECK (octet_length(operation_id)<=32),
            %2$I varchar(128) NOT NULL CHECK (octet_length(%2$I)<=128),
            active_role varchar(128) NOT NULL CHECK (octet_length(active_role)<=128),
            security_context_version bigint NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            written_by varchar(128) CHECK (octet_length(written_by)<=128),
            provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
            PRIMARY KEY (id,revision,operation_id))$ddl$, item.tbl, item.writer);
        EXECUTE format('CREATE UNIQUE INDEX %I ON nova_meta.%I (id,revision) WHERE provenance=''runtime''',
            item.tbl||'_runtime_revision', item.tbl);
        EXECUTE format('CREATE INDEX %I ON nova_meta.%I (%I,active_role,id)',
            item.tbl||'_scope', item.tbl, item.writer);
    END LOOP;
END
$tables$;

-- ── News reader feedback ────────────────────────────────────────────────────
CREATE TABLE nova_meta.config_intelligence_story_feedback (
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    story_id varchar(64) NOT NULL CHECK (octet_length(story_id)<=64),
    reaction varchar(8) NOT NULL CHECK (octet_length(reaction)<=8),
    features jsonb NOT NULL,
    updated_at timestamptz NOT NULL,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (user_name,story_id)
);

-- ── AI Search ───────────────────────────────────────────────────────────────
CREATE TABLE nova_meta.config_ai_search_indexes (
    name varchar(128) PRIMARY KEY CHECK (octet_length(name)<=128),
    id varchar(64) NOT NULL UNIQUE CHECK (octet_length(id)<=64),
    source_relation varchar(512) NOT NULL CHECK (octet_length(source_relation)<=512),
    key_columns jsonb NOT NULL,
    content_columns jsonb NOT NULL,
    filter_columns jsonb NOT NULL,
    entity_id varchar(64) CHECK (octet_length(entity_id)<=64),
    model_alias varchar(128) CHECK (octet_length(model_alias)<=128),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    active_version integer,
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_ai_search_versions (
    index_name varchar(128) NOT NULL CHECK (octet_length(index_name)<=128),
    version integer NOT NULL CHECK (version>0),
    index_id varchar(64) NOT NULL CHECK (octet_length(index_id)<=64),
    model_id varchar(64) CHECK (octet_length(model_id)<=64),
    model_revision varchar(128) CHECK (octet_length(model_revision)<=128),
    dimensions integer NOT NULL CHECK (dimensions>=0),
    metric varchar(32) NOT NULL CHECK (octet_length(metric)<=32),
    build_status varchar(32) NOT NULL CHECK (octet_length(build_status)<=32),
    indexed_rows bigint NOT NULL DEFAULT 0 CHECK (indexed_rows>=0),
    failure_code varchar(64) CHECK (octet_length(failure_code)<=64),
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    build_generation bigint NOT NULL DEFAULT 0 CHECK (build_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (index_name,version)
);
CREATE INDEX config_ai_search_versions_pending ON nova_meta.config_ai_search_versions
    (index_name,version) WHERE build_status IN ('PENDING','BUILDING','AUTO_PENDING','AUTO_BUILDING');

CREATE TABLE nova_meta.config_ai_search_sync_state (
    index_name varchar(128) NOT NULL CHECK (octet_length(index_name)<=128),
    version integer NOT NULL CHECK (version>0),
    source_rows bigint NOT NULL DEFAULT 0 CHECK (source_rows>=0),
    indexed_rows bigint NOT NULL DEFAULT 0 CHECK (indexed_rows>=0),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    last_watermark varchar(128) CHECK (octet_length(last_watermark)<=128),
    updated_at timestamptz NOT NULL,
    build_generation bigint NOT NULL DEFAULT 0 CHECK (build_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (index_name,version)
);

-- The source is a DUPLICATE KEY table: several rows can share an id, so each
-- row gets its own record_id and the source id is kept as written.
CREATE TABLE nova_meta.ai_search_query_log (
    record_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    id varchar(64) NOT NULL CHECK (octet_length(id)<=64),
    index_name varchar(128) NOT NULL CHECK (octet_length(index_name)<=128),
    index_version integer NOT NULL,
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    mode varchar(16) NOT NULL CHECK (octet_length(mode)<=16),
    elapsed_ms bigint NOT NULL CHECK (elapsed_ms>=0),
    lexical_candidates integer NOT NULL CHECK (lexical_candidates>=0),
    semantic_candidates integer NOT NULL CHECK (semantic_candidates>=0),
    result_count integer NOT NULL CHECK (result_count>=0),
    status varchar(16) NOT NULL CHECK (octet_length(status)<=16),
    created_at timestamptz NOT NULL,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX ai_search_query_log_user ON nova_meta.ai_search_query_log (user_name,created_at DESC);
CREATE INDEX ai_search_query_log_index ON nova_meta.ai_search_query_log (index_name,created_at DESC);

CREATE TABLE nova_meta.ai_search_eval_runs (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    index_name varchar(128) NOT NULL CHECK (octet_length(index_name)<=128),
    index_version integer NOT NULL,
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    mode varchar(16) NOT NULL CHECK (octet_length(mode)<=16),
    query_count integer NOT NULL CHECK (query_count>=0),
    metrics jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX ai_search_eval_runs_index ON nova_meta.ai_search_eval_runs (index_name,created_at DESC);

-- ── Access ──────────────────────────────────────────────────────────────────
-- Intelligence metadata is control-plane state. Whether a caller may see a
-- Semantic View, story or decision is the owning service's (and the engine's)
-- check; PG binds the workload and actor, and isolates rows by owner only where
-- the source scopes them to one principal.
CREATE FUNCTION nova_meta.intelligence_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;
REVOKE ALL ON FUNCTION nova_meta.intelligence_actor_bound() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.intelligence_actor_bound()
    TO nova_meta_web,nova_meta_agent,nova_meta_automation,nova_meta_task,nova_meta_maintenance;

DO $policies$
DECLARE item record; tbl text;
BEGIN
    -- predicate: bound = the workload and actor are bound; owner = also the row's
    -- principal and active role; reader = the row belongs to the bound principal.
    FOR item IN SELECT * FROM (VALUES
        ('config_semantic_views','web,agent,automation,task,maintenance','web,agent','web,agent','web,agent','bound','bound'),
        ('config_semantic_view_versions','web,agent,automation,task,maintenance','web,agent','web,agent','web,agent','bound','bound'),
        ('config_context_nodes','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_context_edges','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_monitors','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_observations','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_news','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_investigations','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_decisions','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_decision_events','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_decision_outcomes','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_actions','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_action_events','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_comparisons','web,agent,automation,task','web,agent,automation,task','','','owner','bound'),
        ('config_intelligence_editions','web,agent,task,maintenance','web,task','','','bound','bound'),
        ('config_intelligence_stories','web,agent,task,maintenance','web,task','','','bound','bound'),
        ('config_intelligence_signals','web,agent,automation,task','web,agent,automation,task','','','bound','bound'),
        ('config_intelligence_incidents','web,agent,automation,task','web,agent,automation,task','','','bound','bound'),
        ('config_intelligence_investigation_updates','web,agent,automation,task','web,agent,automation,task','','','bound','bound'),
        ('config_intelligence_story_feedback','web','web','web','web','reader','reader'),
        ('config_ai_search_indexes','web,agent,maintenance','web','web','','bound','bound'),
        ('config_ai_search_versions','web,agent,maintenance','web,maintenance','web,maintenance','','bound','bound'),
        ('config_ai_search_sync_state','web,agent,maintenance','web,maintenance','maintenance','','bound','bound'),
        ('ai_search_query_log','web,agent','web,agent','','','reader','reader'),
        ('ai_search_eval_runs','web','web','','','reader','reader')
    ) AS t(tbl,readers,inserters,updaters,deleters,read_rule,write_rule) LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY', item.tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY', item.tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO %s USING (%s)',
            item.tbl||'_read', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r),
            CASE item.read_rule
                WHEN 'owner' THEN 'nova_meta.intelligence_actor_bound() AND principal=current_setting(''nova.principal'',true) AND active_role=current_setting(''nova.active_role'',true)'
                WHEN 'reader' THEN 'nova_meta.intelligence_actor_bound() AND user_name=current_setting(''nova.principal'',true)'
                ELSE 'nova_meta.intelligence_actor_bound()' END);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO %s WITH CHECK (%s)',
            item.tbl||'_insert', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r),
            CASE item.write_rule
                WHEN 'reader' THEN 'nova_meta.intelligence_actor_bound() AND user_name=current_setting(''nova.principal'',true)'
                ELSE 'nova_meta.intelligence_actor_bound()' END);
        IF item.deleters<>'' THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR DELETE TO %s USING (%s)',
                item.tbl||'_delete', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.deleters,',')) r),
                CASE item.write_rule
                    WHEN 'reader' THEN 'nova_meta.intelligence_actor_bound() AND user_name=current_setting(''nova.principal'',true)'
                    ELSE 'nova_meta.intelligence_actor_bound()' END);
            EXECUTE format('GRANT DELETE ON nova_meta.%I TO %s', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.deleters,',')) r));
        END IF;
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            item.tbl||'_import', item.tbl);
        EXECUTE format('GRANT SELECT ON nova_meta.%I TO %s', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
    END LOOP;
    -- The revision guard and the two shared lookups below run as the owner and
    -- must see every revision of an id; runtime logins cannot assume the owner.
    FOREACH tbl IN ARRAY ARRAY['config_context_nodes','config_context_edges',
            'config_intelligence_monitors','config_intelligence_observations','config_intelligence_news',
            'config_intelligence_investigations','config_intelligence_decisions','config_decision_events',
            'config_decision_outcomes','config_intelligence_actions','config_intelligence_action_events',
            'config_intelligence_comparisons','config_intelligence_editions','config_intelligence_stories',
            'config_intelligence_signals','config_intelligence_incidents',
            'config_intelligence_investigation_updates'] LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_owner USING (true)',
            tbl||'_owner_read', tbl);
    END LOOP;
END
$policies$;

-- Update policies, for the tables whose rows change after insert.
DO $update_policies$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_semantic_views','nova_meta_web,nova_meta_agent','nova_meta.intelligence_actor_bound()'),
        ('config_semantic_view_versions','nova_meta_web,nova_meta_agent','nova_meta.intelligence_actor_bound()'),
        ('config_intelligence_story_feedback','nova_meta_web','nova_meta.intelligence_actor_bound() AND user_name=current_setting(''nova.principal'',true)'),
        ('config_ai_search_indexes','nova_meta_web','nova_meta.intelligence_actor_bound()'),
        ('config_ai_search_versions','nova_meta_web,nova_meta_maintenance','nova_meta.intelligence_actor_bound()'),
        ('config_ai_search_sync_state','nova_meta_maintenance','nova_meta.intelligence_actor_bound()')
    ) AS t(tbl,roles,predicate) LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO %s USING (%s) WITH CHECK (%s)',
            item.tbl||'_update', item.tbl, item.roles, item.predicate, item.predicate);
    END LOOP;
END
$update_policies$;

GRANT INSERT (catalog_name,database_name,schema_name,name,id,owner_name,visibility,status)
    ON nova_meta.config_semantic_views TO nova_meta_web,nova_meta_agent;
GRANT UPDATE (visibility,active_version,status,news_enabled,news_config,metadata_revision)
    ON nova_meta.config_semantic_views TO nova_meta_web,nova_meta_agent;
GRANT INSERT (view_id,version,definition,fingerprint,status)
    ON nova_meta.config_semantic_view_versions TO nova_meta_web,nova_meta_agent;
GRANT UPDATE (status,validation) ON nova_meta.config_semantic_view_versions
    TO nova_meta_web,nova_meta_agent;
DO $revision_grants$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_context_nodes','principal','web,agent,automation,task'),
        ('config_context_edges','principal','web,agent,automation,task'),
        ('config_intelligence_monitors','principal','web,agent,automation,task'),
        ('config_intelligence_observations','principal','web,agent,automation,task'),
        ('config_intelligence_news','principal','web,agent,automation,task'),
        ('config_intelligence_investigations','principal','web,agent,automation,task'),
        ('config_intelligence_decisions','principal','web,agent,automation,task'),
        ('config_decision_events','principal','web,agent,automation,task'),
        ('config_decision_outcomes','principal','web,agent,automation,task'),
        ('config_intelligence_actions','principal','web,agent,automation,task'),
        ('config_intelligence_action_events','principal','web,agent,automation,task'),
        ('config_intelligence_comparisons','principal','web,agent,automation,task'),
        ('config_intelligence_editions','principal','web,task'),
        ('config_intelligence_stories','principal','web,task'),
        ('config_intelligence_signals','actor','web,agent,automation,task'),
        ('config_intelligence_incidents','actor','web,agent,automation,task'),
        ('config_intelligence_investigation_updates','actor','web,agent,automation,task')
    ) AS t(tbl,writer,roles) LOOP
        EXECUTE format('GRANT INSERT (id,revision,operation_id,%I,active_role,security_context_version,payload) ON nova_meta.%I TO %s',
            item.writer, item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.roles,',')) r));
    END LOOP;
END
$revision_grants$;
GRANT INSERT (user_name,story_id,reaction,features),UPDATE (reaction,features)
    ON nova_meta.config_intelligence_story_feedback TO nova_meta_web;
GRANT INSERT (name,id,source_relation,key_columns,content_columns,filter_columns,entity_id,
        model_alias,owner_name,status),
    UPDATE (model_alias,active_version,status,metadata_revision)
    ON nova_meta.config_ai_search_indexes TO nova_meta_web;
GRANT INSERT (index_name,version,index_id,model_id,model_revision,dimensions,metric,build_status),
    UPDATE (build_status,indexed_rows,failure_code,build_generation)
    ON nova_meta.config_ai_search_versions TO nova_meta_web,nova_meta_maintenance;
GRANT INSERT (index_name,version,status) ON nova_meta.config_ai_search_sync_state
    TO nova_meta_web,nova_meta_maintenance;
GRANT UPDATE (source_rows,indexed_rows,status,last_watermark,build_generation)
    ON nova_meta.config_ai_search_sync_state TO nova_meta_maintenance;
GRANT INSERT (id,index_name,index_version,user_name,mode,elapsed_ms,lexical_candidates,
        semantic_candidates,result_count,status)
    ON nova_meta.ai_search_query_log TO nova_meta_web,nova_meta_agent;
GRANT INSERT (id,index_name,index_version,user_name,mode,query_count,metrics)
    ON nova_meta.ai_search_eval_runs TO nova_meta_web;

-- Two shared lookups the owning services authorize themselves (Wave E): a
-- decision named together with its owner, and an action for its reviewer. They
-- return only the two newest revisions of that one id, so a caller can detect a
-- branch; they are not a general read past the owner policy.
CREATE FUNCTION nova_meta.intelligence_shared_revisions(lookup text, record_id text, owner text)
RETURNS TABLE(payload jsonb, revision bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
#variable_conflict use_column
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_agent','nova_meta_automation','nova_meta_task')
       OR nova_meta.actor_context_valid() IS DISTINCT FROM true
       OR current_setting('nova.service_identity',true) IS DISTINCT FROM session_user THEN
        RAISE EXCEPTION 'Intelligence lookup is not authorized' USING ERRCODE='42501';
    END IF;
    IF lookup='decision' AND owner IS NOT NULL THEN
        RETURN QUERY SELECT d.payload,d.revision FROM nova_meta.config_intelligence_decisions d
            WHERE d.id=record_id AND d.principal=owner ORDER BY d.revision DESC LIMIT 2;
    ELSIF lookup='action_review' THEN
        RETURN QUERY SELECT a.payload,a.revision FROM nova_meta.config_intelligence_actions a
            WHERE a.id=record_id ORDER BY a.revision DESC LIMIT 2;
    ELSE
        RAISE EXCEPTION 'Unsupported Intelligence lookup' USING ERRCODE='22023';
    END IF;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.intelligence_shared_revisions(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.intelligence_shared_revisions(text,text,text)
    TO nova_meta_web,nova_meta_agent,nova_meta_automation,nova_meta_task;

-- ── Statement write guard ───────────────────────────────────────────────────
CREATE FUNCTION nova_meta.intelligence_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Intelligence import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation','nova_meta_task',
                           'nova_meta_maintenance') AND TG_OP IN ('INSERT','UPDATE','DELETE') THEN
        IF TG_OP<>'INSERT' AND TG_ARGV[0]='append_only' THEN
            RAISE EXCEPTION 'Intelligence history is append-only' USING ERRCODE='42501';
        END IF;
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Intelligence metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
DO $guards$
DECLARE tbl text; kind text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_semantic_views','config_semantic_view_versions',
            'config_context_nodes','config_context_edges','config_intelligence_monitors',
            'config_intelligence_observations','config_intelligence_news',
            'config_intelligence_investigations','config_intelligence_decisions',
            'config_decision_events','config_decision_outcomes','config_intelligence_actions',
            'config_intelligence_action_events','config_intelligence_comparisons',
            'config_intelligence_editions','config_intelligence_stories',
            'config_intelligence_signals','config_intelligence_incidents',
            'config_intelligence_investigation_updates','config_intelligence_story_feedback',
            'config_ai_search_indexes','config_ai_search_versions','config_ai_search_sync_state',
            'ai_search_query_log','ai_search_eval_runs'] LOOP
        kind:=CASE WHEN tbl IN ('config_semantic_views','config_semantic_view_versions',
                    'config_intelligence_story_feedback','config_ai_search_indexes',
                    'config_ai_search_versions','config_ai_search_sync_state')
                   THEN 'mutable' ELSE 'append_only' END;
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.intelligence_write_guard(%L)',
            tbl||'_write_guard', tbl, kind);
    END LOOP;
END
$guards$;

-- ── Revision row guard ──────────────────────────────────────────────────────
-- Runs as the owner so it sees every revision of an id, whatever its scope: a
-- runtime revision must extend the newest one by exactly one, cannot extend a
-- branched (forked) revision, and a scoped record cannot move to another
-- principal, role or security version (a guessed id must not overwrite another
-- scope's record).
CREATE FUNCTION nova_meta.intelligence_revision_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE latest bigint; branches bigint; prior record;
BEGIN
    IF session_user='nova_meta_migrator' THEN
        NEW.provenance:='imported';
        RETURN NEW;
    END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_agent','nova_meta_automation','nova_meta_task')
       OR nova_meta.actor_context_valid() IS DISTINCT FROM true OR TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Intelligence revision write is not authorized' USING ERRCODE='42501';
    END IF;
    IF btrim(NEW.operation_id)='' OR jsonb_typeof(NEW.payload) IS DISTINCT FROM 'object'
       OR NEW.payload->>'id' IS DISTINCT FROM NEW.id
       OR NEW.payload->'revision' IS DISTINCT FROM to_jsonb(NEW.revision) THEN
        RAISE EXCEPTION 'Invalid Intelligence revision' USING ERRCODE='23514';
    END IF;
    -- One branch per table shape: a record field that a table lacks cannot
    -- appear in an expression evaluated for that table.
    IF TG_TABLE_NAME IN ('config_intelligence_signals','config_intelligence_incidents',
                         'config_intelligence_investigation_updates') THEN
        -- Deployment-wide records: the actor is the writer's provenance.
        IF NEW.actor IS DISTINCT FROM current_setting('nova.principal',true)
           OR NEW.active_role IS DISTINCT FROM current_setting('nova.active_role',true)
           OR NEW.security_context_version::text
              IS DISTINCT FROM current_setting('nova.security_context_version',true) THEN
            RAISE EXCEPTION 'A Business Event revision records its writer' USING ERRCODE='42501';
        END IF;
        IF NEW.payload->'actor'->>'principal' IS DISTINCT FROM NEW.actor THEN
            RAISE EXCEPTION 'Invalid Intelligence revision' USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.payload->'scope'->>'principal' IS DISTINCT FROM NEW.principal
           OR NEW.payload->'scope'->>'active_role' IS DISTINCT FROM NEW.active_role
           OR NEW.payload->'scope'->'security_context_version'
              IS DISTINCT FROM to_jsonb(NEW.security_context_version) THEN
            RAISE EXCEPTION 'Invalid Intelligence revision' USING ERRCODE='23514';
        END IF;
    END IF;
    EXECUTE format('SELECT revision,count(*) FROM nova_meta.%I WHERE id=$1 GROUP BY revision '
                   'ORDER BY revision DESC LIMIT 1', TG_TABLE_NAME)
        INTO latest, branches USING NEW.id;
    IF NEW.revision IS DISTINCT FROM coalesce(latest,0)+1 THEN
        RAISE EXCEPTION 'Record changed; reload before editing' USING ERRCODE='23505';
    END IF;
    IF branches>1 THEN
        RAISE EXCEPTION 'Concurrent revisions require reconciliation' USING ERRCODE='23505';
    END IF;
    IF latest IS NOT NULL AND TG_TABLE_NAME NOT IN ('config_intelligence_signals',
            'config_intelligence_incidents','config_intelligence_investigation_updates') THEN
        EXECUTE format('SELECT principal,active_role,security_context_version FROM nova_meta.%I '
                       'WHERE id=$1 AND revision=$2 LIMIT 1', TG_TABLE_NAME)
            INTO prior USING NEW.id, latest;
        IF ROW(prior.principal,prior.active_role,prior.security_context_version)
           IS DISTINCT FROM ROW(NEW.principal,NEW.active_role,NEW.security_context_version) THEN
            RAISE EXCEPTION 'Record unavailable' USING ERRCODE='42501';
        END IF;
    END IF;
    NEW.provenance:='runtime';
    NEW.created_at:=clock_timestamp();
    NEW.written_by:=current_setting('nova.principal',true);
    RETURN NEW;
END
$body$;
DO $revision_rows$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_context_nodes','config_context_edges',
            'config_intelligence_monitors','config_intelligence_observations','config_intelligence_news',
            'config_intelligence_investigations','config_intelligence_decisions','config_decision_events',
            'config_decision_outcomes','config_intelligence_actions','config_intelligence_action_events',
            'config_intelligence_comparisons','config_intelligence_editions','config_intelligence_stories',
            'config_intelligence_signals','config_intelligence_incidents',
            'config_intelligence_investigation_updates'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.intelligence_revision_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$revision_rows$;

-- ── Row guard for Semantic Views, feedback and Search ───────────────────────
-- Imported rows bypass it on insert; a later runtime change to an imported row
-- must still be a valid transition.
CREATE FUNCTION nova_meta.intelligence_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE actor text:=current_setting('nova.principal',true); current_generation bigint;
        version_status text;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='config_semantic_views' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.status NOT IN ('DRAFT','ACTIVE','DEPRECATED')
           OR coalesce(NEW.visibility,'PUBLIC') NOT IN ('PUBLIC','PRIVATE')
           OR btrim(NEW.name)='' OR btrim(NEW.id)=''
           OR (NEW.news_config IS NOT NULL AND jsonb_typeof(NEW.news_config)<>'object') THEN
            RAISE EXCEPTION 'Invalid Semantic View' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' OR NEW.active_version IS NOT NULL
               OR NEW.owner_name IS DISTINCT FROM actor
               OR NEW.news_enabled IS NOT NULL OR NEW.news_config IS NOT NULL THEN
                RAISE EXCEPTION 'A Semantic View starts as its creator''s draft' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            NEW.news_updated_by:=NULL; NEW.news_updated_at:=NULL;
            RETURN NEW;
        END IF;
        IF ROW(NEW.id,NEW.catalog_name,NEW.database_name,NEW.schema_name,NEW.name,NEW.owner_name,
               NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.id,OLD.catalog_name,OLD.database_name,OLD.schema_name,OLD.name,
               OLD.owner_name,OLD.created_at,OLD.provenance) THEN
            RAISE EXCEPTION 'Invalid Semantic View change' USING ERRCODE='23514';
        END IF;
        IF NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'A Semantic View change needs the next revision' USING ERRCODE='23514';
        END IF;
        IF (NEW.status='ACTIVE') IS DISTINCT FROM (NEW.active_version IS NOT NULL) THEN
            RAISE EXCEPTION 'Only an active Semantic View has an active version' USING ERRCODE='23514';
        END IF;
        IF NEW.active_version IS NOT NULL AND NEW.active_version IS DISTINCT FROM OLD.active_version
           AND NOT EXISTS (SELECT 1 FROM nova_meta.config_semantic_view_versions v
                           WHERE v.view_id=NEW.id AND v.version=NEW.active_version
                             AND v.status='ACTIVE') THEN
            RAISE EXCEPTION 'A Semantic View can only point to a published version'
                USING ERRCODE='23514';
        END IF;
        IF ROW(NEW.news_enabled,NEW.news_config) IS DISTINCT FROM ROW(OLD.news_enabled,OLD.news_config)
        THEN
            NEW.news_updated_by:=actor; NEW.news_updated_at:=clock_timestamp();
        ELSE
            NEW.news_updated_by:=OLD.news_updated_by; NEW.news_updated_at:=OLD.news_updated_at;
        END IF;
        IF ROW(NEW.status,NEW.active_version,NEW.visibility)
           IS DISTINCT FROM ROW(OLD.status,OLD.active_version,OLD.visibility) THEN
            NEW.updated_at:=clock_timestamp();
        ELSE
            NEW.updated_at:=OLD.updated_at;
        END IF;
    ELSIF TG_TABLE_NAME='config_semantic_view_versions' THEN
        IF TG_OP='DELETE' THEN
            -- Versions go only with their view (DROP removes the view first), so a
            -- published version can never disappear under a live view.
            IF EXISTS (SELECT 1 FROM nova_meta.config_semantic_views WHERE id=OLD.view_id) THEN
                RAISE EXCEPTION 'Remove the Semantic View before its versions' USING ERRCODE='23514';
            END IF;
            RETURN OLD;
        END IF;
        IF NEW.status NOT IN ('DRAFT','VALIDATED','ACTIVE','DEPRECATED')
           OR jsonb_typeof(NEW.definition) IS DISTINCT FROM 'object' OR btrim(NEW.fingerprint)=''
           OR (NEW.validation IS NOT NULL AND jsonb_typeof(NEW.validation)<>'object') THEN
            RAISE EXCEPTION 'Invalid Semantic View version' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' OR NEW.validation IS NOT NULL THEN
                RAISE EXCEPTION 'A Semantic View version starts as a draft' USING ERRCODE='23514';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM nova_meta.config_semantic_views WHERE id=NEW.view_id) THEN
                RAISE EXCEPTION 'Semantic View not found' USING ERRCODE='23503';
            END IF;
            IF NEW.version IS DISTINCT FROM (SELECT coalesce(max(v.version),0)+1
                    FROM nova_meta.config_semantic_view_versions v WHERE v.view_id=NEW.view_id) THEN
                RAISE EXCEPTION 'A newer Semantic View version exists' USING ERRCODE='23505';
            END IF;
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
            NEW.validated_at:=NULL; NEW.activated_at:=NULL;
            RETURN NEW;
        END IF;
        IF ROW(NEW.view_id,NEW.version,NEW.definition,NEW.fingerprint,NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.view_id,OLD.version,OLD.definition,OLD.fingerprint,OLD.created_at,
               OLD.provenance) THEN
            RAISE EXCEPTION 'Semantic View versions are immutable' USING ERRCODE='23514';
        END IF;
        IF OLD.status IN ('ACTIVE','DEPRECATED') THEN
            -- A published version keeps its definition, validation proof and
            -- activation time; it can only be retired.
            IF ROW(NEW.validation,NEW.validated_at,NEW.activated_at)
               IS DISTINCT FROM ROW(OLD.validation,OLD.validated_at,OLD.activated_at)
               OR NOT (NEW.status=OLD.status OR (OLD.status='ACTIVE' AND NEW.status='DEPRECATED')) THEN
                RAISE EXCEPTION 'Published Semantic View versions are immutable' USING ERRCODE='23514';
            END IF;
        ELSIF NEW.status='ACTIVE' THEN
            IF OLD.status<>'VALIDATED' OR NEW.validation IS DISTINCT FROM OLD.validation
               OR OLD.validation->'valid' IS DISTINCT FROM 'true'::jsonb THEN
                RAISE EXCEPTION 'Only validated versions may be published' USING ERRCODE='23514';
            END IF;
            NEW.validated_at:=OLD.validated_at; NEW.activated_at:=clock_timestamp();
        ELSIF NEW.status='DEPRECATED' THEN
            RAISE EXCEPTION 'Only a published version can be retired' USING ERRCODE='23514';
        ELSE
            IF NEW.status='VALIDATED' AND NEW.validation->'valid' IS DISTINCT FROM 'true'::jsonb THEN
                RAISE EXCEPTION 'A validated version needs a passing report' USING ERRCODE='23514';
            END IF;
            NEW.validated_at:=CASE WHEN ROW(NEW.validation,NEW.status)
                IS DISTINCT FROM ROW(OLD.validation,OLD.status) THEN clock_timestamp()
                ELSE OLD.validated_at END;
            NEW.activated_at:=OLD.activated_at;
        END IF;
    ELSIF TG_TABLE_NAME='config_intelligence_story_feedback' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.reaction NOT IN ('like','dislike') OR jsonb_typeof(NEW.features) IS DISTINCT FROM 'object'
           OR NEW.user_name IS DISTINCT FROM actor THEN
            RAISE EXCEPTION 'Invalid story feedback' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime';
        ELSIF ROW(NEW.user_name,NEW.story_id,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.user_name,OLD.story_id,OLD.provenance) THEN
            RAISE EXCEPTION 'Invalid story feedback change' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_ai_search_indexes' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.status NOT IN ('BUILDING','ACTIVE','DEPRECATED') OR btrim(NEW.name)=''
           OR jsonb_typeof(NEW.key_columns) IS DISTINCT FROM 'array'
           OR jsonb_typeof(NEW.content_columns) IS DISTINCT FROM 'array'
           OR jsonb_typeof(NEW.filter_columns) IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Invalid search index' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'BUILDING' OR NEW.active_version IS NOT NULL
               OR NEW.owner_name IS DISTINCT FROM actor THEN
                RAISE EXCEPTION 'A search index starts building, owned by its creator'
                    USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.name,NEW.id,NEW.source_relation,NEW.key_columns,NEW.content_columns,
               NEW.filter_columns,NEW.entity_id,NEW.owner_name,NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.name,OLD.id,OLD.source_relation,OLD.key_columns,
               OLD.content_columns,OLD.filter_columns,OLD.entity_id,OLD.owner_name,OLD.created_at,
               OLD.provenance)
           OR NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision+1
           OR (NEW.status='ACTIVE' AND NEW.active_version IS NULL) THEN
            RAISE EXCEPTION 'Invalid search index change' USING ERRCODE='23514';
        END IF;
        IF NEW.active_version IS NOT NULL AND NEW.active_version IS DISTINCT FROM OLD.active_version
           AND NOT EXISTS (SELECT 1 FROM nova_meta.config_ai_search_versions v
                           WHERE v.index_name=NEW.name AND v.version=NEW.active_version
                             AND v.build_status='ACTIVE') THEN
            RAISE EXCEPTION 'A search index can only serve an activated version' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_ai_search_versions' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.build_status NOT IN ('PENDING','BUILDING','AUTO_PENDING','AUTO_BUILDING','READY',
                                    'FAILED','ACTIVE','DEPRECATED')
           OR btrim(NEW.metric)='' OR NEW.dimensions>65536 THEN
            RAISE EXCEPTION 'Invalid search version' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.build_status NOT IN ('PENDING','AUTO_PENDING') THEN
                RAISE EXCEPTION 'A search version starts pending' USING ERRCODE='23514';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM nova_meta.config_ai_search_indexes i
                           WHERE i.name=NEW.index_name AND i.id=NEW.index_id) THEN
                RAISE EXCEPTION 'Search index not found' USING ERRCODE='23503';
            END IF;
            IF NEW.version IS DISTINCT FROM (SELECT coalesce(max(v.version),0)+1
                    FROM nova_meta.config_ai_search_versions v WHERE v.index_name=NEW.index_name) THEN
                RAISE EXCEPTION 'A newer search version exists' USING ERRCODE='23505';
            END IF;
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp(); NEW.completed_at:=NULL;
            NEW.indexed_rows:=0; NEW.failure_code:=NULL; NEW.build_generation:=0;
            RETURN NEW;
        END IF;
        IF ROW(NEW.index_name,NEW.version,NEW.index_id,NEW.model_id,NEW.model_revision,
               NEW.dimensions,NEW.metric,NEW.created_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.index_name,OLD.version,OLD.index_id,OLD.model_id,
               OLD.model_revision,OLD.dimensions,OLD.metric,OLD.created_at,OLD.provenance) THEN
            RAISE EXCEPTION 'Search versions are immutable' USING ERRCODE='23514';
        END IF;
        IF (NEW.build_status IN ('BUILDING','AUTO_BUILDING','READY','FAILED')
            AND current_user<>'nova_meta_maintenance')
           OR (NEW.build_status IN ('ACTIVE','DEPRECATED') AND current_user<>'nova_meta_web') THEN
            -- Builds belong to the maintenance worker, activation to the owner's API.
            RAISE EXCEPTION 'Search version change is not authorized' USING ERRCODE='42501';
        END IF;
        IF NEW.build_status IN ('BUILDING','AUTO_BUILDING') THEN
            -- A claim (or a takeover of a lapsed build) takes the next generation;
            -- the builder that held the previous one is fenced out.
            IF OLD.build_status NOT IN ('PENDING','BUILDING','AUTO_PENDING','AUTO_BUILDING')
               OR (NEW.build_status='AUTO_BUILDING') IS DISTINCT FROM (OLD.build_status LIKE 'AUTO\_%')
               OR NEW.build_generation IS DISTINCT FROM OLD.build_generation+1
               OR NEW.indexed_rows IS DISTINCT FROM OLD.indexed_rows THEN
                RAISE EXCEPTION 'Invalid search build claim' USING ERRCODE='23514';
            END IF;
            NEW.failure_code:=NULL;
        ELSIF NEW.build_status IN ('READY','FAILED') THEN
            IF OLD.build_status NOT IN ('BUILDING','AUTO_BUILDING')
               OR NEW.build_generation IS DISTINCT FROM OLD.build_generation
               OR (NEW.build_status='READY' AND NEW.failure_code IS NOT NULL) THEN
                RAISE EXCEPTION 'Invalid search build result' USING ERRCODE='23514';
            END IF;
            NEW.completed_at:=clock_timestamp();
        ELSIF NEW.build_status IN ('ACTIVE','DEPRECATED') THEN
            IF OLD.build_status NOT IN ('READY','ACTIVE','DEPRECATED')
               OR (NEW.build_status='DEPRECATED' AND OLD.build_status='READY')
               OR ROW(NEW.build_generation,NEW.indexed_rows,NEW.failure_code)
                  IS DISTINCT FROM ROW(OLD.build_generation,OLD.indexed_rows,OLD.failure_code) THEN
                RAISE EXCEPTION 'Invalid search activation' USING ERRCODE='23514';
            END IF;
            NEW.completed_at:=OLD.completed_at;
        ELSE
            RAISE EXCEPTION 'Invalid search version transition' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='config_ai_search_sync_state' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.status NOT IN ('PENDING','BUILDING','READY','FAILED') THEN
            RAISE EXCEPTION 'Invalid search sync state' USING ERRCODE='23514';
        END IF;
        SELECT v.build_generation,v.build_status INTO current_generation,version_status
            FROM nova_meta.config_ai_search_versions v
            WHERE v.index_name=NEW.index_name AND v.version=NEW.version;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'PENDING' OR version_status NOT IN ('PENDING','AUTO_PENDING')
               OR version_status IS NULL THEN
                RAISE EXCEPTION 'A search sync state starts with its pending version'
                    USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.build_generation:=0; NEW.source_rows:=0;
            NEW.indexed_rows:=0; NEW.last_watermark:=NULL;
        ELSIF ROW(NEW.index_name,NEW.version,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.index_name,OLD.version,OLD.provenance)
              OR NEW.build_generation IS DISTINCT FROM current_generation
              OR version_status NOT IN ('BUILDING','AUTO_BUILDING') THEN
            -- Only the builder holding the version's current claim records progress.
            RAISE EXCEPTION 'Search build lease lost' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='ai_search_query_log' THEN
        IF NEW.user_name IS DISTINCT FROM actor OR btrim(NEW.mode)='' OR btrim(NEW.status)='' THEN
            RAISE EXCEPTION 'Invalid search query log' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.record_id:=gen_random_uuid(); NEW.created_at:=clock_timestamp();
    ELSE
        IF NEW.user_name IS DISTINCT FROM actor OR btrim(NEW.mode)=''
           OR jsonb_typeof(NEW.metrics) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Invalid search evaluation' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    END IF;
    RETURN NEW;
END
$body$;
DO $rows$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_semantic_views','config_intelligence_story_feedback',
            'config_ai_search_indexes','config_ai_search_versions','config_ai_search_sync_state',
            'ai_search_query_log','ai_search_eval_runs'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.intelligence_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$rows$;
CREATE TRIGGER config_semantic_view_versions_row_guard BEFORE INSERT OR UPDATE OR DELETE
    ON nova_meta.config_semantic_view_versions FOR EACH ROW
    EXECUTE FUNCTION nova_meta.intelligence_row_guard();

-- ── Success audit for governed changes ──────────────────────────────────────
-- Semantic View definitions, versions and publication, and Search index
-- creation and activation are governed. Build progress, feedback, query logs
-- and the revision stores (append-only history that records its writer) are
-- not, as in the source.
CREATE FUNCTION nova_meta.audit_intelligence_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity text; revision bigint;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_agent','nova_meta_maintenance')
       OR NOT nova_meta.actor_context_valid() OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('config_semantic_views','config_semantic_view_versions',
                                'config_ai_search_indexes','config_ai_search_versions') THEN
        RAISE EXCEPTION 'Invalid Intelligence audit source' USING ERRCODE='42501';
    END IF;
    -- One branch per table: a record field that a table lacks cannot appear in
    -- an expression evaluated for that table.
    IF TG_TABLE_NAME='config_semantic_views' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.id; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.id; revision:=NEW.metadata_revision; END IF;
    ELSIF TG_TABLE_NAME='config_semantic_view_versions' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.view_id||':'||OLD.version; revision:=OLD.version;
        ELSE identity:=NEW.view_id||':'||NEW.version; revision:=NEW.version; END IF;
    ELSIF TG_TABLE_NAME='config_ai_search_indexes' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.name; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.name; revision:=NEW.metadata_revision; END IF;
    ELSE
        IF TG_OP='UPDATE' AND NOT (NEW.build_status IN ('ACTIVE','DEPRECATED')
                                   AND NEW.build_status IS DISTINCT FROM OLD.build_status) THEN
            RETURN NULL;  -- build claims and results are execution, not governance
        END IF;
        IF TG_OP='DELETE' THEN identity:=OLD.index_name||':'||OLD.version; revision:=OLD.version;
        ELSE identity:=NEW.index_name||':'||NEW.version; revision:=NEW.version; END IF;
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,identity,
        CASE TG_OP WHEN 'INSERT' THEN 'CREATE' WHEN 'DELETE' THEN 'DELETE' ELSE 'UPDATE' END,
        'SUCCESS','ALLOW',TG_TABLE_NAME,jsonb_build_object('id',identity),coalesce(revision,1));
    RETURN NULL;
END
$body$;
CREATE TRIGGER config_semantic_views_audit AFTER INSERT OR UPDATE OR DELETE
    ON nova_meta.config_semantic_views FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_intelligence_change();
CREATE TRIGGER config_semantic_view_versions_audit AFTER INSERT OR UPDATE OR DELETE
    ON nova_meta.config_semantic_view_versions FOR EACH ROW
    EXECUTE FUNCTION nova_meta.audit_intelligence_change();
CREATE TRIGGER config_ai_search_indexes_audit AFTER INSERT OR UPDATE
    ON nova_meta.config_ai_search_indexes FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_intelligence_change();
CREATE TRIGGER config_ai_search_versions_audit AFTER INSERT OR UPDATE
    ON nova_meta.config_ai_search_versions FOR EACH ROW
    EXECUTE FUNCTION nova_meta.audit_intelligence_change();
