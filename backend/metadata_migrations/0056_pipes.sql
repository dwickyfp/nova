-- Pipes: continuous loads of new stage files into a table (CREATE PIPE). A pipe
-- runs on the task worker as its owner role's scheduled-task account; its
-- definition, its file ledger and the load intent of every batch live here.
-- The engine never stores a pipe and never sees a storage credential at rest.
--
-- config_pipes  one row per pipe; properties and state change with a revision
--               compare-and-swap; a worker claims the pipe with a fenced lease.
-- pipe_files    one row per file version (name + etag) the pipe has seen, with
--               its load state, the label of the batch that loads it and errors.
-- pipe_batches  the intent recorded before a batch's INSERT runs: its label
--               (deterministic from the pipe and the file set) and outcome.
CREATE TABLE nova_meta.config_pipes (
    id uuid PRIMARY KEY,
    database_name varchar(128) NOT NULL
        CHECK (btrim(database_name)<>'' AND octet_length(database_name)<=128),
    name varchar(256) NOT NULL CHECK (btrim(name)<>'' AND octet_length(name)<=256),
    target_table varchar(512) NOT NULL CHECK (btrim(target_table)<>''),
    definition text NOT NULL CHECK (char_length(definition) BETWEEN 1 AND 65533),
    source jsonb NOT NULL CHECK (jsonb_typeof(source)='object'),
    auto_ingest boolean NOT NULL DEFAULT true,
    poll_interval integer NOT NULL DEFAULT 300 CHECK (poll_interval BETWEEN 1 AND 86400),
    batch_size bigint NOT NULL DEFAULT 1073741824 CHECK (batch_size>0),
    batch_files integer NOT NULL DEFAULT 256 CHECK (batch_files BETWEEN 1 AND 100000),
    state varchar(16) NOT NULL DEFAULT 'RUNNING'
        CHECK (state IN ('RUNNING','SUSPENDED','ERROR','FINISHED')),
    last_error text CHECK (char_length(last_error)<=4096),
    owner_role varchar(128) NOT NULL CHECK (btrim(owner_role)<>''),
    created_by varchar(128) NOT NULL CHECK (btrim(created_by)<>''),
    task_id varchar(64) NOT NULL CHECK (btrim(task_id)<>''),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision>0),
    wake_count bigint NOT NULL DEFAULT 0 CHECK (wake_count>=0),
    claim_token uuid,
    claim_generation bigint NOT NULL DEFAULT 0 CHECK (claim_generation>=0),
    claim_expires_at timestamptz,
    last_polled_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (database_name, name),
    UNIQUE (task_id),
    CHECK ((claim_token IS NULL)=(claim_expires_at IS NULL))
);

CREATE TABLE nova_meta.pipe_files (
    pipe_id uuid NOT NULL REFERENCES nova_meta.config_pipes(id),
    file_name varchar(1024) NOT NULL CHECK (btrim(file_name)<>''),
    file_version varchar(256) NOT NULL CHECK (btrim(file_version)<>''),
    file_size bigint NOT NULL CHECK (file_size>=0),
    last_modified timestamptz,
    load_state varchar(16) NOT NULL DEFAULT 'UNLOADED'
        CHECK (load_state IN ('UNLOADED','LOADING','FINISHED','ERROR')),
    batch_label varchar(128) CHECK (batch_label ~ '^[A-Za-z][A-Za-z0-9_]{0,127}$'),
    rows_loaded bigint CHECK (rows_loaded>=0),
    error_msg text CHECK (char_length(error_msg)<=4096),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count>=0),
    staged_time timestamptz NOT NULL DEFAULT clock_timestamp(),
    start_load_time timestamptz,
    finish_load_time timestamptz,
    PRIMARY KEY (pipe_id, file_name, file_version),
    CHECK ((load_state='LOADING')<=(batch_label IS NOT NULL)),
    CHECK ((load_state='ERROR')<=(error_msg IS NOT NULL))
);
CREATE INDEX pipe_files_by_state ON nova_meta.pipe_files (pipe_id, load_state, staged_time);
CREATE INDEX pipe_files_by_label ON nova_meta.pipe_files (pipe_id, batch_label)
    WHERE batch_label IS NOT NULL;

