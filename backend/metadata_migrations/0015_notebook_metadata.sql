-- Notebook runtime lifecycle, finished executions and package manifests.
-- Every row belongs to one person: a shared notebook never shares a runtime,
-- an execution history or a package manifest between people.
CREATE TABLE nova_meta.config_notebook_runtimes (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    notebook_id varchar(64) NOT NULL CHECK (octet_length(notebook_id)<=64),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    status varchar(24) NOT NULL CHECK (octet_length(status)<=24),
    active_role varchar(128) CHECK (octet_length(active_role)<=128),
    image varchar(512) CHECK (octet_length(image)<=512),
    cpu varchar(16) CHECK (octet_length(cpu)<=16),
    memory varchar(16) CHECK (octet_length(memory)<=16),
    generation integer NOT NULL DEFAULT 1 CHECK (generation>0),
    error_message text CHECK (char_length(error_message)<=65533),
    created_at timestamptz,
    ready_at timestamptz,
    ended_at timestamptz,
    end_reason varchar(32) CHECK (octet_length(end_reason)<=32),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0)
);
CREATE INDEX config_notebook_runtimes_owner
    ON nova_meta.config_notebook_runtimes (user_name,notebook_id);

CREATE TABLE nova_meta.config_notebook_executions (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    notebook_id varchar(64) NOT NULL CHECK (octet_length(notebook_id)<=64),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    runtime_id varchar(64) NOT NULL CHECK (octet_length(runtime_id)<=64),
    run_id varchar(64) NOT NULL CHECK (octet_length(run_id)<=64),
    cell_id varchar(64) NOT NULL CHECK (octet_length(cell_id)<=64),
    cell_kind varchar(16) NOT NULL CHECK (octet_length(cell_kind)<=16),
    sql_engine varchar(16) CHECK (octet_length(sql_engine)<=16),
    source_revision varchar(80) NOT NULL CHECK (octet_length(source_revision)<=80),
    generation integer NOT NULL DEFAULT 1,
    execution_count integer,
    status varchar(16) NOT NULL CHECK (octet_length(status)<=16),
    active_role varchar(128) CHECK (octet_length(active_role)<=128),
    error_name varchar(256) CHECK (octet_length(error_name)<=256),
    error_message text CHECK (char_length(error_message)<=65533),
    output_key varchar(1024) CHECK (octet_length(output_key)<=1024),
    output_bytes bigint,
    truncated boolean NOT NULL DEFAULT false,
    summary_json text CHECK (char_length(summary_json)<=65533),
    queued_at timestamptz,
    started_at timestamptz,
    ended_at timestamptz,
    duration_ms bigint
);
CREATE INDEX config_notebook_executions_cell
    ON nova_meta.config_notebook_executions (user_name,notebook_id,cell_id,ended_at DESC);

-- The source keyed packages by notebook only, so two people sharing a notebook
-- overwrote each other's manifest (R21). Each person now has their own row.
CREATE TABLE nova_meta.config_notebook_packages (
    notebook_id varchar(64) NOT NULL CHECK (octet_length(notebook_id)<=64),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    requirements_json text NOT NULL CHECK (char_length(requirements_json)<=65533),
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    PRIMARY KEY (notebook_id,user_name)
);

ALTER TABLE nova_meta.config_notebook_runtimes ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_notebook_runtimes FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_notebook_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_notebook_executions FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_notebook_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_notebook_packages FORCE ROW LEVEL SECURITY;

