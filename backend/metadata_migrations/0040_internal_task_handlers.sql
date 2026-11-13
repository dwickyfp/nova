-- Accept the internal handlers executed by the existing task worker.
-- Keep the finite allowlist, immutable handler identity and version guards.
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
               'sql','python','intelligence.monitor','intelligence.consolidate',
               'intelligence.outcome','intelligence.newsroom','agents.quality')
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
