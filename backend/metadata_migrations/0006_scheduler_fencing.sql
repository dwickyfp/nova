CREATE TABLE nova_meta.scheduler_leases (
    name text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_.]{0,63}$'),
    holder uuid NOT NULL,
    token bigint NOT NULL CHECK (token > 0),
    expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION nova_meta.scheduler_lease_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.token <> 1 OR NEW.expires_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'Invalid scheduler acquisition' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.name IS DISTINCT FROM OLD.name OR NOT (
            (NEW.token = OLD.token AND NEW.holder = OLD.holder
                AND OLD.expires_at > clock_timestamp()) OR
            (NEW.token = OLD.token + 1 AND OLD.expires_at <= clock_timestamp()
                AND NEW.expires_at > clock_timestamp())
        ) THEN
            RAISE EXCEPTION 'Expired or invalid scheduler lease' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.expires_at > clock_timestamp() + interval '5 minutes' THEN
        RAISE EXCEPTION 'Scheduler lease exceeds deadline bound' USING ERRCODE = '23514';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER scheduler_lease_guard BEFORE INSERT OR UPDATE ON nova_meta.scheduler_leases
    FOR EACH ROW EXECUTE FUNCTION nova_meta.scheduler_lease_guard();

CREATE TABLE nova_meta.scheduled_occurrences (
    root_task_id varchar(64) NOT NULL CHECK (root_task_id <> ''),
    definition_version bigint NOT NULL CHECK (definition_version > 0),
    scheduled_at timestamptz NOT NULL,
    definition_ref uuid NOT NULL,
    definition_digest text NOT NULL CHECK (definition_digest ~ '^[0-9a-f]{64}$'),
    graph_run_id varchar(64) NOT NULL UNIQUE CHECK (graph_run_id <> ''),
    job_id uuid NOT NULL UNIQUE REFERENCES nova_meta.job_queue (id),
    lease_name text NOT NULL REFERENCES nova_meta.scheduler_leases (name),
    lease_holder uuid NOT NULL,
    lease_token bigint NOT NULL CHECK (lease_token > 0),
    owner_principal text NOT NULL DEFAULT current_setting('nova.principal'),
    owner_role text NOT NULL DEFAULT current_setting('nova.active_role'),
    principal_type text NOT NULL DEFAULT current_setting('nova.principal_type'),
    security_context_version bigint NOT NULL
        DEFAULT current_setting('nova.security_context_version')::bigint,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (root_task_id, definition_version, scheduled_at)
);
CREATE FUNCTION nova_meta.scheduled_occurrence_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE leader nova_meta.scheduler_leases; work nova_meta.job_queue;
BEGIN
    -- Hold the row through the domain commit so a successor cannot acquire
    -- leadership between validation and occurrence/job publication.
    SELECT * INTO leader FROM nova_meta.scheduler_leases WHERE name = NEW.lease_name FOR SHARE;
    IF leader.holder IS DISTINCT FROM NEW.lease_holder
       OR leader.token IS DISTINCT FROM NEW.lease_token OR leader.expires_at <= clock_timestamp()
       OR NEW.scheduled_at > clock_timestamp() THEN
        RAISE EXCEPTION 'Schedule occurrence has no live leader or is not due' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO work FROM nova_meta.job_queue WHERE id = NEW.job_id;
    IF work.id IS NULL OR work.queue <> 'task' OR work.kind <> 'task.graph' OR work.external_effect
       OR ROW(work.definition_ref, work.definition_version, work.definition_digest,
              work.owner_principal, work.owner_role, work.principal_type, work.security_context_version)
          IS DISTINCT FROM ROW(NEW.definition_ref, NEW.definition_version, NEW.definition_digest,
              NEW.owner_principal, NEW.owner_role, NEW.principal_type, NEW.security_context_version) THEN
        RAISE EXCEPTION 'Schedule occurrence differs from its durable work' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER scheduled_occurrence_guard BEFORE INSERT ON nova_meta.scheduled_occurrences
    FOR EACH ROW EXECUTE FUNCTION nova_meta.scheduled_occurrence_guard();
CREATE CONSTRAINT TRIGGER scheduled_occurrence_commit_guard
    AFTER INSERT ON nova_meta.scheduled_occurrences DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION nova_meta.scheduled_occurrence_guard();

ALTER TABLE nova_meta.scheduler_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.scheduler_leases FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.scheduled_occurrences ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.scheduled_occurrences FORCE ROW LEVEL SECURITY;
CREATE POLICY scheduler_actor ON nova_meta.scheduler_leases AS RESTRICTIVE TO nova_meta_scheduler
    USING (nova_meta.actor_context_valid());
CREATE POLICY scheduler_lease_read ON nova_meta.scheduler_leases FOR SELECT TO nova_meta_scheduler
    USING (true);
CREATE POLICY scheduler_lease_insert ON nova_meta.scheduler_leases FOR INSERT TO nova_meta_scheduler
    WITH CHECK (current_setting('nova.principal_type', true) = 'SERVICE');
-- Caller-bound occurrence transactions need a shared row lock on leadership.
-- UPDATE's WITH CHECK still rejects every non-service lease mutation.
CREATE POLICY scheduler_lease_update ON nova_meta.scheduler_leases FOR UPDATE TO nova_meta_scheduler
    USING (true)
    WITH CHECK (current_setting('nova.principal_type', true) = 'SERVICE');
CREATE POLICY occurrence_actor ON nova_meta.scheduled_occurrences AS RESTRICTIVE TO nova_meta_app
    USING (nova_meta.actor_context_valid());
CREATE POLICY occurrence_read ON nova_meta.scheduled_occurrences FOR SELECT
    TO nova_meta_web, nova_meta_scheduler, nova_meta_task USING (
    (current_user IN ('nova_meta_scheduler', 'nova_meta_task')
        AND current_setting('nova.principal_type', true) = 'SERVICE') OR
    (owner_principal = current_setting('nova.principal', true)
        AND owner_role = current_setting('nova.active_role', true)
        AND principal_type = current_setting('nova.principal_type', true)
        AND security_context_version = current_setting('nova.security_context_version', true)::bigint));
CREATE POLICY occurrence_insert ON nova_meta.scheduled_occurrences FOR INSERT TO nova_meta_scheduler
    WITH CHECK (owner_principal = current_setting('nova.principal', true)
        AND owner_role = current_setting('nova.active_role', true)
        AND principal_type = current_setting('nova.principal_type', true)
        AND security_context_version = current_setting('nova.security_context_version', true)::bigint);

GRANT SELECT, INSERT (name, holder, token, expires_at), UPDATE (holder, token, expires_at)
    ON nova_meta.scheduler_leases TO nova_meta_scheduler;
GRANT SELECT ON nova_meta.scheduled_occurrences TO nova_meta_web, nova_meta_scheduler, nova_meta_task;
GRANT INSERT (root_task_id, definition_version, scheduled_at, definition_ref, definition_digest,
              graph_run_id, job_id, lease_name, lease_holder, lease_token)
    ON nova_meta.scheduled_occurrences TO nova_meta_scheduler;