-- Only the person a row belongs to, through a bound web or notebook identity.
CREATE FUNCTION nova_meta.notebook_row_owner(owner text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT session_user IN ('nova_meta_web','nova_meta_notebook')
        AND nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
        AND owner IS NOT DISTINCT FROM current_setting('nova.principal',true)
$body$;
REVOKE ALL ON FUNCTION nova_meta.notebook_row_owner(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.notebook_row_owner(text)
    TO nova_meta_web,nova_meta_notebook;

CREATE POLICY notebook_runtime_read ON nova_meta.config_notebook_runtimes FOR SELECT
    TO nova_meta_web,nova_meta_notebook USING (nova_meta.notebook_row_owner(user_name));
CREATE POLICY notebook_runtime_insert ON nova_meta.config_notebook_runtimes FOR INSERT
    TO nova_meta_notebook WITH CHECK (nova_meta.notebook_row_owner(user_name));
CREATE POLICY notebook_runtime_update ON nova_meta.config_notebook_runtimes FOR UPDATE
    TO nova_meta_notebook USING (nova_meta.notebook_row_owner(user_name))
    WITH CHECK (nova_meta.notebook_row_owner(user_name));
CREATE POLICY notebook_execution_read ON nova_meta.config_notebook_executions FOR SELECT
    TO nova_meta_web,nova_meta_notebook USING (nova_meta.notebook_row_owner(user_name));
CREATE POLICY notebook_execution_insert ON nova_meta.config_notebook_executions FOR INSERT
    TO nova_meta_notebook WITH CHECK (nova_meta.notebook_row_owner(user_name));
CREATE POLICY notebook_package_read ON nova_meta.config_notebook_packages FOR SELECT
    TO nova_meta_web,nova_meta_notebook USING (nova_meta.notebook_row_owner(user_name));
CREATE POLICY notebook_package_insert ON nova_meta.config_notebook_packages FOR INSERT
    TO nova_meta_web,nova_meta_notebook WITH CHECK (nova_meta.notebook_row_owner(user_name));
CREATE POLICY notebook_package_update ON nova_meta.config_notebook_packages FOR UPDATE
    TO nova_meta_web,nova_meta_notebook USING (nova_meta.notebook_row_owner(user_name))
    WITH CHECK (nova_meta.notebook_row_owner(user_name));
CREATE POLICY notebook_runtime_import ON nova_meta.config_notebook_runtimes FOR ALL
    TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));
CREATE POLICY notebook_execution_import ON nova_meta.config_notebook_executions FOR ALL
    TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));
CREATE POLICY notebook_package_import ON nova_meta.config_notebook_packages FOR ALL
    TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton));

GRANT SELECT ON nova_meta.config_notebook_runtimes,nova_meta.config_notebook_executions,
    nova_meta.config_notebook_packages TO nova_meta_web,nova_meta_notebook;
GRANT INSERT (id,notebook_id,user_name,status,active_role,image,cpu,memory,generation,
        error_message,created_at,ready_at,ended_at,end_reason),
    UPDATE (status,active_role,image,cpu,memory,generation,error_message,ready_at,ended_at,
        end_reason,metadata_revision)
    ON nova_meta.config_notebook_runtimes TO nova_meta_notebook;
-- Executions are written once, at completion; no workload may change one.
GRANT INSERT (id,notebook_id,user_name,runtime_id,run_id,cell_id,cell_kind,sql_engine,
        source_revision,generation,execution_count,status,active_role,error_name,
        error_message,output_key,output_bytes,truncated,summary_json,queued_at,started_at,
        ended_at,duration_ms)
    ON nova_meta.config_notebook_executions TO nova_meta_notebook;
GRANT INSERT (notebook_id,user_name,requirements_json),
    UPDATE (requirements_json,metadata_revision)
    ON nova_meta.config_notebook_packages TO nova_meta_web,nova_meta_notebook;

CREATE FUNCTION nova_meta.notebook_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Notebook import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_notebook') AND TG_OP IN ('INSERT','UPDATE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Notebook metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
CREATE TRIGGER notebook_runtime_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_notebook_runtimes FOR EACH STATEMENT
    EXECUTE FUNCTION nova_meta.notebook_write_guard();
CREATE TRIGGER notebook_execution_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_notebook_executions FOR EACH STATEMENT
    EXECUTE FUNCTION nova_meta.notebook_write_guard();
CREATE TRIGGER notebook_package_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_notebook_packages FOR EACH STATEMENT
    EXECUTE FUNCTION nova_meta.notebook_write_guard();

