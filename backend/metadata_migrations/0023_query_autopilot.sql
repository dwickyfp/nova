-- Query Autopilot control metadata: the thirteen QUERY_AUTOPILOT_* tables.
-- Every table keeps the source envelope (id, family/cohort, version, state,
-- payload, timestamps, expiry) so payloads and opaque ids survive import
-- unchanged. Rows carry provenance: imported rows are kept as they were, while
-- lifecycle, fencing and approval rules apply to rows written at runtime.
--
-- Classes (see PG_QUERY_AUTOPILOT.md):
--   governed definitions  policies, enrollments      web only, version CAS, audited
--   governed candidates   opportunities              revision CAS, approval bound to the
--                                                    approving principal/role, audited
--   leased jobs           jobs                       created once, lease generation
--   fenced results        experiments, actions,      written only under the current lease
--                         outcomes                   of their job; actions/outcomes append-only
--   facts                 observations, incidents    insert once; identical retry only
--   evidence              evidence                   identity fixed; expiry is terminal
--   derived summaries     families, rollups,         worker-maintained, identity fixed
--                         baselines
DO $tables$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['query_autopilot_families','query_autopilot_observations',
            'query_autopilot_rollups','query_autopilot_baselines','query_autopilot_evidence',
            'query_autopilot_incidents','query_autopilot_opportunities',
            'query_autopilot_experiments','query_autopilot_actions','query_autopilot_outcomes',
            'query_autopilot_policies','query_autopilot_enrollments','query_autopilot_jobs'] LOOP
        EXECUTE format($ddl$CREATE TABLE nova_meta.%I (
            id varchar(128) PRIMARY KEY CHECK (octet_length(id)<=128),
            family_id varchar(64) NOT NULL CHECK (octet_length(family_id)<=64),
            cohort_id varchar(64) NOT NULL CHECK (octet_length(cohort_id)<=64),
            version bigint NOT NULL,
            state varchar(32) NOT NULL CHECK (octet_length(state)<=32),
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            expires_at timestamptz,
            provenance varchar(16) NOT NULL DEFAULT 'imported'
                CHECK (provenance IN ('runtime','imported'))
        )$ddl$, tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY', tbl);
    END LOOP;
END
$tables$;

-- Paging is by id within a filter, as in the source repository.
CREATE INDEX query_autopilot_families_cohort ON nova_meta.query_autopilot_families (cohort_id,id);
CREATE INDEX query_autopilot_observations_created ON nova_meta.query_autopilot_observations (created_at);
CREATE INDEX query_autopilot_observations_updated ON nova_meta.query_autopilot_observations (updated_at,id);
CREATE INDEX query_autopilot_rollups_state ON nova_meta.query_autopilot_rollups (state,id);
CREATE INDEX query_autopilot_rollups_created ON nova_meta.query_autopilot_rollups (created_at);
CREATE INDEX query_autopilot_baselines_created ON nova_meta.query_autopilot_baselines (created_at);
CREATE INDEX query_autopilot_evidence_updated ON nova_meta.query_autopilot_evidence (updated_at,id);
CREATE INDEX query_autopilot_outcomes_family ON nova_meta.query_autopilot_outcomes (family_id,cohort_id,id);
CREATE INDEX query_autopilot_jobs_state ON nova_meta.query_autopilot_jobs (state,id);

-- Leased jobs: a claim advances the generation; only its holder may move the job.
ALTER TABLE nova_meta.query_autopilot_jobs
    ADD COLUMN lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation>=0),
    ADD COLUMN heartbeat_at timestamptz;

-- Results written by a job record which job and lease wrote them.
ALTER TABLE nova_meta.query_autopilot_experiments
    ADD COLUMN job_id varchar(128) CHECK (octet_length(job_id)<=128),
    ADD COLUMN job_generation bigint CHECK (job_generation>=1);
