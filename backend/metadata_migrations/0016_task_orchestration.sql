-- Task graph definitions, edges, graph runs, node runs and role bindings.
-- Rows carry provenance: imported source rows are kept as they were, while
-- uniqueness and lifecycle rules apply to rows written at runtime.
CREATE TABLE nova_meta.config_tasks (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    name varchar(256) NOT NULL CHECK (octet_length(name)<=256),
    database_name varchar(128) CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) CHECK (octet_length(schema_name)<=128),
    definition text CHECK (char_length(definition)<=65533),
    schedule_kind varchar(32) NOT NULL CHECK (octet_length(schedule_kind)<=32),
    schedule_expr varchar(256) CHECK (octet_length(schedule_expr)<=256),
    timezone varchar(64) NOT NULL CHECK (octet_length(timezone)<=64),
    when_expr text CHECK (char_length(when_expr)<=65533),
    overlap_policy varchar(32) CHECK (octet_length(overlap_policy)<=32),
    owner_role varchar(128) CHECK (octet_length(owner_role)<=128),
    created_by varchar(128) CHECK (octet_length(created_by)<=128),
    consecutive_fail_count integer DEFAULT 0,
    version bigint DEFAULT 1,
    handler varchar(64) DEFAULT 'sql' CHECK (octet_length(handler)<=64),
    handler_config jsonb,
    lifecycle_state varchar(16) DEFAULT 'STARTED' CHECK (octet_length(lifecycle_state)<=16),
    when_ast text CHECK (char_length(when_ast)<=65533),
    created_at timestamptz,
    updated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- One runtime task per scoped name; a missing scope is a deliberate null-safe
-- value, not "matches anything" (Wave B).
CREATE UNIQUE INDEX config_tasks_runtime_name ON nova_meta.config_tasks
    (database_name,schema_name,name) NULLS NOT DISTINCT WHERE provenance='runtime';

