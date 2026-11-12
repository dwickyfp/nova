CREATE TABLE nova_meta.agent_automation_deliveries (
    occurrence_id uuid PRIMARY KEY REFERENCES nova_meta.agent_automation_occurrences(id),
    job_id uuid NOT NULL UNIQUE REFERENCES nova_meta.job_queue(id),
    lease_generation bigint NOT NULL CHECK (lease_generation>0),
    request_digest text NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    request jsonb NOT NULL CHECK (jsonb_typeof(request)='object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE nova_meta.agent_automation_delivery_receipts (
    occurrence_id uuid PRIMARY KEY REFERENCES nova_meta.agent_automation_deliveries(occurrence_id),
    response_digest text NOT NULL CHECK (response_digest ~ '^[0-9a-f]{64}$'),
    is_error boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
DO $access$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['agent_automation_deliveries','agent_automation_delivery_receipts'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY',tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY',tbl);
        EXECUTE format('GRANT SELECT,INSERT ON nova_meta.%I TO nova_meta_automation',tbl);
        EXECUTE format('CREATE POLICY worker ON nova_meta.%I TO nova_meta_automation
            USING (nova_meta.agent_automation_service())
            WITH CHECK (nova_meta.agent_automation_service())',tbl);
        EXECUTE format('CREATE TRIGGER write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
            ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.agent_write_guard()',tbl);
    END LOOP;
END
$access$;
CREATE FUNCTION nova_meta.automation_delivery_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM nova_meta.job_queue j
        JOIN nova_meta.agent_automation_occurrences o ON o.job_id=j.id
        WHERE j.id=NEW.job_id AND j.definition_ref=NEW.occurrence_id
            AND j.lease_token=NEW.lease_generation AND j.state='PREPARED'
            AND j.lease_expires_at>clock_timestamp()
            AND o.definition->>'owner_name'=NEW.request->>'owner_name'
            AND o.definition->>'role_name'=NEW.request->>'role_name'
            AND o.definition->'delivery'->>'mcp_tool_id'=NEW.request->>'tool_id') THEN
        RAISE EXCEPTION 'Delivery has no active prepared occurrence' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER delivery_guard BEFORE INSERT ON nova_meta.agent_automation_deliveries
    FOR EACH ROW EXECUTE FUNCTION nova_meta.automation_delivery_guard();