ALTER TABLE nova_meta.query_autopilot_actions
    ADD COLUMN job_id varchar(128) CHECK (octet_length(job_id)<=128),
    ADD COLUMN job_generation bigint CHECK (job_generation>=1);
ALTER TABLE nova_meta.query_autopilot_outcomes
    ADD COLUMN job_id varchar(128) CHECK (octet_length(job_id)<=128),
    ADD COLUMN job_generation bigint CHECK (job_generation>=1);

-- Candidates: the domain version (advanced by experiments) is separate from the
-- metadata revision that every change advances. The approval is bound to the
-- principal, role and security-context version that approved it.
ALTER TABLE nova_meta.query_autopilot_opportunities
    ADD COLUMN metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    ADD COLUMN approved_by varchar(128) CHECK (octet_length(approved_by)<=128),
    ADD COLUMN approved_role varchar(128) CHECK (octet_length(approved_role)<=128),
    ADD COLUMN approved_context_version bigint,
    ADD COLUMN approval_digest varchar(64) CHECK (octet_length(approval_digest)<=64),
    ADD COLUMN approval_expires_at timestamptz,
    ADD CONSTRAINT query_autopilot_opportunities_approval_complete CHECK (
        (approved_by IS NULL AND approved_role IS NULL AND approved_context_version IS NULL
            AND approval_digest IS NULL AND approval_expires_at IS NULL)
        OR (approved_by IS NOT NULL AND approved_role IS NOT NULL
            AND approved_context_version IS NOT NULL AND approval_digest IS NOT NULL
            AND approval_expires_at IS NOT NULL));

ALTER TABLE nova_meta.query_autopilot_policies
    ADD COLUMN updated_by varchar(128) CHECK (octet_length(updated_by)<=128);
ALTER TABLE nova_meta.query_autopilot_enrollments
    ADD COLUMN updated_by varchar(128) CHECK (octet_length(updated_by)<=128);

-- Autopilot metadata is control-plane state; the owning service (and the
-- engine, for data) authorizes callers, so access is per workload through a
-- bound actor context, never anonymous. The autopilot worker runs under the
-- automation workload login (WAVE_J_REVIEW.md).
CREATE FUNCTION nova_meta.autopilot_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;
REVOKE ALL ON FUNCTION nova_meta.autopilot_actor_bound() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.autopilot_actor_bound()
    TO nova_meta_web,nova_meta_query,nova_meta_proxy,nova_meta_scheduler,nova_meta_automation;

DO $policies$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('query_autopilot_policies','web,query,proxy,automation','web','web'),
        ('query_autopilot_enrollments','web,query,proxy,automation','web','web'),
        ('query_autopilot_opportunities','web,automation','web,automation','web,automation'),
        ('query_autopilot_jobs','web,scheduler,automation','web,scheduler,automation','web,automation'),
        ('query_autopilot_experiments','web,automation','automation','automation'),
        ('query_autopilot_actions','web,automation','automation','automation'),
        ('query_autopilot_outcomes','web,automation','automation','automation'),
        ('query_autopilot_evidence','web,query,proxy,automation','web,query,proxy,automation',
            'web,query,proxy,automation'),
        ('query_autopilot_observations','web,query,proxy,automation','web,query,proxy',''),
        ('query_autopilot_incidents','web,automation','automation',''),
        ('query_autopilot_families','web,automation','automation','automation'),
        ('query_autopilot_rollups','web,automation','automation','automation'),
        ('query_autopilot_baselines','web,automation','automation','automation')
    ) AS t(tbl,readers,inserters,updaters) LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO %s USING (nova_meta.autopilot_actor_bound())',
            item.tbl||'_read', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO %s WITH CHECK (nova_meta.autopilot_actor_bound())',
            item.tbl||'_insert', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r));
        IF item.updaters<>'' THEN
            EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO %s USING (nova_meta.autopilot_actor_bound()) WITH CHECK (nova_meta.autopilot_actor_bound())',
                item.tbl||'_update', item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.updaters,',')) r));
        END IF;
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            item.tbl||'_import', item.tbl);
        EXECUTE format('GRANT SELECT ON nova_meta.%I TO %s', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
        EXECUTE format('GRANT INSERT (id,family_id,cohort_id,version,state,payload,expires_at) ON nova_meta.%I TO %s',
            item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r));
        IF item.updaters<>'' THEN
            EXECUTE format('GRANT UPDATE (version,state,payload,expires_at) ON nova_meta.%I TO %s',
                item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.updaters,',')) r));
        END IF;
    END LOOP;