CREATE TABLE nova_meta.config_task_edges (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    graph_id varchar(64) NOT NULL CHECK (octet_length(graph_id)<=64),
    parent_task varchar(256) NOT NULL CHECK (octet_length(parent_task)<=256),
    child_task varchar(256) NOT NULL CHECK (octet_length(child_task)<=256),
    edge_kind varchar(16) NOT NULL DEFAULT 'after' CHECK (octet_length(edge_kind)<=16),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE UNIQUE INDEX config_task_edges_runtime_pair ON nova_meta.config_task_edges
    (graph_id,parent_task,child_task,edge_kind) WHERE provenance='runtime';
CREATE INDEX config_task_edges_graph ON nova_meta.config_task_edges (graph_id);

CREATE TABLE nova_meta.config_task_graph_runs (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    graph_id varchar(64) NOT NULL CHECK (octet_length(graph_id)<=64),
    trigger_type varchar(32) NOT NULL CHECK (octet_length(trigger_type)<=32),
    state varchar(32) NOT NULL CHECK (octet_length(state)<=32),
    overlap_policy varchar(16) NOT NULL DEFAULT 'skip' CHECK (octet_length(overlap_policy)<=16),
    wal_marks text CHECK (char_length(wal_marks)<=65533),
    execution_user varchar(128) CHECK (octet_length(execution_user)<=128),
    execution_role varchar(128) CHECK (octet_length(execution_role)<=128),
    execution_session_id varchar(128) CHECK (octet_length(execution_session_id)<=128),
    started_at timestamptz,
    heartbeat_at timestamptz,
    finished_at timestamptz,
    lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_task_graph_runs_graph ON nova_meta.config_task_graph_runs
    (graph_id,started_at DESC);

CREATE TABLE nova_meta.config_task_runs (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    graph_run_id varchar(64) CHECK (octet_length(graph_run_id)<=64),
    task_id varchar(64) CHECK (octet_length(task_id)<=64),
    attempt integer DEFAULT 1,
    state varchar(32) NOT NULL CHECK (octet_length(state)<=32),
    delegated boolean DEFAULT true,
    starrocks_query_id varchar(128) CHECK (octet_length(starrocks_query_id)<=128),
    error_message text CHECK (char_length(error_message)<=65533),
    output text CHECK (char_length(output)<=65533),
    started_at timestamptz,
    heartbeat_at timestamptz,
    finished_at timestamptz,
    lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_task_runs_graph_run ON nova_meta.config_task_runs (graph_run_id,task_id);

CREATE TABLE nova_meta.config_task_role_bindings (
    role_name varchar(128) PRIMARY KEY CHECK (octet_length(role_name)<=128),
    execution_user varchar(128) NOT NULL CHECK (octet_length(execution_user)<=128),
    configured_by varchar(128) NOT NULL CHECK (octet_length(configured_by)<=128),
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0)
);

ALTER TABLE nova_meta.config_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_task_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_task_edges FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_task_graph_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_task_graph_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_task_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_task_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_task_role_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_task_role_bindings FORCE ROW LEVEL SECURITY;

-- Task metadata is control-plane state: its caller is authorized by the owning
-- service (and the engine for data), so access is per workload, through a
-- bound actor context, never anonymously.
CREATE FUNCTION nova_meta.task_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;
REVOKE ALL ON FUNCTION nova_meta.task_actor_bound() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.task_actor_bound()
    TO nova_meta_web,nova_meta_query,nova_meta_scheduler,nova_meta_task;

DO $policies$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_tasks','web,query,scheduler,task','web,query','web,query,task'),
        ('config_task_edges','web,query,scheduler,task','web,query','web,query'),
        ('config_task_graph_runs','web,query,scheduler,task','web,scheduler,task','web,scheduler,task'),
        ('config_task_runs','web,query,scheduler,task','task','task'),
        ('config_task_role_bindings','web,query,scheduler,task','web','web')
    ) AS t(tbl,readers,inserters,updaters) LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO %s USING (nova_meta.task_actor_bound())',
            item.tbl||'_read', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO %s WITH CHECK (nova_meta.task_actor_bound())',
            item.tbl||'_insert', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r));
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO %s USING (nova_meta.task_actor_bound()) WITH CHECK (nova_meta.task_actor_bound())',
            item.tbl||'_update', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.updaters,',')) r));
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            item.tbl||'_import', item.tbl);
        EXECUTE format('GRANT SELECT ON nova_meta.%I TO %s', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
    END LOOP;
END
$policies$;

-- Definitions and edges may be deleted by the owning service (DROP TASK);
-- run history is never deleted by a workload.
CREATE POLICY config_tasks_delete ON nova_meta.config_tasks FOR DELETE
    TO nova_meta_web,nova_meta_query USING (nova_meta.task_actor_bound());
CREATE POLICY config_task_edges_delete ON nova_meta.config_task_edges FOR DELETE
    TO nova_meta_web,nova_meta_query USING (nova_meta.task_actor_bound());
GRANT DELETE ON nova_meta.config_tasks,nova_meta.config_task_edges TO nova_meta_web,nova_meta_query;

GRANT INSERT (id,name,database_name,schema_name,definition,schedule_kind,schedule_expr,timezone,
        when_expr,overlap_policy,owner_role,created_by,handler,handler_config,lifecycle_state,
        when_ast)
    ON nova_meta.config_tasks TO nova_meta_web,nova_meta_query;
GRANT UPDATE (name,database_name,schema_name,definition,schedule_kind,schedule_expr,timezone,
        when_expr,overlap_policy,owner_role,handler_config,lifecycle_state,when_ast,version)
    ON nova_meta.config_tasks TO nova_meta_web,nova_meta_query;
GRANT UPDATE (consecutive_fail_count) ON nova_meta.config_tasks TO nova_meta_task;
GRANT INSERT (id,graph_id,parent_task,child_task,edge_kind),UPDATE (parent_task,child_task)
    ON nova_meta.config_task_edges TO nova_meta_web,nova_meta_query;
GRANT INSERT (id,graph_id,trigger_type,state,overlap_policy,wal_marks,execution_user,
        execution_role,execution_session_id)
    ON nova_meta.config_task_graph_runs TO nova_meta_web,nova_meta_scheduler,nova_meta_task;
GRANT UPDATE (state,wal_marks,heartbeat_at,finished_at,lease_generation)
    ON nova_meta.config_task_graph_runs TO nova_meta_web,nova_meta_scheduler,nova_meta_task;
GRANT INSERT (id,graph_run_id,task_id,attempt,state,delegated)
    ON nova_meta.config_task_runs TO nova_meta_task;
GRANT UPDATE (state,starrocks_query_id,error_message,output,heartbeat_at,finished_at,
        lease_generation)
    ON nova_meta.config_task_runs TO nova_meta_task;
GRANT INSERT (role_name,execution_user,configured_by),
    UPDATE (execution_user,configured_by,metadata_revision)
    ON nova_meta.config_task_role_bindings TO nova_meta_web;

CREATE FUNCTION nova_meta.task_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Task import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_query','nova_meta_scheduler','nova_meta_task')
        AND TG_OP IN ('INSERT','UPDATE','DELETE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Task metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
DO $guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_tasks','config_task_edges','config_task_graph_runs',
                               'config_task_runs','config_task_role_bindings'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.task_write_guard()',
            tbl||'_write_guard', tbl);
    END LOOP;
END
$guards$;

-- Row rules for runtime writes. Imported rows bypass them on insert; a later
-- runtime change to an imported row must still be a valid transition.
CREATE FUNCTION nova_meta.task_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='config_tasks' THEN
        IF NEW.schedule_kind NOT IN ('manual','interval','cron','stream')
           OR coalesce(NEW.overlap_policy,'skip') NOT IN ('skip','queue','allow')
           OR coalesce(NEW.lifecycle_state,'STARTED') NOT IN ('STARTED','SUSPENDED')
           OR coalesce(NEW.handler,'sql') NOT IN ('sql','python')
           OR btrim(NEW.name)='' OR NEW.consecutive_fail_count<0 THEN
            RAISE EXCEPTION 'Invalid task' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.version:=1; NEW.consecutive_fail_count:=0;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.id,NEW.created_by,NEW.created_at,NEW.provenance,NEW.handler)
                IS DISTINCT FROM ROW(OLD.id,OLD.created_by,OLD.created_at,OLD.provenance,OLD.handler) THEN
            RAISE EXCEPTION 'Invalid task change' USING ERRCODE='23514';
        ELSIF NEW.version IS DISTINCT FROM OLD.version THEN
            -- A definition change advances the version by exactly one.
            IF NEW.version<>coalesce(OLD.version,0)+1 THEN
                RAISE EXCEPTION 'Invalid task version' USING ERRCODE='23514';
            END IF;
            NEW.updated_at:=clock_timestamp();
        ELSIF ROW(NEW.name,NEW.database_name,NEW.schema_name,NEW.definition,NEW.schedule_kind,
                  NEW.schedule_expr,NEW.timezone,NEW.when_expr,NEW.overlap_policy,NEW.owner_role,
                  NEW.handler_config,NEW.when_ast)
              IS DISTINCT FROM ROW(OLD.name,OLD.database_name,OLD.schema_name,OLD.definition,
                  OLD.schedule_kind,OLD.schedule_expr,OLD.timezone,OLD.when_expr,
                  OLD.overlap_policy,OLD.owner_role,OLD.handler_config,OLD.when_ast) THEN
            RAISE EXCEPTION 'A task definition change needs a new version' USING ERRCODE='23514';
        ELSIF NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state THEN
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_task_edges' THEN
        IF NEW.edge_kind NOT IN ('after','finalize') OR btrim(NEW.parent_task)=''
           OR btrim(NEW.child_task)='' OR NEW.parent_task=NEW.child_task THEN
            RAISE EXCEPTION 'Invalid task edge' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
        ELSIF ROW(NEW.id,NEW.graph_id,NEW.edge_kind,NEW.created_at,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.id,OLD.graph_id,OLD.edge_kind,OLD.created_at,OLD.provenance) THEN
            RAISE EXCEPTION 'Invalid task edge change' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='config_task_graph_runs' THEN
        IF NEW.state NOT IN ('pending','running','success','failed','cancelled')
           OR NEW.trigger_type NOT IN ('manual','schedule','stream','reconcile')
           OR NEW.overlap_policy NOT IN ('skip','queue','allow') THEN
            RAISE EXCEPTION 'Invalid graph run' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.state<>'pending' THEN
                RAISE EXCEPTION 'A graph run starts pending' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.lease_generation:=0;
            NEW.started_at:=clock_timestamp(); NEW.finished_at:=NULL; NEW.heartbeat_at:=NULL;
        ELSIF ROW(NEW.id,NEW.graph_id,NEW.trigger_type,NEW.overlap_policy,NEW.execution_user,
                  NEW.execution_role,NEW.execution_session_id,NEW.started_at,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.id,OLD.graph_id,OLD.trigger_type,OLD.overlap_policy,
                  OLD.execution_user,OLD.execution_role,OLD.execution_session_id,OLD.started_at,
                  OLD.provenance)
              OR NEW.lease_generation<OLD.lease_generation
              OR OLD.state IN ('success','failed','cancelled') THEN
            RAISE EXCEPTION 'Invalid graph run transition' USING ERRCODE='23514';
        END IF;
        IF NEW.state IN ('success','failed','cancelled') AND OLD IS DISTINCT FROM NULL
           AND NEW.state IS DISTINCT FROM OLD.state THEN
            NEW.finished_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_task_runs' THEN
        IF NEW.state NOT IN ('pending','running','success','failed','skipped','suspended','abandoned')
           OR NEW.attempt<1 THEN
            RAISE EXCEPTION 'Invalid task run' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.state<>'pending' THEN
                RAISE EXCEPTION 'A node run starts pending' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.lease_generation:=0;
            NEW.started_at:=clock_timestamp(); NEW.finished_at:=NULL; NEW.heartbeat_at:=NULL;
        ELSIF ROW(NEW.id,NEW.graph_run_id,NEW.task_id,NEW.attempt,NEW.delegated,NEW.started_at,
                  NEW.provenance)
              IS DISTINCT FROM ROW(OLD.id,OLD.graph_run_id,OLD.task_id,OLD.attempt,OLD.delegated,
                  OLD.started_at,OLD.provenance)
              OR NEW.lease_generation<OLD.lease_generation
              OR OLD.state IN ('success','failed','skipped','suspended') THEN
            RAISE EXCEPTION 'Invalid node run transition' USING ERRCODE='23514';
        END IF;
        IF NEW.state IN ('success','failed','skipped','suspended') AND OLD IS DISTINCT FROM NULL
           AND NEW.state IS DISTINCT FROM OLD.state THEN
            NEW.finished_at:=clock_timestamp();
        END IF;
    ELSE
        IF btrim(NEW.role_name)='' OR btrim(NEW.execution_user)='' THEN
            RAISE EXCEPTION 'Invalid role binding' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.metadata_revision:=1;
        ELSIF NEW.role_name IS DISTINCT FROM OLD.role_name
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid role binding revision' USING ERRCODE='23514';
        END IF;
        NEW.configured_by:=current_setting('nova.principal',true);
        NEW.updated_at:=clock_timestamp();
    END IF;
    RETURN NEW;
