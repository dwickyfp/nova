CREATE FUNCTION nova_meta.job_worker(queue_name text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid()
       AND current_setting('nova.principal_type', true) = 'SERVICE'
       AND current_user = 'nova_meta_' || queue_name
$body$;
GRANT EXECUTE ON FUNCTION nova_meta.job_worker(text) TO nova_meta_app;

CREATE TABLE nova_meta.job_queue (
    id uuid PRIMARY KEY,
    queue text NOT NULL CHECK (queue IN
        ('query', 'agent', 'task', 'ingestion', 'ml', 'notebook', 'automation', 'maintenance')),
    kind text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_.]{0,63}$'),
    owner_principal text NOT NULL DEFAULT current_setting('nova.principal')
        CHECK (char_length(owner_principal) BETWEEN 1 AND 128),
    owner_role text NOT NULL DEFAULT current_setting('nova.active_role')
        CHECK (char_length(owner_role) BETWEEN 1 AND 128),
    principal_type text NOT NULL DEFAULT current_setting('nova.principal_type')
        CHECK (principal_type IN ('USER', 'SERVICE')),
    security_context_version bigint NOT NULL
        DEFAULT current_setting('nova.security_context_version')::bigint
        CHECK (security_context_version > 0),
    definition_ref uuid NOT NULL,
    definition_version bigint NOT NULL CHECK (definition_version > 0),
    definition_digest text NOT NULL CHECK (definition_digest ~ '^[0-9a-f]{64}$'),
    idempotency_key uuid NOT NULL UNIQUE,
    resource_key text NOT NULL CHECK (resource_key ~ '^[0-9a-f]{64}$'),
    external_effect boolean NOT NULL,
    state text NOT NULL DEFAULT 'PENDING' CHECK (state IN
        ('PENDING', 'LEASED', 'PREPARED', 'SUBMITTED', 'VERIFYING',
         'COMMITTED', 'COMPLETED', 'FAILED', 'BLOCKED', 'CANCELLED')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_holder uuid,
    lease_token bigint NOT NULL DEFAULT 0 CHECK (lease_token >= 0),
    lease_expires_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    retryable boolean NOT NULL DEFAULT false,
    error_class text CHECK (error_class ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((lease_holder IS NULL) = (lease_expires_at IS NULL)),
    CHECK (state IN ('PENDING', 'CANCELLED') OR lease_holder IS NOT NULL),
    CHECK (state <> 'PENDING' OR lease_holder IS NULL),
    CHECK (NOT retryable OR state = 'FAILED'),
    CHECK (external_effect OR state NOT IN ('PREPARED', 'SUBMITTED', 'VERIFYING', 'BLOCKED'))
);
CREATE INDEX job_queue_due ON nova_meta.job_queue (queue, next_attempt_at, created_at, id)
    WHERE state = 'PENDING';
CREATE INDEX job_queue_expired ON nova_meta.job_queue (queue, lease_expires_at, id)
    WHERE state IN ('LEASED', 'PREPARED', 'SUBMITTED', 'VERIFYING', 'BLOCKED', 'COMMITTED');
-- A second job cannot prepare the same resource while an effect is unresolved.
CREATE UNIQUE INDEX job_queue_resource_fence ON nova_meta.job_queue (resource_key)
    WHERE state IN ('PREPARED', 'SUBMITTED', 'VERIFYING', 'BLOCKED', 'COMMITTED');

CREATE TABLE nova_meta.external_effect_intents (
    job_id uuid NOT NULL REFERENCES nova_meta.job_queue (id),
    attempt integer NOT NULL CHECK (attempt > 0),
    operation_id uuid NOT NULL UNIQUE,
    participant text NOT NULL CHECK (participant ~ '^[a-z][a-z0-9_.]{0,63}$'),
    request_digest text NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (job_id, attempt)
);
CREATE TABLE nova_meta.external_effect_receipts (
    job_id uuid NOT NULL,
    attempt integer NOT NULL,
    state text NOT NULL CHECK (state IN ('VISIBLE', 'ABSENT')),
    evidence_ref uuid NOT NULL,
    result_digest text NOT NULL CHECK (result_digest ~ '^[0-9a-f]{64}$'),
    verified_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (job_id, attempt),
    FOREIGN KEY (job_id, attempt) REFERENCES nova_meta.external_effect_intents (job_id, attempt)
);

CREATE TABLE nova_meta.outbox_events (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES nova_meta.job_queue (id),
    ready_revision bigint NOT NULL CHECK (ready_revision > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    next_publish_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    published_at timestamptz,
    publish_attempt integer NOT NULL DEFAULT 0 CHECK (publish_attempt >= 0),
    lease_holder uuid,
    lease_token bigint NOT NULL DEFAULT 0 CHECK (lease_token >= 0),
    lease_expires_at timestamptz,
    UNIQUE (job_id, ready_revision),
    CHECK ((lease_holder IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX outbox_due ON nova_meta.outbox_events (next_publish_at, created_at, id)
    WHERE published_at IS NULL;

CREATE FUNCTION nova_meta.outbox_lease_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'Outbox publication is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.lease_token = OLD.lease_token + 1 THEN
        IF (OLD.lease_expires_at IS NOT NULL AND OLD.lease_expires_at > clock_timestamp())
           OR NEW.publish_attempt <> OLD.publish_attempt + 1 OR NEW.lease_holder IS NULL
           OR NEW.lease_expires_at <= clock_timestamp() OR NEW.published_at IS NOT NULL THEN
            RAISE EXCEPTION 'Invalid outbox claim' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.lease_token <> OLD.lease_token OR NEW.publish_attempt <> OLD.publish_attempt
       OR OLD.lease_holder IS NULL OR OLD.lease_expires_at <= clock_timestamp()
       OR (NEW.lease_holder IS DISTINCT FROM OLD.lease_holder AND NEW.lease_holder IS NOT NULL) THEN
        RAISE EXCEPTION 'Expired or invalid outbox lease' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER outbox_lease_guard BEFORE UPDATE ON nova_meta.outbox_events
    FOR EACH ROW EXECUTE FUNCTION nova_meta.outbox_lease_guard();

CREATE FUNCTION nova_meta.job_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF ROW(NEW.id, NEW.queue, NEW.kind, NEW.owner_principal, NEW.owner_role,
           NEW.principal_type, NEW.security_context_version, NEW.definition_ref,
           NEW.definition_version, NEW.definition_digest, NEW.idempotency_key,
           NEW.resource_key, NEW.external_effect, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.queue, OLD.kind, OLD.owner_principal, OLD.owner_role,
           OLD.principal_type, OLD.security_context_version, OLD.definition_ref,
           OLD.definition_version, OLD.definition_digest, OLD.idempotency_key,
           OLD.resource_key, OLD.external_effect, OLD.created_at)
       OR NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'Immutable job identity or invalid revision' USING ERRCODE = '23514';
    END IF;
    IF OLD.state IN ('COMPLETED', 'CANCELLED') OR NOT (
        (OLD.state = NEW.state AND OLD.state NOT IN ('PENDING', 'FAILED')) OR
        (OLD.state = 'PENDING' AND NEW.state IN ('LEASED', 'CANCELLED')) OR
        (OLD.state = 'LEASED' AND NEW.state IN ('PREPARED', 'COMMITTED', 'FAILED', 'CANCELLED')) OR
        (OLD.state = 'PREPARED' AND NEW.state IN ('SUBMITTED', 'VERIFYING')) OR
        (OLD.state = 'SUBMITTED' AND NEW.state = 'VERIFYING') OR
        (OLD.state = 'VERIFYING' AND NEW.state IN ('COMMITTED', 'FAILED', 'BLOCKED')) OR
        (OLD.state = 'BLOCKED' AND NEW.state = 'VERIFYING') OR
        (OLD.state = 'COMMITTED' AND NEW.state = 'COMPLETED') OR
        (OLD.state = 'FAILED' AND OLD.retryable AND NEW.state = 'PENDING')
    ) OR (OLD.external_effect AND OLD.state = 'LEASED' AND NEW.state IN ('COMMITTED', 'FAILED')) THEN
        RAISE EXCEPTION 'Illegal durable job transition' USING ERRCODE = '23514';
    END IF;
    IF NEW.lease_token = OLD.lease_token + 1 THEN
        IF (OLD.state <> 'PENDING' AND OLD.lease_expires_at > clock_timestamp())
           OR NEW.state NOT IN ('LEASED', 'VERIFYING', 'COMMITTED')
           OR NEW.lease_holder IS NULL OR NEW.lease_expires_at <= clock_timestamp()
           OR NEW.attempt <> OLD.attempt + (CASE WHEN OLD.state IN ('PENDING', 'LEASED') THEN 1 ELSE 0 END)
        THEN RAISE EXCEPTION 'Invalid job claim' USING ERRCODE = '23514'; END IF;
    ELSE
        IF NEW.lease_token <> OLD.lease_token OR NEW.attempt <> OLD.attempt
           OR (NEW.lease_holder IS DISTINCT FROM OLD.lease_holder AND NEW.state <> 'PENDING')
           OR (OLD.state NOT IN ('PENDING', 'FAILED') AND OLD.lease_expires_at <= clock_timestamp())
        THEN RAISE EXCEPTION 'Expired or invalid job lease' USING ERRCODE = '23514'; END IF;
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER job_revision_guard BEFORE UPDATE ON nova_meta.job_queue
    FOR EACH ROW EXECUTE FUNCTION nova_meta.job_revision_guard();

CREATE FUNCTION nova_meta.job_effect_consistency() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE job nova_meta.job_queue; proof text; identity uuid;
BEGIN
    IF TG_TABLE_NAME = 'job_queue' THEN identity := NEW.id; ELSE identity := NEW.job_id; END IF;
    SELECT * INTO job FROM nova_meta.job_queue WHERE id = identity;
    IF job.state = 'PENDING' AND NOT EXISTS (
        SELECT 1 FROM nova_meta.outbox_events WHERE job_id = identity AND ready_revision = job.revision
    ) THEN
        RAISE EXCEPTION 'Pending job has no durable wakeup' USING ERRCODE = '23514';
    END IF;
    IF job.external_effect AND job.state IN
        ('PREPARED', 'SUBMITTED', 'VERIFYING', 'BLOCKED', 'COMMITTED', 'COMPLETED', 'FAILED') THEN
        IF NOT EXISTS (SELECT 1 FROM nova_meta.external_effect_intents
                       WHERE job_id = identity AND attempt = job.attempt) THEN
            RAISE EXCEPTION 'Missing immutable external intent' USING ERRCODE = '23514';
        END IF;
        SELECT state INTO proof FROM nova_meta.external_effect_receipts
            WHERE job_id = identity AND attempt = job.attempt;
        IF (job.state IN ('COMMITTED', 'COMPLETED') AND proof IS DISTINCT FROM 'VISIBLE')
           OR (job.state = 'FAILED' AND proof IS DISTINCT FROM 'ABSENT')
           OR (proof IS NOT NULL AND job.state NOT IN ('COMMITTED', 'COMPLETED', 'FAILED')) THEN
            RAISE EXCEPTION 'External outcome has no matching proof' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NULL;
END
$body$;
CREATE CONSTRAINT TRIGGER job_effect_consistency AFTER INSERT OR UPDATE ON nova_meta.job_queue
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.job_effect_consistency();
CREATE CONSTRAINT TRIGGER receipt_job_consistency AFTER INSERT ON nova_meta.external_effect_receipts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.job_effect_consistency();

ALTER TABLE nova_meta.job_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.job_queue FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.external_effect_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.external_effect_intents FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.external_effect_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.external_effect_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.outbox_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.outbox_events FORCE ROW LEVEL SECURITY;

CREATE POLICY job_actor ON nova_meta.job_queue AS RESTRICTIVE TO nova_meta_app
    USING (nova_meta.actor_context_valid());
CREATE POLICY job_read ON nova_meta.job_queue FOR SELECT TO nova_meta_app USING (
    nova_meta.job_worker(queue) OR
    (owner_principal = current_setting('nova.principal', true)
     AND owner_role = current_setting('nova.active_role', true)
     AND principal_type = current_setting('nova.principal_type', true)
     AND security_context_version = current_setting('nova.security_context_version', true)::bigint));
CREATE POLICY job_insert ON nova_meta.job_queue FOR INSERT
    TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent, nova_meta_scheduler,
       nova_meta_task, nova_meta_ingestion, nova_meta_ml, nova_meta_notebook, nova_meta_automation,
       nova_meta_maintenance WITH CHECK (
    owner_principal = current_setting('nova.principal', true)
    AND owner_role = current_setting('nova.active_role', true)
    AND principal_type = current_setting('nova.principal_type', true)
    AND security_context_version = current_setting('nova.security_context_version', true)::bigint
    AND state = 'PENDING' AND revision = 1 AND attempt = 0 AND lease_token = 0);
CREATE POLICY job_update ON nova_meta.job_queue FOR UPDATE TO nova_meta_app
    USING (nova_meta.job_worker(queue)) WITH CHECK (nova_meta.job_worker(queue));

CREATE POLICY intent_read ON nova_meta.external_effect_intents FOR SELECT TO nova_meta_app
    USING (EXISTS (SELECT 1 FROM nova_meta.job_queue WHERE id = job_id));
CREATE POLICY intent_insert ON nova_meta.external_effect_intents FOR INSERT TO nova_meta_app
    WITH CHECK (EXISTS (SELECT 1 FROM nova_meta.job_queue j WHERE j.id = job_id
        AND j.attempt = external_effect_intents.attempt AND j.state = 'PREPARED'
        AND j.external_effect AND nova_meta.job_worker(j.queue)
        AND j.lease_expires_at > clock_timestamp()));
CREATE POLICY receipt_read ON nova_meta.external_effect_receipts FOR SELECT TO nova_meta_app
    USING (EXISTS (SELECT 1 FROM nova_meta.job_queue WHERE id = job_id));
CREATE POLICY receipt_insert ON nova_meta.external_effect_receipts FOR INSERT TO nova_meta_app
    WITH CHECK (EXISTS (SELECT 1 FROM nova_meta.job_queue j WHERE j.id = job_id
        AND j.attempt = external_effect_receipts.attempt AND j.state = 'VERIFYING'
        AND j.external_effect AND nova_meta.job_worker(j.queue)
        AND j.lease_expires_at > clock_timestamp()));

CREATE POLICY outbox_insert ON nova_meta.outbox_events FOR INSERT TO nova_meta_app
    WITH CHECK (EXISTS (SELECT 1 FROM nova_meta.job_queue j WHERE j.id = job_id
        AND j.revision = ready_revision AND j.state = 'PENDING')
        AND published_at IS NULL AND lease_token = 0 AND publish_attempt = 0);
CREATE POLICY outbox_read ON nova_meta.outbox_events FOR SELECT TO nova_meta_app USING (
    nova_meta.job_worker('outbox') OR EXISTS (SELECT 1 FROM nova_meta.job_queue WHERE id = job_id));
CREATE POLICY outbox_update ON nova_meta.outbox_events FOR UPDATE TO nova_meta_outbox
    USING (nova_meta.job_worker('outbox')) WITH CHECK (nova_meta.job_worker('outbox'));

GRANT SELECT ON nova_meta.job_queue, nova_meta.external_effect_intents,
    nova_meta.external_effect_receipts, nova_meta.outbox_events TO nova_meta_app;
GRANT INSERT (id, queue, kind, definition_ref, definition_version, definition_digest,
              idempotency_key, resource_key, external_effect, next_attempt_at)
    ON nova_meta.job_queue TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent,
    nova_meta_scheduler, nova_meta_task, nova_meta_ingestion, nova_meta_ml,
    nova_meta_notebook, nova_meta_automation, nova_meta_maintenance;
GRANT UPDATE (state, revision, attempt, lease_holder, lease_token, lease_expires_at,
              next_attempt_at, retryable, error_class)
    ON nova_meta.job_queue TO nova_meta_query, nova_meta_agent, nova_meta_task,
    nova_meta_ingestion, nova_meta_ml, nova_meta_notebook, nova_meta_automation, nova_meta_maintenance;
GRANT INSERT (job_id, attempt, operation_id, participant, request_digest)
    ON nova_meta.external_effect_intents TO nova_meta_query, nova_meta_agent, nova_meta_task,
    nova_meta_ingestion, nova_meta_ml, nova_meta_notebook, nova_meta_automation, nova_meta_maintenance;
GRANT INSERT (job_id, attempt, state, evidence_ref, result_digest)
    ON nova_meta.external_effect_receipts TO nova_meta_query, nova_meta_agent, nova_meta_task,
    nova_meta_ingestion, nova_meta_ml, nova_meta_notebook, nova_meta_automation, nova_meta_maintenance;
GRANT INSERT (id, job_id, ready_revision) ON nova_meta.outbox_events
    TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent, nova_meta_scheduler,
    nova_meta_task, nova_meta_ingestion, nova_meta_ml, nova_meta_notebook,
    nova_meta_automation, nova_meta_maintenance;
GRANT UPDATE (next_publish_at, published_at, publish_attempt, lease_holder, lease_token, lease_expires_at)
    ON nova_meta.outbox_events TO nova_meta_outbox;