END
$policies$;

-- Column grants beyond the shared envelope.
GRANT INSERT (created_at) ON nova_meta.query_autopilot_observations
    TO nova_meta_web,nova_meta_query,nova_meta_proxy;
GRANT INSERT (created_at) ON nova_meta.query_autopilot_baselines TO nova_meta_automation;
GRANT UPDATE (lease_generation,heartbeat_at) ON nova_meta.query_autopilot_jobs
    TO nova_meta_web,nova_meta_automation;
GRANT INSERT (job_id,job_generation),UPDATE (job_generation)
    ON nova_meta.query_autopilot_experiments,nova_meta.query_autopilot_actions,
       nova_meta.query_autopilot_outcomes TO nova_meta_automation;
GRANT UPDATE (metadata_revision,approval_digest,approval_expires_at,approved_by,approved_role,
        approved_context_version)
    ON nova_meta.query_autopilot_opportunities TO nova_meta_web,nova_meta_automation;

-- Retention deletes belong to the autopilot worker only: raw measurements and
-- summaries by age, evidence once expired, and finished housekeeping jobs.
-- Durable decisions (opportunities, experiments, actions, outcomes, policies,
-- enrollments) and incidents are never deleted by a workload.
CREATE POLICY query_autopilot_observations_delete ON nova_meta.query_autopilot_observations
    FOR DELETE TO nova_meta_automation USING (nova_meta.autopilot_actor_bound());
CREATE POLICY query_autopilot_rollups_delete ON nova_meta.query_autopilot_rollups
    FOR DELETE TO nova_meta_automation USING (nova_meta.autopilot_actor_bound());
CREATE POLICY query_autopilot_baselines_delete ON nova_meta.query_autopilot_baselines
    FOR DELETE TO nova_meta_automation USING (nova_meta.autopilot_actor_bound());
CREATE POLICY query_autopilot_evidence_delete ON nova_meta.query_autopilot_evidence
    FOR DELETE TO nova_meta_automation
    USING (nova_meta.autopilot_actor_bound() AND payload->>'availability'='expired');
CREATE POLICY query_autopilot_jobs_delete ON nova_meta.query_autopilot_jobs
    FOR DELETE TO nova_meta_automation
    USING (nova_meta.autopilot_actor_bound() AND state='COMPLETED'
        AND payload->>'kind' IN ('aggregate','cleanup'));
GRANT DELETE ON nova_meta.query_autopilot_observations,nova_meta.query_autopilot_rollups,
    nova_meta.query_autopilot_baselines,nova_meta.query_autopilot_evidence,
    nova_meta.query_autopilot_jobs TO nova_meta_automation;

