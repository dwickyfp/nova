CREATE TABLE nova_meta.config_model_aliases (
    id varchar(64) PRIMARY KEY CHECK (id <> ''),
    alias_name varchar(128) NOT NULL CHECK (alias_name <> ''),
    function_type varchar(32) NOT NULL CHECK (function_type IN
        ('complete', 'sentiment', 'classify', 'summarize', 'extract', 'translate', 'filter', 'embed')),
    provider_id varchar(64) NOT NULL REFERENCES nova_meta.config_ai_providers (id),
    model_id varchar(64) REFERENCES nova_meta.config_ai_models (id),
    system_prompt text CHECK (char_length(system_prompt) <= 65533),
    default_params text CHECK (char_length(default_params) <= 65533
        AND pg_input_is_valid(default_params, 'json')),
    is_default boolean DEFAULT true,
    is_active boolean DEFAULT true,
    created_at timestamptz DEFAULT clock_timestamp(),
    updated_at timestamptz DEFAULT clock_timestamp(),
    created_by varchar(128) DEFAULT nullif(current_setting('nova.principal', true), ''),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision > 0),
    is_deleted boolean NOT NULL DEFAULT false,
    CHECK (NOT is_deleted OR (is_active IS FALSE AND is_default IS FALSE))
);
CREATE INDEX model_alias_provider ON nova_meta.config_model_aliases (provider_id);
CREATE INDEX model_alias_model ON nova_meta.config_model_aliases (model_id) WHERE model_id IS NOT NULL;
CREATE UNIQUE INDEX model_alias_default ON nova_meta.config_model_aliases (function_type)
    WHERE is_default AND NOT is_deleted;

CREATE FUNCTION nova_meta.model_alias_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE provider_live boolean; model nova_meta.config_ai_models;
BEGIN
    IF nova_meta.actor_context_valid() IS DISTINCT FROM true
       OR nova_meta.actor_is_administrator() IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Alias write requires an administrator context' USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR NEW.metadata_revision <> OLD.metadata_revision + 1
           OR (OLD.is_deleted AND NOT NEW.is_deleted) THEN
            RAISE EXCEPTION 'Invalid alias revision' USING ERRCODE = '23514';
        END IF;
        NEW.updated_at := clock_timestamp();
    END IF;
    IF NOT NEW.is_deleted THEN
        SELECT NOT is_deleted INTO provider_live FROM nova_meta.config_ai_providers
            WHERE id = NEW.provider_id FOR UPDATE;
        IF provider_live IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'Alias provider is unavailable' USING ERRCODE = '23514';
        END IF;
        IF NEW.model_id IS NOT NULL THEN
            SELECT * INTO model FROM nova_meta.config_ai_models WHERE id = NEW.model_id FOR SHARE;
            IF model.id IS NULL OR model.is_deleted OR model.provider_id <> NEW.provider_id
               OR (NEW.function_type = 'embed' AND model.type <> 'embedding')
               OR (NEW.function_type <> 'embed' AND model.type <> 'llm') THEN
                RAISE EXCEPTION 'Alias model does not match its provider or function' USING ERRCODE = '23514';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER model_alias_guard BEFORE INSERT OR UPDATE ON nova_meta.config_model_aliases
    FOR EACH ROW EXECUTE FUNCTION nova_meta.model_alias_guard();

CREATE FUNCTION nova_meta.ai_registry_alias_reference_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF NEW.is_deleted AND NOT OLD.is_deleted THEN
        IF (TG_TABLE_NAME = 'config_ai_providers' AND EXISTS (
                SELECT 1 FROM nova_meta.config_model_aliases WHERE provider_id = OLD.id AND NOT is_deleted
            )) OR (TG_TABLE_NAME = 'config_ai_models' AND EXISTS (
                SELECT 1 FROM nova_meta.config_model_aliases WHERE model_id = OLD.id AND NOT is_deleted
            )) THEN
            RAISE EXCEPTION 'Registry object is referenced by a model alias' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER ai_provider_alias_reference_guard BEFORE UPDATE ON nova_meta.config_ai_providers
    FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_registry_alias_reference_guard();
CREATE TRIGGER ai_model_alias_reference_guard BEFORE UPDATE ON nova_meta.config_ai_models
    FOR EACH ROW EXECUTE FUNCTION nova_meta.ai_registry_alias_reference_guard();

ALTER TABLE nova_meta.config_model_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_model_aliases FORCE ROW LEVEL SECURITY;
CREATE POLICY model_alias_actor ON nova_meta.config_model_aliases AS RESTRICTIVE
    TO nova_meta_app, nova_meta_readonly USING (nova_meta.actor_context_valid());
CREATE POLICY model_alias_read ON nova_meta.config_model_aliases FOR SELECT
    TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent,
       nova_meta_task, nova_meta_ingestion, nova_meta_ml, nova_meta_automation,
       nova_meta_maintenance, nova_meta_readonly USING (true);
CREATE POLICY model_alias_insert ON nova_meta.config_model_aliases FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.actor_is_administrator());
CREATE POLICY model_alias_update ON nova_meta.config_model_aliases FOR UPDATE TO nova_meta_web
    USING (nova_meta.actor_is_administrator()) WITH CHECK (nova_meta.actor_is_administrator());
GRANT SELECT ON nova_meta.config_model_aliases
    TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent,
       nova_meta_task, nova_meta_ingestion, nova_meta_ml, nova_meta_automation,
       nova_meta_maintenance, nova_meta_readonly;
GRANT INSERT (id, alias_name, function_type, provider_id, model_id, system_prompt,
              default_params, is_default, is_active),
    UPDATE (alias_name, function_type, provider_id, model_id, system_prompt, default_params,
            is_default, is_active, metadata_revision, is_deleted)
    ON nova_meta.config_model_aliases TO nova_meta_web;
