CREATE FUNCTION nova_meta.ai_function_snapshot(requested_type text) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path = pg_catalog AS $body$
DECLARE a nova_meta.config_model_aliases; p nova_meta.config_ai_providers;
        m nova_meta.config_ai_models; parameters jsonb;
BEGIN
    IF requested_type NOT IN
        ('complete','sentiment','classify','summarize','extract','translate','filter','embed') THEN
        RAISE EXCEPTION 'Invalid function type' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO a FROM nova_meta.config_model_aliases
        WHERE function_type=requested_type AND NOT is_deleted
          AND (is_default IS TRUE OR is_active IS TRUE)
        ORDER BY (is_default IS TRUE) DESC,alias_name,id LIMIT 1;
    IF a.id IS NULL THEN
        RETURN jsonb_build_object('contract_version',1,'function_type',requested_type,'mode','unconfigured',
            'alias',NULL,'provider',NULL,'model',NULL,'parameters','{}'::jsonb);
    END IF;
    SELECT * INTO p FROM nova_meta.config_ai_providers WHERE id=a.provider_id AND NOT is_deleted;
    SELECT * INTO m FROM nova_meta.config_ai_models WHERE id=a.model_id AND NOT is_deleted;
    IF p.id IS NULL OR (a.model_id IS NOT NULL AND m.id IS NULL) THEN
        RAISE EXCEPTION 'Function configuration has an unavailable reference' USING ERRCODE = '23514';
    END IF;
    SELECT coalesce(jsonb_object_agg(key,value),'{}'::jsonb) INTO parameters
        FROM jsonb_each(coalesce(a.default_params::jsonb,'{}'::jsonb))
        WHERE key IN ('temperature','max_tokens','top_p','timeout_ms') AND value<>'null'::jsonb;
    RETURN jsonb_build_object(
        'contract_version',1,'function_type',requested_type,
        'mode',CASE WHEN a.is_active IS TRUE AND p.is_active IS TRUE
                         AND (a.model_id IS NULL OR m.is_active IS TRUE)
                    THEN 'configured' ELSE 'disabled' END,
        'alias',jsonb_build_object('id',a.id,'revision',a.metadata_revision,
            'name',a.alias_name,'active',a.is_active,'system_prompt',a.system_prompt),
        'provider',jsonb_build_object('id',p.id,'revision',p.metadata_revision,'active',p.is_active),
        'model',CASE WHEN m.id IS NULL THEN NULL ELSE jsonb_build_object('id',m.id,
            'revision',m.metadata_revision,'name',m.name,'type',m.type,'active',m.is_active,
            'max_tokens',m.max_tokens,'logical_alias',m.logical_alias,'descriptor_revision',m.revision,
            'dimensions',m.dimensions,'modality',m.modality,'metric',m.metric) END,
        'parameters',parameters);
END
$body$;
GRANT EXECUTE ON FUNCTION nova_meta.ai_function_snapshot(text) TO nova_meta_web;

CREATE TABLE nova_meta.ai_function_definitions (
    id uuid PRIMARY KEY,
    function_type varchar(32) NOT NULL CHECK (function_type IN
        ('complete','sentiment','classify','summarize','extract','translate','filter','embed')),
    version bigint NOT NULL CHECK (version > 0),
    payload text NOT NULL CHECK (octet_length(payload)<=1048576 AND pg_input_is_valid(payload,'jsonb')),
    digest text NOT NULL CHECK (digest=encode(sha256(convert_to(payload,'UTF8')),'hex')),
    owner_principal varchar(128) NOT NULL DEFAULT current_setting('nova.principal'),
    owner_role varchar(128) NOT NULL DEFAULT current_setting('nova.active_role'),
    principal_type text NOT NULL DEFAULT current_setting('nova.principal_type'),
    security_context_version bigint NOT NULL DEFAULT current_setting('nova.security_context_version')::bigint,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (function_type,version),
    UNIQUE (id,function_type,version,digest)
);
CREATE TABLE nova_meta.ai_function_heads (
    function_type varchar(32) PRIMARY KEY,
    definition_id uuid NOT NULL,
    version bigint NOT NULL,
    digest text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (definition_id,function_type,version,digest)
        REFERENCES nova_meta.ai_function_definitions (id,function_type,version,digest)
);