-- A runtime row records one lifecycle. Its identity is fixed, its generation
-- only grows, and once it has ended it never changes again.
CREATE FUNCTION nova_meta.notebook_runtime_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN RETURN NEW; END IF;
    IF NEW.status NOT IN ('READY','FAILED','STOPPED','LOST')
       OR (NEW.status IN ('FAILED','STOPPED','LOST')) <> (NEW.ended_at IS NOT NULL)
       OR NEW.created_at IS NULL THEN
        RAISE EXCEPTION 'Invalid notebook runtime state' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.metadata_revision<>1 THEN
            RAISE EXCEPTION 'Invalid notebook runtime' USING ERRCODE='23514';
        END IF;
    ELSIF ROW(NEW.id,NEW.notebook_id,NEW.user_name,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id,OLD.notebook_id,OLD.user_name,OLD.created_at)
        OR NEW.metadata_revision<>OLD.metadata_revision+1
        OR NEW.generation<OLD.generation OR OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid notebook runtime transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER notebook_runtime_guard BEFORE INSERT OR UPDATE
    ON nova_meta.config_notebook_runtimes FOR EACH ROW
    EXECUTE FUNCTION nova_meta.notebook_runtime_guard();

CREATE FUNCTION nova_meta.notebook_execution_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN RETURN NEW; END IF;
    IF NEW.status NOT IN ('succeeded','failed','cancelled','skipped','timeout')
       OR NEW.cell_kind NOT IN ('sql','python') OR NEW.generation<1
       OR NEW.queued_at IS NULL OR NEW.ended_at IS NULL
       OR (NEW.started_at IS NOT NULL AND NEW.started_at>NEW.ended_at)
       OR (NEW.output_bytes IS NOT NULL AND NEW.output_bytes<0)
       OR (NEW.duration_ms IS NOT NULL AND NEW.duration_ms<0) THEN
        RAISE EXCEPTION 'Invalid notebook execution' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER notebook_execution_guard BEFORE INSERT
    ON nova_meta.config_notebook_executions FOR EACH ROW
    EXECUTE FUNCTION nova_meta.notebook_execution_guard();

CREATE FUNCTION nova_meta.notebook_package_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN RETURN NEW; END IF;
    IF jsonb_typeof(NEW.requirements_json::jsonb) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'A package manifest is a list' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.metadata_revision<>1 THEN
            RAISE EXCEPTION 'Invalid notebook packages' USING ERRCODE='23514';
        END IF;
    ELSIF ROW(NEW.notebook_id,NEW.user_name) IS DISTINCT FROM ROW(OLD.notebook_id,OLD.user_name)
        OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
        RAISE EXCEPTION 'Invalid notebook packages revision' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER notebook_package_guard BEFORE INSERT OR UPDATE
    ON nova_meta.config_notebook_packages FOR EACH ROW
    EXECUTE FUNCTION nova_meta.notebook_package_guard();

-- Package changes are governed actions; runtime and execution rows are the
-- operational history the source never audited, so only packages are audited.
CREATE FUNCTION nova_meta.audit_notebook_package_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_notebook')
       OR NOT nova_meta.actor_context_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_TABLE_NAME<>'config_notebook_packages'
       OR TG_OP NOT IN ('INSERT','UPDATE') THEN
        RAISE EXCEPTION 'Invalid notebook audit source' USING ERRCODE='42501';
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,NEW.notebook_id,
        CASE WHEN TG_OP='INSERT' THEN 'CREATE' ELSE 'UPDATE' END,'SUCCESS','ALLOW',
        TG_TABLE_NAME,jsonb_build_object('notebook_id',NEW.notebook_id,'user_name',NEW.user_name),
        NEW.metadata_revision);
    RETURN NULL;
END
$body$;
CREATE TRIGGER notebook_package_audit AFTER INSERT OR UPDATE
    ON nova_meta.config_notebook_packages FOR EACH ROW
    EXECUTE FUNCTION nova_meta.audit_notebook_package_change();
