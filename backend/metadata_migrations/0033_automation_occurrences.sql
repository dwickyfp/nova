CREATE TABLE nova_meta.agent_automation_occurrences (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL UNIQUE REFERENCES nova_meta.job_queue(id),
    automation_id varchar(64) NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    scheduled_for timestamptz NOT NULL,
    definition_digest text NOT NULL CHECK (definition_digest ~ '^[0-9a-f]{64}$'),
    definition jsonb NOT NULL CHECK (jsonb_typeof(definition)='object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(automation_id,generation),
    UNIQUE(automation_id,scheduled_for)
);
CREATE TABLE nova_meta.agent_automation_receipts (
    id uuid PRIMARY KEY,
    occurrence_id uuid NOT NULL UNIQUE REFERENCES nova_meta.agent_automation_occurrences(id),
    result_digest text NOT NULL CHECK (result_digest ~ '^[0-9a-f]{64}$'),
    status varchar(64) NOT NULL,
    thread_id varchar(64),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
DO $access$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['agent_automation_occurrences','agent_automation_receipts'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY',tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY',tbl);
        EXECUTE format('GRANT SELECT,INSERT ON nova_meta.%I TO nova_meta_automation',tbl);
        EXECUTE format('CREATE POLICY worker ON nova_meta.%I TO nova_meta_automation USING (nova_meta.agent_automation_service()) WITH CHECK (nova_meta.agent_automation_service())',tbl);
        EXECUTE format('CREATE TRIGGER write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.agent_write_guard()',tbl);
    END LOOP;
END
$access$;

CREATE FUNCTION nova_meta.automation_occurrence_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM nova_meta.job_queue j
        WHERE j.id=NEW.job_id AND j.queue='automation' AND j.kind='agent.automation'
          AND j.definition_ref=NEW.id AND j.definition_digest=NEW.definition_digest
          AND j.definition_version=NEW.generation AND j.state='PENDING')
       OR NOT EXISTS (SELECT 1 FROM nova_meta.config_agent_automations a
        WHERE a.automation_id=NEW.automation_id AND a.run_generation=NEW.generation
          AND a.next_run_at=NEW.scheduled_for AND a.last_status='running'
          AND a.owner_name=NEW.definition->>'owner_name'
          AND a.role_name=NEW.definition->>'role_name') THEN
        RAISE EXCEPTION 'Automation occurrence has no matching intent' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER occurrence_guard BEFORE INSERT ON nova_meta.agent_automation_occurrences
    FOR EACH ROW EXECUTE FUNCTION nova_meta.automation_occurrence_guard();