CREATE FUNCTION nova_meta.ai_definition_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE prior_version bigint; item record;
BEGIN
    PERFORM pg_advisory_xact_lock(1313822273,1095518529);
    SELECT version INTO prior_version FROM nova_meta.ai_function_heads
        WHERE function_type=NEW.function_type FOR UPDATE;
    IF NEW.version<>coalesce(prior_version,0)+1
       OR NEW.payload::jsonb IS DISTINCT FROM nova_meta.ai_function_snapshot(NEW.function_type) THEN
        RAISE EXCEPTION 'Function definition does not match current configuration' USING ERRCODE='23514';
    END IF;
    FOR item IN SELECT * FROM jsonb_each(NEW.payload::jsonb->'parameters') LOOP
        IF jsonb_typeof(item.value)<>'number' THEN
            RAISE EXCEPTION 'Invalid function parameter' USING ERRCODE='23514';
        END IF;
        IF (item.key='temperature' AND (item.value::numeric<0 OR item.value::numeric>2))
           OR (item.key='top_p' AND (item.value::numeric<0 OR item.value::numeric>1))
           OR (item.key IN ('max_tokens','timeout_ms') AND (item.value::numeric<=0
               OR item.value::numeric>2147483647 OR trunc(item.value::numeric)<>item.value::numeric)) THEN
            RAISE EXCEPTION 'Invalid function parameter' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END
$body$;
CREATE TRIGGER ai_definition_guard BEFORE INSERT ON nova_meta.ai_function_definitions
    FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_definition_guard();

CREATE FUNCTION nova_meta.ai_function_head_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE expected_version bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(1313822273,1095518529);
    IF TG_OP='INSERT' THEN
        SELECT version INTO expected_version FROM nova_meta.ai_function_heads
            WHERE function_type=NEW.function_type FOR UPDATE;
        expected_version:=coalesce(expected_version,0)+1;
    ELSE
        expected_version:=OLD.version+1;
        IF NEW.function_type<>OLD.function_type THEN
            RAISE EXCEPTION 'Invalid function head identity' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.version<>expected_version THEN
        RAISE EXCEPTION 'Invalid function head revision' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER ai_function_head_guard BEFORE INSERT OR UPDATE ON nova_meta.ai_function_heads
    FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_function_head_guard();

CREATE FUNCTION nova_meta.ai_function_job_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE d nova_meta.ai_function_definitions;
BEGIN
    IF NEW.kind<>'ai.function.reconcile' THEN RETURN NEW; END IF;
    SELECT * INTO d FROM nova_meta.ai_function_definitions WHERE id=NEW.definition_ref;
    IF d.id IS NULL OR NEW.queue<>'maintenance' OR NOT NEW.external_effect
       OR NEW.definition_version<>d.version OR NEW.definition_digest<>d.digest
       OR NEW.resource_key<>encode(sha256(convert_to('nova.ai.function.'||d.function_type,'UTF8')),'hex')
       OR ROW(NEW.owner_principal,NEW.owner_role,NEW.principal_type,NEW.security_context_version)
          IS DISTINCT FROM ROW(d.owner_principal,d.owner_role,d.principal_type,d.security_context_version) THEN
        RAISE EXCEPTION 'Function job does not match its definition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER ai_function_job_guard BEFORE INSERT ON nova_meta.job_queue
    FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_function_job_guard();
CREATE UNIQUE INDEX ai_function_job_definition ON nova_meta.job_queue (definition_ref)
    WHERE kind='ai.function.reconcile';

CREATE FUNCTION nova_meta.ai_definition_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM nova_meta.ai_function_heads
            WHERE function_type=NEW.function_type AND version>=NEW.version)
       OR NOT EXISTS (SELECT 1 FROM nova_meta.job_queue WHERE definition_ref=NEW.id
            AND definition_version=NEW.version AND definition_digest=NEW.digest
            AND kind='ai.function.reconcile') THEN
        RAISE EXCEPTION 'Function definition requires its head and durable job' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END
$body$;
CREATE CONSTRAINT TRIGGER ai_definition_complete AFTER INSERT ON nova_meta.ai_function_definitions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_definition_complete();