END
$body$;
DO $rows$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_tasks','config_task_edges','config_task_graph_runs',
                               'config_task_runs','config_task_role_bindings'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.task_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$rows$;

-- Definition, edge and role-binding changes are governed actions and audited;
-- run rows are execution history, as in the source, and are not.
CREATE FUNCTION nova_meta.audit_task_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity text; revision bigint;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_task')
       OR NOT nova_meta.actor_context_valid() OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('config_tasks','config_task_edges','config_task_role_bindings') THEN
        RAISE EXCEPTION 'Invalid task audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='config_tasks' THEN
        IF TG_OP='UPDATE' THEN
            IF NEW.version IS NOT DISTINCT FROM OLD.version
               AND NEW.lifecycle_state IS NOT DISTINCT FROM OLD.lifecycle_state THEN
                RETURN NULL;  -- the failure counter is bookkeeping, not a change
            END IF;
        END IF;
    END IF;
    -- One branch per table: a record field that a table lacks cannot appear
    -- in an expression evaluated for that table.
    IF TG_TABLE_NAME='config_task_role_bindings' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.role_name; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.role_name; revision:=NEW.metadata_revision; END IF;
    ELSIF TG_TABLE_NAME='config_tasks' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.id; revision:=OLD.version;
        ELSE identity:=NEW.id; revision:=NEW.version; END IF;
    ELSE
        IF TG_OP='DELETE' THEN identity:=OLD.id; ELSE identity:=NEW.id; END IF;
        revision:=1;
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,identity,
        CASE TG_OP WHEN 'INSERT' THEN 'CREATE' WHEN 'DELETE' THEN 'DELETE' ELSE 'UPDATE' END,
        'SUCCESS','ALLOW',TG_TABLE_NAME,jsonb_build_object('id',identity),coalesce(revision,1));
    RETURN NULL;
END
$body$;
CREATE TRIGGER config_tasks_audit AFTER INSERT OR UPDATE OR DELETE ON nova_meta.config_tasks
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_task_change();
CREATE TRIGGER config_task_edges_audit AFTER INSERT OR UPDATE OR DELETE ON nova_meta.config_task_edges
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_task_change();
CREATE TRIGGER config_task_role_bindings_audit AFTER INSERT OR UPDATE
    ON nova_meta.config_task_role_bindings FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_task_change();