CREATE FUNCTION nova_meta.autopilot_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Autopilot import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_query','nova_meta_proxy',
                           'nova_meta_scheduler','nova_meta_automation')
        AND TG_OP IN ('INSERT','UPDATE','DELETE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Autopilot metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;

-- The writing job must still hold the lease the result names. The job row is
-- locked FOR SHARE, so a concurrent re-claim waits for this transaction and a
-- claim that committed first makes this write fail.
CREATE FUNCTION nova_meta.autopilot_lease_held(job text, generation bigint) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $body$
    SELECT EXISTS (SELECT 1 FROM nova_meta.query_autopilot_jobs
        WHERE id=job AND state='RUNNING' AND lease_generation=generation FOR SHARE)
$body$;
REVOKE ALL ON FUNCTION nova_meta.autopilot_lease_held(text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.autopilot_lease_held(text,bigint) TO nova_meta_automation;

-- Row rules for runtime writes. Imported rows bypass them on insert; a later
-- runtime change to an imported row must still be a valid transition. One
-- branch per table: a field a table lacks is never evaluated for it.
CREATE FUNCTION nova_meta.autopilot_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE
    principal text := current_setting('nova.principal',true);
    kind text;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        RETURN NEW;
    END IF;
    IF jsonb_typeof(NEW.payload)<>'object' OR btrim(NEW.id)='' OR NEW.version<1 THEN
        RAISE EXCEPTION 'Invalid autopilot record' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        NEW.provenance:='runtime';
        IF TG_TABLE_NAME NOT IN ('query_autopilot_observations','query_autopilot_baselines')
           OR NEW.created_at IS NULL THEN
            NEW.created_at:=clock_timestamp();
        END IF;
    ELSIF ROW(NEW.id,NEW.family_id,NEW.cohort_id,NEW.created_at,NEW.provenance)
          IS DISTINCT FROM ROW(OLD.id,OLD.family_id,OLD.cohort_id,OLD.created_at,OLD.provenance) THEN
        RAISE EXCEPTION 'An autopilot record keeps its identity' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=clock_timestamp();

    IF TG_TABLE_NAME IN ('query_autopilot_policies','query_autopilot_enrollments') THEN
        IF NEW.payload->>'id' IS DISTINCT FROM NEW.id
           OR NEW.payload->>'version' IS DISTINCT FROM NEW.version::text THEN
            RAISE EXCEPTION 'A definition payload must carry its id and version' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND NEW.version<>OLD.version+1 THEN
            RAISE EXCEPTION 'A definition change advances the version by one' USING ERRCODE='23514';
        END IF;
        NEW.updated_by:=principal;
    ELSIF TG_TABLE_NAME='query_autopilot_opportunities' THEN
        IF NEW.state NOT IN ('PROPOSED','VALIDATING','INCONCLUSIVE','BLOCKED','READY_AUTO',
                'READY_APPROVAL','APPROVED','REJECTED','APPLYING','APPLIED','VERIFYING','SUCCESS',
                'NO_IMPROVEMENT','REGRESSED','ROLLED_BACK','FAILED')
           OR NEW.payload->>'id' IS DISTINCT FROM NEW.id
           OR NEW.payload->>'state' IS DISTINCT FROM NEW.state
           OR NEW.payload->>'version' IS DISTINCT FROM NEW.version::text THEN
            RAISE EXCEPTION 'Invalid autopilot candidate' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.state<>'PROPOSED' OR NEW.version<>1 OR NEW.approval_digest IS NOT NULL
               OR nullif(NEW.payload->>'approved_by','') IS NOT NULL
               OR nullif(NEW.payload->>'experiment_id','') IS NOT NULL THEN
                RAISE EXCEPTION 'A new candidate is proposed and unapproved' USING ERRCODE='23514';
            END IF;
            NEW.metadata_revision:=1;
            NEW.approved_by:=NULL; NEW.approved_role:=NULL; NEW.approved_context_version:=NULL;
        ELSE
            IF NEW.metadata_revision<>OLD.metadata_revision+1 THEN
                RAISE EXCEPTION 'A candidate change advances the revision by one' USING ERRCODE='23514';
            END IF;
            IF NEW.version<OLD.version OR NEW.version>OLD.version+1
               OR OLD.state IN ('REJECTED','SUCCESS','NO_IMPROVEMENT','ROLLED_BACK','FAILED') THEN
                RAISE EXCEPTION 'Invalid candidate transition' USING ERRCODE='23514';
            END IF;
            IF NEW.state='APPROVED' AND OLD.state<>'APPROVED' THEN
                -- Only a person, through the web workload, approves; the approval
                -- is bound to that principal, role and context version.
                IF current_user<>'nova_meta_web' OR OLD.state<>'READY_APPROVAL'
                   OR NEW.version<>OLD.version OR NEW.approval_digest IS NULL
                   OR NEW.approval_expires_at IS NULL
                   OR NEW.approval_expires_at<=clock_timestamp()
                   OR NEW.payload->>'approved_by' IS DISTINCT FROM principal
                   OR NEW.payload->>'approval_digest' IS DISTINCT FROM NEW.approval_digest THEN
                    RAISE EXCEPTION 'Invalid candidate approval' USING ERRCODE='23514';
                END IF;
                NEW.approved_by:=principal;
                NEW.approved_role:=current_setting('nova.active_role',true);
                NEW.approved_context_version:=current_setting('nova.security_context_version',true)::bigint;
            ELSIF NEW.approval_digest IS NOT NULL THEN
                -- An approval is never created outside approval, moved, or kept
                -- across a new candidate version; it may only be withdrawn.
                IF ROW(NEW.approved_by,NEW.approved_role,NEW.approved_context_version,
                       NEW.approval_digest,NEW.approval_expires_at)
                   IS DISTINCT FROM ROW(OLD.approved_by,OLD.approved_role,
                       OLD.approved_context_version,OLD.approval_digest,OLD.approval_expires_at)
                   OR NEW.version<>OLD.version THEN
                    RAISE EXCEPTION 'An approval cannot be changed or restored' USING ERRCODE='23514';
                END IF;
            END IF;
            IF NEW.state='APPROVED' AND NEW.approval_digest IS NULL THEN
                RAISE EXCEPTION 'An approved candidate needs its approval' USING ERRCODE='23514';
            END IF;
            IF NEW.state='APPLYING' AND OLD.state<>'APPLYING' THEN
                IF OLD.state='READY_AUTO' THEN
                    NULL;
                ELSIF OLD.state<>'APPROVED' OR OLD.approval_digest IS NULL
                      OR OLD.approval_expires_at<=clock_timestamp() THEN
                    RAISE EXCEPTION 'Applying needs a current approval' USING ERRCODE='23514';
                END IF;
            END IF;
            IF NEW.state='REJECTED' AND current_user<>'nova_meta_web' THEN
                RAISE EXCEPTION 'Only a person rejects a candidate' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='query_autopilot_jobs' THEN
        IF NEW.state NOT IN ('QUEUED','RUNNING','COMPLETED','BLOCKED','CHECKPOINT') THEN
            RAISE EXCEPTION 'Invalid autopilot job' USING ERRCODE='23514';
        END IF;
        kind:=NEW.payload->>'kind';
        IF TG_OP='INSERT' THEN
            NEW.lease_generation:=0; NEW.heartbeat_at:=NULL;
            IF NEW.state='CHECKPOINT' THEN
                IF current_user<>'nova_meta_automation' OR kind IS NOT NULL THEN
                    RAISE EXCEPTION 'Only the autopilot worker keeps checkpoints' USING ERRCODE='23514';
                END IF;
            ELSIF NEW.payload->>'id' IS DISTINCT FROM NEW.id
                  OR NEW.payload->>'state' IS DISTINCT FROM NEW.state
                  OR NEW.payload->>'version' IS DISTINCT FROM NEW.version::text
                  OR NEW.version<>1 THEN
                RAISE EXCEPTION 'A job starts at version one' USING ERRCODE='23514';
            ELSIF current_user='nova_meta_scheduler' THEN
                IF kind NOT IN ('aggregate','cleanup') OR NEW.state<>'QUEUED' THEN
                    RAISE EXCEPTION 'The scheduler queues housekeeping only' USING ERRCODE='23514';
                END IF;
            ELSIF current_user='nova_meta_web' THEN
                -- Approve and reject complete in the request; others queue.
                IF kind NOT IN ('experiment','approve','reject','apply')
                   OR (NEW.state='RUNNING') IS DISTINCT FROM (kind IN ('approve','reject'))
                   OR NEW.state NOT IN ('QUEUED','RUNNING')
                   OR NEW.payload->>'actor' IS DISTINCT FROM principal
                   OR NEW.payload->>'actor_role' IS DISTINCT FROM current_setting('nova.active_role',true)
                   OR nullif(NEW.payload->>'request_hash','') IS NULL THEN
                    RAISE EXCEPTION 'Invalid requested operation' USING ERRCODE='23514';
                END IF;
            ELSIF kind NOT IN ('verify','apply','aggregate','cleanup') OR NEW.state<>'QUEUED' THEN
                RAISE EXCEPTION 'The worker queues follow-up jobs only' USING ERRCODE='23514';
            END IF;
        ELSE
            IF OLD.state='CHECKPOINT' OR NEW.state='CHECKPOINT' THEN
                IF OLD.state<>NEW.state OR current_user<>'nova_meta_automation'
                   OR kind IS NOT NULL OR NEW.lease_generation<>OLD.lease_generation THEN
                    RAISE EXCEPTION 'A checkpoint stays a checkpoint' USING ERRCODE='23514';
                END IF;
            ELSIF OLD.state IN ('COMPLETED','BLOCKED') THEN
                RAISE EXCEPTION 'A finished job is frozen' USING ERRCODE='23514';
            ELSIF kind IS DISTINCT FROM OLD.payload->>'kind'
                  OR NEW.payload->>'request_hash' IS DISTINCT FROM OLD.payload->>'request_hash'
                  OR NEW.payload->>'candidate_id' IS DISTINCT FROM OLD.payload->>'candidate_id'
                  OR NEW.payload->>'actor' IS DISTINCT FROM OLD.payload->>'actor'
                  OR NEW.payload->>'actor_role' IS DISTINCT FROM OLD.payload->>'actor_role'
                  OR NEW.payload->>'state' IS DISTINCT FROM NEW.state
                  OR NEW.payload->>'version' IS DISTINCT FROM NEW.version::text
                  OR NEW.lease_generation<OLD.lease_generation
                  OR NEW.lease_generation>OLD.lease_generation+1 THEN
                RAISE EXCEPTION 'Invalid job transition' USING ERRCODE='23514';
            ELSIF NEW.lease_generation=OLD.lease_generation+1 THEN
                -- A claim: queued work starts, or an interrupted run is retaken
                -- for reconciliation. Only the worker claims.
                IF current_user<>'nova_meta_automation' OR NEW.state<>'RUNNING'
                   OR OLD.state NOT IN ('QUEUED','RUNNING') THEN
                    RAISE EXCEPTION 'Invalid job claim' USING ERRCODE='23514';
                END IF;
                NEW.heartbeat_at:=clock_timestamp();
            ELSIF current_user='nova_meta_web' THEN
                IF kind NOT IN ('approve','reject') OR OLD.state<>'RUNNING'
                   OR NEW.state<>'COMPLETED' OR OLD.lease_generation<>0 THEN
                    RAISE EXCEPTION 'Only an in-request decision is completed here' USING ERRCODE='23514';
                END IF;
            ELSIF OLD.state<>'RUNNING' THEN
                RAISE EXCEPTION 'Queued work must be claimed first' USING ERRCODE='23514';
            END IF;
            IF NEW.version IS DISTINCT FROM OLD.version
               AND NEW.version<>OLD.version+1 THEN
                RAISE EXCEPTION 'A job change advances the version by one' USING ERRCODE='23514';
            END IF;
            IF NEW.state<>OLD.state AND NEW.version<>OLD.version+1 THEN
                RAISE EXCEPTION 'A job state change advances the version' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME IN ('query_autopilot_experiments','query_autopilot_actions',
                            'query_autopilot_outcomes') THEN
        -- Fenced results: written only while their job holds this lease.
        IF NEW.job_id IS NULL OR NEW.job_generation IS NULL
           OR NOT nova_meta.autopilot_lease_held(NEW.job_id,NEW.job_generation) THEN
            RAISE EXCEPTION 'The job lease is not held' USING ERRCODE='40001';
        END IF;
        IF TG_OP='UPDATE' AND (NEW.job_id IS DISTINCT FROM OLD.job_id
                               OR NEW.job_generation<coalesce(OLD.job_generation,0)) THEN
            RAISE EXCEPTION 'A result keeps its job' USING ERRCODE='23514';
        END IF;
        IF TG_TABLE_NAME='query_autopilot_actions' THEN
            kind:=NEW.payload->>'kind';
            IF NEW.state NOT IN ('APPLYING','APPLIED') OR NEW.payload->>'id' IS DISTINCT FROM NEW.id
               OR NEW.payload->>'state' IS DISTINCT FROM NEW.state
               OR nullif(NEW.payload->>'candidate_id','') IS NULL OR nullif(kind,'') IS NULL THEN
                RAISE EXCEPTION 'Invalid autopilot action' USING ERRCODE='23514';
            END IF;
            -- An engine change keeps the evidence needed to verify it.
            IF kind IN ('EXPERIMENT_CLEANUP','COMPENSATION') THEN
                IF nullif(NEW.payload->>'owned_object_binding','') IS NULL THEN
                    RAISE EXCEPTION 'A compensation keeps the object it removes' USING ERRCODE='23514';
                END IF;
            ELSIF nullif(NEW.payload->>'binding','') IS NULL
                  OR jsonb_typeof(NEW.payload->'statement_digests')<>'array'
                  OR nullif(NEW.payload->>'actor','') IS NULL
                  OR nullif(NEW.payload->>'actor_role','') IS NULL THEN
                RAISE EXCEPTION 'An action keeps its statements, binding and actor' USING ERRCODE='23514';
            END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.state<>'APPLYING' THEN
                    RAISE EXCEPTION 'An action records its intent first' USING ERRCODE='23514';
                END IF;
            ELSIF OLD.state<>'APPLYING' OR NEW.state<>'APPLIED'
                  OR NOT ((NEW.payload-'state') @> (OLD.payload-'state'))
                  OR NEW.version<>OLD.version THEN
                -- The intent is a fact: acknowledgement adds to it once.
                RAISE EXCEPTION 'An action is acknowledged once, keeping its intent' USING ERRCODE='23514';
            ELSIF kind IN ('MATERIALIZED_VIEW','ADD_INDEX','REFRESH_POLICY','PLAN_BASELINE')
                  AND nullif(NEW.payload->>'owned_object_binding','') IS NULL THEN
                RAISE EXCEPTION 'An applied object keeps its verified identity' USING ERRCODE='23514';
            END IF;
        ELSIF TG_TABLE_NAME='query_autopilot_outcomes' THEN
            IF TG_OP='UPDATE' AND (
                   OLD.payload->>'rollback' IS DISTINCT FROM 'pending'
                   OR NEW.payload->>'rollback' IS NULL OR NEW.payload->>'rollback'='pending'
                   OR (NEW.payload-'rollback') IS DISTINCT FROM (OLD.payload-'rollback')
                   OR NEW.state<>OLD.state OR NEW.version<>OLD.version) THEN
                -- A measured outcome is a fact; only a pending rollback resolves.
                RAISE EXCEPTION 'An outcome is recorded once' USING ERRCODE='23514';
            END IF;
        ELSE
            IF nullif(NEW.payload->>'candidate_id','') IS NULL THEN
                RAISE EXCEPTION 'An experiment names its candidate' USING ERRCODE='23514';
            END IF;
            IF TG_OP='UPDATE' AND NEW.payload->>'candidate_id' IS DISTINCT FROM OLD.payload->>'candidate_id' THEN
                RAISE EXCEPTION 'An experiment keeps its candidate' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='query_autopilot_evidence' THEN
        IF NEW.payload->>'availability' NOT IN
               ('available','unavailable','expired','unsupported','unauthorized')
           OR NEW.payload->>'id' IS DISTINCT FROM NEW.id THEN
            RAISE EXCEPTION 'Invalid autopilot evidence' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' THEN
            IF ROW(NEW.payload->>'kind',NEW.payload->>'source',NEW.payload->>'collected_at',
                   NEW.payload->>'family_id',NEW.payload->>'cohort_id')
               IS DISTINCT FROM ROW(OLD.payload->>'kind',OLD.payload->>'source',
                   OLD.payload->>'collected_at',OLD.payload->>'family_id',OLD.payload->>'cohort_id')
               OR (OLD.payload->>'availability'='expired' AND NEW.payload IS DISTINCT FROM OLD.payload) THEN
                RAISE EXCEPTION 'Evidence keeps its identity; expiry is final' USING ERRCODE='23514';
            END IF;
            IF OLD.payload->>'availability'<>'unavailable'
               AND NEW.payload->>'availability'<>'expired'
               AND NEW.payload IS DISTINCT FROM OLD.payload THEN
                -- Collected evidence is a fact until it expires.
                RAISE EXCEPTION 'Collected evidence only expires' USING ERRCODE='23514';
            END IF;
            IF NEW.payload->>'availability'='expired'
               AND nullif(NEW.payload->>'payload_ref','') IS NOT NULL THEN
                RAISE EXCEPTION 'Expired evidence drops its payload' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='query_autopilot_rollups' THEN
        IF NEW.state NOT IN ('OPEN','COMPLETE') THEN
            RAISE EXCEPTION 'Invalid rollup' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END
$body$;

DO $guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['query_autopilot_families','query_autopilot_observations',
            'query_autopilot_rollups','query_autopilot_baselines','query_autopilot_evidence',
            'query_autopilot_incidents','query_autopilot_opportunities',
            'query_autopilot_experiments','query_autopilot_actions','query_autopilot_outcomes',
            'query_autopilot_policies','query_autopilot_enrollments','query_autopilot_jobs'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.autopilot_write_guard()',
            tbl||'_write_guard', tbl);
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.autopilot_row_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$guards$;

-- Policy, enrollment and candidate changes are governed actions and audited;
-- jobs, results, evidence, measurements and summaries are execution history,
-- as in the source, and are not.
CREATE FUNCTION nova_meta.audit_autopilot_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE revision bigint;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_automation')
       OR NOT nova_meta.actor_context_valid() OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('query_autopilot_policies','query_autopilot_enrollments',
                                'query_autopilot_opportunities') THEN
        RAISE EXCEPTION 'Invalid autopilot audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='query_autopilot_opportunities' THEN
        revision:=NEW.metadata_revision;
    ELSE
        revision:=NEW.version;
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,NEW.id,
        CASE WHEN TG_OP='INSERT' THEN 'CREATE'
             WHEN TG_TABLE_NAME='query_autopilot_opportunities' AND NEW.state IS DISTINCT FROM OLD.state
                 THEN NEW.state
             ELSE 'UPDATE' END,
        'SUCCESS','ALLOW',TG_TABLE_NAME,jsonb_build_object('id',NEW.id),revision);
    RETURN NULL;
END
$body$;
CREATE TRIGGER query_autopilot_policies_audit AFTER INSERT OR UPDATE
    ON nova_meta.query_autopilot_policies FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_autopilot_change();
CREATE TRIGGER query_autopilot_enrollments_audit AFTER INSERT OR UPDATE
    ON nova_meta.query_autopilot_enrollments FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_autopilot_change();
CREATE TRIGGER query_autopilot_opportunities_audit AFTER INSERT OR UPDATE
    ON nova_meta.query_autopilot_opportunities FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_autopilot_change();