CREATE FUNCTION nova_meta.ai_configuration_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE types text[]; selected_type text; snapshot jsonb; recorded jsonb;
BEGIN
    -- Runtime writes require an intent. Cutover must separately validate
    -- privileged imports before publishing the metadata epoch.
    IF NOT pg_has_role(current_user,'nova_meta_app','MEMBER') THEN RETURN NULL; END IF;
    IF TG_TABLE_NAME='config_model_aliases' THEN
        types:=ARRAY[NEW.function_type];
        IF TG_OP='UPDATE' THEN types:=array_append(types,OLD.function_type); END IF;
    ELSIF TG_TABLE_NAME='config_ai_providers' THEN
        SELECT array_agg(DISTINCT function_type) INTO types FROM nova_meta.config_model_aliases
            WHERE provider_id=NEW.id AND NOT is_deleted;
    ELSE
        SELECT array_agg(DISTINCT function_type) INTO types FROM nova_meta.config_model_aliases
            WHERE model_id=NEW.id AND NOT is_deleted;
    END IF;
    FOREACH selected_type IN ARRAY coalesce(types,ARRAY[]::text[]) LOOP
        snapshot:=nova_meta.ai_function_snapshot(selected_type);
        SELECT d.payload::jsonb INTO recorded FROM nova_meta.ai_function_heads h
            JOIN nova_meta.ai_function_definitions d ON d.id=h.definition_id
            WHERE h.function_type=selected_type;
        IF recorded IS NULL AND snapshot->>'mode'='unconfigured' THEN CONTINUE; END IF;
        IF snapshot IS DISTINCT FROM recorded THEN
            RAISE EXCEPTION 'AI configuration requires its registration intent' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NULL;
END
$body$;
CREATE CONSTRAINT TRIGGER ai_alias_configuration_complete AFTER INSERT OR UPDATE
    ON nova_meta.config_model_aliases DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_configuration_complete();
CREATE CONSTRAINT TRIGGER ai_provider_configuration_complete AFTER INSERT OR UPDATE
    ON nova_meta.config_ai_providers DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_configuration_complete();
CREATE CONSTRAINT TRIGGER ai_model_configuration_complete AFTER INSERT OR UPDATE
    ON nova_meta.config_ai_models DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_configuration_complete();

ALTER TABLE nova_meta.ai_function_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ai_function_definitions FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ai_function_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.ai_function_heads FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_definition_actor ON nova_meta.ai_function_definitions AS RESTRICTIVE
    TO nova_meta_app,nova_meta_readonly USING (nova_meta.actor_context_valid());
CREATE POLICY ai_head_actor ON nova_meta.ai_function_heads AS RESTRICTIVE
    TO nova_meta_app,nova_meta_readonly USING (nova_meta.actor_context_valid());
CREATE POLICY ai_definition_admin_read ON nova_meta.ai_function_definitions FOR SELECT
    TO nova_meta_web USING (nova_meta.actor_is_administrator());
CREATE POLICY ai_head_admin_read ON nova_meta.ai_function_heads FOR SELECT
    TO nova_meta_web USING (nova_meta.actor_is_administrator());
CREATE POLICY ai_definition_worker_read ON nova_meta.ai_function_definitions FOR SELECT
    TO nova_meta_maintenance USING (current_setting('nova.principal_type',true)='SERVICE');
CREATE POLICY ai_head_worker_read ON nova_meta.ai_function_heads FOR SELECT
    TO nova_meta_maintenance USING (current_setting('nova.principal_type',true)='SERVICE');
CREATE POLICY ai_definition_auditor_read ON nova_meta.ai_function_definitions FOR SELECT
    TO nova_meta_readonly USING (true);
CREATE POLICY ai_head_auditor_read ON nova_meta.ai_function_heads FOR SELECT
    TO nova_meta_readonly USING (true);
CREATE POLICY ai_definition_insert ON nova_meta.ai_function_definitions FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.actor_is_administrator());
CREATE POLICY ai_head_insert ON nova_meta.ai_function_heads FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.actor_is_administrator());
CREATE POLICY ai_head_update ON nova_meta.ai_function_heads FOR UPDATE TO nova_meta_web
    USING (nova_meta.actor_is_administrator()) WITH CHECK (nova_meta.actor_is_administrator());
GRANT SELECT ON nova_meta.ai_function_definitions,nova_meta.ai_function_heads
    TO nova_meta_web,nova_meta_maintenance,nova_meta_readonly;
GRANT INSERT (id,function_type,version,payload,digest) ON nova_meta.ai_function_definitions
    TO nova_meta_web;
GRANT INSERT (function_type,definition_id,version,digest),UPDATE (definition_id,version,digest)
    ON nova_meta.ai_function_heads TO nova_meta_web;