CREATE TABLE nova_meta.pipe_batches (
    pipe_id uuid NOT NULL REFERENCES nova_meta.config_pipes(id),
    label varchar(128) NOT NULL CHECK (label ~ '^[A-Za-z][A-Za-z0-9_]{0,127}$'),
    state varchar(16) NOT NULL DEFAULT 'LOADING' CHECK (state IN ('LOADING','FINISHED','FAILED')),
    file_count integer NOT NULL CHECK (file_count>0),
    total_bytes bigint NOT NULL CHECK (total_bytes>=0),
    claim_generation bigint NOT NULL CHECK (claim_generation>0),
    rows_loaded bigint CHECK (rows_loaded>=0),
    error_msg text CHECK (char_length(error_msg)<=4096),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    PRIMARY KEY (pipe_id, label)
);

ALTER TABLE nova_meta.config_pipes ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_pipes FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.pipe_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.pipe_files FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.pipe_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.pipe_batches FORCE ROW LEVEL SECURITY;

-- A person sees and changes the pipes of their active role (administrators see
-- every pipe). The task worker acts as the pipes SERVICE actor. Data access is
-- still the engine's and Ranger's, checked as the owner role at every run.
CREATE FUNCTION nova_meta.pipe_visible(owner text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.task_actor_bound()
       AND current_setting('nova.principal_type',true)='USER'
       AND (owner=current_setting('nova.active_role',true) OR nova_meta.actor_is_administrator())
$body$;
REVOKE ALL ON FUNCTION nova_meta.pipe_visible(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.pipe_visible(text) TO nova_meta_web,nova_meta_query;

CREATE FUNCTION nova_meta.pipe_worker() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.task_actor_bound()
       AND current_setting('nova.principal_type',true)='SERVICE'
       AND current_setting('nova.principal',true)='nova-pipes'
$body$;
REVOKE ALL ON FUNCTION nova_meta.pipe_worker() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.pipe_worker() TO nova_meta_task;

CREATE POLICY config_pipes_read ON nova_meta.config_pipes FOR SELECT
    TO nova_meta_web,nova_meta_query USING (nova_meta.pipe_visible(owner_role));
CREATE POLICY config_pipes_create ON nova_meta.config_pipes FOR INSERT
    TO nova_meta_web,nova_meta_query WITH CHECK (
        nova_meta.pipe_visible(owner_role)
        AND owner_role=current_setting('nova.active_role',true)
        AND created_by=current_setting('nova.principal',true));
CREATE POLICY config_pipes_change ON nova_meta.config_pipes FOR UPDATE
    TO nova_meta_web,nova_meta_query
    USING (nova_meta.pipe_visible(owner_role)) WITH CHECK (nova_meta.pipe_visible(owner_role));
CREATE POLICY config_pipes_remove ON nova_meta.config_pipes FOR DELETE
    TO nova_meta_web,nova_meta_query USING (nova_meta.pipe_visible(owner_role));
CREATE POLICY config_pipes_worker_read ON nova_meta.config_pipes FOR SELECT
    TO nova_meta_task USING (nova_meta.pipe_worker());
CREATE POLICY config_pipes_worker_change ON nova_meta.config_pipes FOR UPDATE
    TO nova_meta_task USING (nova_meta.pipe_worker()) WITH CHECK (nova_meta.pipe_worker());

DO $policies$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['pipe_files','pipe_batches'] LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_web,nova_meta_query '
            'USING (EXISTS (SELECT 1 FROM nova_meta.config_pipes p WHERE p.id=pipe_id))',
            tbl||'_read', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR DELETE TO nova_meta_web,nova_meta_query '
            'USING (EXISTS (SELECT 1 FROM nova_meta.config_pipes p WHERE p.id=pipe_id))',
            tbl||'_remove', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_task '
            'USING (nova_meta.pipe_worker())', tbl||'_worker_read', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO nova_meta_task '
            'WITH CHECK (nova_meta.pipe_worker())', tbl||'_worker_create', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO nova_meta_task '
            'USING (nova_meta.pipe_worker()) WITH CHECK (nova_meta.pipe_worker())',
            tbl||'_worker_change', tbl);
    END LOOP;
END
$policies$;
-- RETRY FILE / RETRY ALL: a person moves their pipe's failed files back to the queue.
CREATE POLICY pipe_files_retry ON nova_meta.pipe_files FOR UPDATE TO nova_meta_web,nova_meta_query
    USING (EXISTS (SELECT 1 FROM nova_meta.config_pipes p WHERE p.id=pipe_id))
    WITH CHECK (EXISTS (SELECT 1 FROM nova_meta.config_pipes p WHERE p.id=pipe_id));

GRANT SELECT,DELETE ON nova_meta.config_pipes,nova_meta.pipe_files,nova_meta.pipe_batches
    TO nova_meta_web,nova_meta_query;
GRANT INSERT (id,database_name,name,target_table,definition,source,auto_ingest,poll_interval,
        batch_size,batch_files,state,owner_role,created_by,task_id)
    ON nova_meta.config_pipes TO nova_meta_web,nova_meta_query;
GRANT UPDATE (auto_ingest,poll_interval,batch_size,batch_files,state,last_error,revision,
        wake_count)
    ON nova_meta.config_pipes TO nova_meta_web,nova_meta_query;
GRANT UPDATE (load_state,error_msg,retry_count) ON nova_meta.pipe_files
    TO nova_meta_web,nova_meta_query;
GRANT SELECT ON nova_meta.config_pipes,nova_meta.pipe_files,nova_meta.pipe_batches
    TO nova_meta_task;
GRANT UPDATE (state,last_error,revision,claim_token,claim_generation,claim_expires_at,
        last_polled_at)
    ON nova_meta.config_pipes TO nova_meta_task;
GRANT INSERT (pipe_id,file_name,file_version,file_size,last_modified),
    UPDATE (load_state,batch_label,rows_loaded,error_msg)
    ON nova_meta.pipe_files TO nova_meta_task;
GRANT INSERT (pipe_id,label,file_count,total_bytes,claim_generation),
    UPDATE (state,rows_loaded,error_msg,claim_generation)
    ON nova_meta.pipe_batches TO nova_meta_task;

-- Row rules: identity never changes; a property or state change names the
-- revision it read; the lease generation only grows; file and batch states move
-- only along the load lifecycle, and their times come from the database clock.
CREATE FUNCTION nova_meta.pipe_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE holder bigint;
BEGIN
    IF TG_TABLE_NAME='config_pipes' THEN
        IF TG_OP='INSERT' THEN
            IF NEW.state<>'RUNNING' THEN
                RAISE EXCEPTION 'A pipe starts running' USING ERRCODE='23514';
            END IF;
            NEW.revision:=1; NEW.wake_count:=0; NEW.last_error:=NULL;
            NEW.claim_token:=NULL; NEW.claim_generation:=0; NEW.claim_expires_at:=NULL;
            NEW.last_polled_at:=NULL;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.id,NEW.database_name,NEW.name,NEW.target_table,NEW.definition,NEW.source,
               NEW.owner_role,NEW.created_by,NEW.task_id,NEW.created_at)
           IS DISTINCT FROM ROW(OLD.id,OLD.database_name,OLD.name,OLD.target_table,
               OLD.definition,OLD.source,OLD.owner_role,OLD.created_by,OLD.task_id,OLD.created_at)
        THEN
            RAISE EXCEPTION 'A pipe definition cannot change; replace the pipe'
                USING ERRCODE='23514';
        END IF;
        IF NEW.claim_generation<OLD.claim_generation OR NEW.wake_count<OLD.wake_count THEN
            RAISE EXCEPTION 'Invalid pipe lease change' USING ERRCODE='23514';
        END IF;
        IF ROW(NEW.auto_ingest,NEW.poll_interval,NEW.batch_size,NEW.batch_files,NEW.state,
               NEW.last_error)
           IS DISTINCT FROM ROW(OLD.auto_ingest,OLD.poll_interval,OLD.batch_size,
               OLD.batch_files,OLD.state,OLD.last_error) THEN
            IF NEW.revision<>OLD.revision+1 THEN
                RAISE EXCEPTION 'A pipe change needs the revision it read' USING ERRCODE='23514';
            END IF;
            NEW.updated_at:=clock_timestamp();
        ELSIF NEW.revision<>OLD.revision THEN
            RAISE EXCEPTION 'Invalid pipe revision' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME='pipe_files' THEN
        IF TG_OP='INSERT' THEN
            NEW.load_state:='UNLOADED'; NEW.batch_label:=NULL; NEW.rows_loaded:=NULL;
            NEW.error_msg:=NULL; NEW.retry_count:=0; NEW.staged_time:=clock_timestamp();
            NEW.start_load_time:=NULL; NEW.finish_load_time:=NULL;
            RETURN NEW;
        END IF;
        IF ROW(NEW.pipe_id,NEW.file_name,NEW.file_version,NEW.file_size,NEW.last_modified,
               NEW.staged_time)
           IS DISTINCT FROM ROW(OLD.pipe_id,OLD.file_name,OLD.file_version,OLD.file_size,
               OLD.last_modified,OLD.staged_time) THEN
            RAISE EXCEPTION 'A file version cannot change' USING ERRCODE='23514';
        END IF;
        IF OLD.load_state='UNLOADED' AND NEW.load_state='LOADING' THEN
            IF NEW.batch_label IS NULL THEN
                RAISE EXCEPTION 'A loading file names its batch' USING ERRCODE='23514';
            END IF;
            NEW.start_load_time:=clock_timestamp(); NEW.finish_load_time:=NULL;
            NEW.error_msg:=NULL; NEW.rows_loaded:=NULL;
        ELSIF OLD.load_state='LOADING' AND NEW.load_state='FINISHED' THEN
            NEW.batch_label:=OLD.batch_label; NEW.error_msg:=NULL;
            NEW.finish_load_time:=clock_timestamp();
        ELSIF OLD.load_state='LOADING' AND NEW.load_state='ERROR' THEN
            NEW.batch_label:=OLD.batch_label; NEW.finish_load_time:=clock_timestamp();
        ELSIF OLD.load_state='LOADING' AND NEW.load_state='UNLOADED' THEN
            NEW.batch_label:=NULL; NEW.start_load_time:=NULL; NEW.finish_load_time:=NULL;
        ELSIF OLD.load_state='ERROR' AND NEW.load_state='UNLOADED' THEN
            IF NEW.retry_count<>OLD.retry_count+1 THEN
                RAISE EXCEPTION 'A retry is counted' USING ERRCODE='23514';
            END IF;
            NEW.batch_label:=NULL; NEW.error_msg:=NULL; NEW.rows_loaded:=NULL;
            NEW.start_load_time:=NULL; NEW.finish_load_time:=NULL;
        ELSE
            RAISE EXCEPTION 'Invalid file load transition' USING ERRCODE='23514';
        END IF;
        IF NOT (OLD.load_state='ERROR' AND NEW.load_state='UNLOADED') THEN
            NEW.retry_count:=OLD.retry_count;
        END IF;
        RETURN NEW;
    END IF;
    -- pipe_batches: the worker holding the pipe's current lease records them.
    SELECT claim_generation INTO holder FROM nova_meta.config_pipes WHERE id=NEW.pipe_id;
    IF holder IS DISTINCT FROM NEW.claim_generation THEN
        RAISE EXCEPTION 'The pipe lease moved to another run' USING ERRCODE='40001';
    END IF;
    IF TG_OP='INSERT' THEN
        NEW.state:='LOADING'; NEW.rows_loaded:=NULL; NEW.error_msg:=NULL;
        NEW.created_at:=clock_timestamp(); NEW.finished_at:=NULL;
        RETURN NEW;
    END IF;
    IF ROW(NEW.pipe_id,NEW.label,NEW.file_count,NEW.total_bytes,NEW.created_at)
       IS DISTINCT FROM ROW(OLD.pipe_id,OLD.label,OLD.file_count,OLD.total_bytes,OLD.created_at)
    THEN
        RAISE EXCEPTION 'A batch cannot change its files' USING ERRCODE='23514';
    END IF;
    IF OLD.state='LOADING' AND NEW.state IN ('FINISHED','FAILED') THEN
        NEW.finished_at:=clock_timestamp();
    ELSIF OLD.state IN ('LOADING','FAILED') AND NEW.state='LOADING' THEN
        NEW.finished_at:=NULL; NEW.rows_loaded:=NULL; NEW.error_msg:=NULL;
    ELSE
        RAISE EXCEPTION 'Invalid batch transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER config_pipes_rows BEFORE INSERT OR UPDATE ON nova_meta.config_pipes
    FOR EACH ROW EXECUTE FUNCTION nova_meta.pipe_row_guard();
CREATE TRIGGER pipe_files_rows BEFORE INSERT OR UPDATE ON nova_meta.pipe_files
    FOR EACH ROW EXECUTE FUNCTION nova_meta.pipe_row_guard();
CREATE TRIGGER pipe_batches_rows BEFORE INSERT OR UPDATE ON nova_meta.pipe_batches
    FOR EACH ROW EXECUTE FUNCTION nova_meta.pipe_row_guard();
-- The same statement guard as task metadata: a workload login and an active epoch.
DO $guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_pipes','pipe_files','pipe_batches'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.task_write_guard()',
            tbl||'_write_guard', tbl);
    END LOOP;
END
$guards$;

-- The worker suspends a pipe's schedule when the owner role lost access. That is
-- the task login's only lifecycle change, and only on a pipe's own task.
GRANT UPDATE (lifecycle_state) ON nova_meta.config_tasks TO nova_meta_task;

-- Accept the pipe handler: a pipe's schedule is a task row the worker runs as the
-- owner role's scheduled-task account. The rest of the guard is unchanged from 0053.
CREATE OR REPLACE FUNCTION nova_meta.task_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' AND TG_TABLE_NAME<>'config_task_role_bindings' THEN
            NEW.provenance:='imported';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='config_tasks' THEN
        IF NEW.schedule_kind NOT IN ('manual','interval','cron','stream')
           OR coalesce(NEW.overlap_policy,'skip') NOT IN ('skip','queue','allow')
           OR coalesce(NEW.lifecycle_state,'STARTED') NOT IN ('STARTED','SUSPENDED')
           OR coalesce(NEW.handler,'sql') NOT IN (
               'sql','sql_script','python','intelligence.monitor','intelligence.consolidate',
               'intelligence.outcome','intelligence.newsroom','agents.quality',
               'viz.dashboard_export','pipe.load')
           OR btrim(NEW.name)='' OR NEW.consecutive_fail_count<0 THEN
            RAISE EXCEPTION 'Invalid task' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND current_user='nova_meta_task'
           AND NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state
           AND (OLD.handler<>'pipe.load' OR NEW.lifecycle_state<>'SUSPENDED') THEN
            RAISE EXCEPTION 'The task worker only suspends a pipe schedule'
                USING ERRCODE='42501';
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
