CREATE FUNCTION nova_meta.actor_context_valid() RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $body$
    SELECT nullif(current_setting('nova.principal', true), '') IS NOT NULL
       AND nullif(current_setting('nova.active_role', true), '') IS NOT NULL
       AND current_setting('nova.security_context_version', true) ~ '^[1-9][0-9]*$'
       AND current_setting('nova.principal_type', true) IN ('USER', 'SERVICE')
$body$;
CREATE FUNCTION nova_meta.actor_is_administrator() RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $body$
    SELECT current_setting('nova.active_role', true) IN
        ('ACCOUNTADMIN', 'SECURITYADMIN', 'user_admin', 'security_admin')
$body$;
GRANT EXECUTE ON FUNCTION nova_meta.actor_context_valid(),
    nova_meta.actor_is_administrator() TO nova_meta_app, nova_meta_readonly;

CREATE TABLE nova_meta.config_ai_providers (
    id varchar(64) PRIMARY KEY CHECK (id <> ''),
    name varchar(128) NOT NULL CHECK (name <> ''),
    type varchar(32) NOT NULL CHECK (type <> ''),
    endpoint varchar(512) NOT NULL CHECK (endpoint <> ''),
    default_params text CHECK (char_length(default_params) <= 65533
        AND pg_input_is_valid(default_params, 'json')),
    is_active boolean DEFAULT true,
    created_at timestamptz DEFAULT clock_timestamp(),
    created_by varchar(128) DEFAULT nullif(current_setting('nova.principal', true), ''),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision > 0),
    is_deleted boolean NOT NULL DEFAULT false,
    has_api_key boolean NOT NULL DEFAULT false,
    api_key_masked varchar(8),
    UNIQUE (id, metadata_revision),
    CHECK (NOT is_deleted OR (is_active IS FALSE AND NOT has_api_key)),
    CHECK ((has_api_key AND api_key_masked IS NOT NULL)
        OR (NOT has_api_key AND api_key_masked IS NULL))
);

-- Ciphertext is a separate projection with its own RLS. A provider metadata
-- reader must not gain credential access merely because it can list models.
CREATE TABLE nova_meta.config_ai_provider_credentials (
    provider_id varchar(64) PRIMARY KEY,
    metadata_revision bigint NOT NULL CHECK (metadata_revision > 0),
    ciphertext text CHECK (char_length(ciphertext) <= 4096 AND ciphertext LIKE 'enc:%'),
    FOREIGN KEY (provider_id, metadata_revision)
        REFERENCES nova_meta.config_ai_providers (id, metadata_revision)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE nova_meta.config_ai_models (
    id varchar(64) PRIMARY KEY CHECK (id <> ''),
    provider_id varchar(64) NOT NULL REFERENCES nova_meta.config_ai_providers (id),
    name varchar(128) NOT NULL CHECK (name <> ''),
    display_name varchar(256),
    type varchar(32) NOT NULL CHECK (type IN ('llm', 'embedding')),
    max_tokens integer CHECK (max_tokens > 0),
    logical_alias varchar(128),
    revision varchar(128),
    dimensions integer CHECK (dimensions > 0),
    modality varchar(32),
    metric varchar(32),
    default_params text CHECK (char_length(default_params) <= 65533
        AND pg_input_is_valid(default_params, 'json')),
    is_active boolean DEFAULT true,
    created_at timestamptz DEFAULT clock_timestamp(),
    created_by varchar(128) DEFAULT nullif(current_setting('nova.principal', true), ''),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision > 0),
    is_deleted boolean NOT NULL DEFAULT false,
    CHECK (NOT is_deleted OR is_active IS FALSE),
    CHECK ((type = 'embedding' AND logical_alias IS NOT NULL AND revision IS NOT NULL
            AND dimensions IS NOT NULL AND max_tokens IS NULL
            AND logical_alias <> '' AND revision <> ''
            AND modality IS NOT NULL AND metric IS NOT NULL
            AND modality = 'text' AND metric IN ('cosine', 'l2'))
        OR (type = 'llm' AND logical_alias IS NULL AND revision IS NULL
            AND dimensions IS NULL AND modality IS NULL AND metric IS NULL))
);
CREATE INDEX ai_model_provider ON nova_meta.config_ai_models (provider_id);
CREATE UNIQUE INDEX ai_model_embedding_alias ON nova_meta.config_ai_models (logical_alias)
    WHERE type = 'embedding' AND NOT is_deleted;

CREATE FUNCTION nova_meta.ai_provider_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.metadata_revision <> OLD.metadata_revision + 1
       OR (OLD.is_deleted AND NOT NEW.is_deleted) THEN
        RAISE EXCEPTION 'Invalid provider revision' USING ERRCODE = '23514';
    END IF;
    IF NEW.is_deleted AND EXISTS (
        SELECT 1 FROM nova_meta.config_ai_models WHERE provider_id = OLD.id AND NOT is_deleted
    ) THEN
        RAISE EXCEPTION 'Provider has live models' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER ai_provider_revision_guard BEFORE UPDATE
ON nova_meta.config_ai_providers FOR EACH ROW
EXECUTE FUNCTION nova_meta.ai_provider_revision_guard();

CREATE FUNCTION nova_meta.ai_credential_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF NEW.provider_id IS DISTINCT FROM OLD.provider_id
       OR NEW.metadata_revision <> OLD.metadata_revision + 1 THEN
        RAISE EXCEPTION 'Invalid credential revision' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER ai_credential_revision_guard BEFORE UPDATE
ON nova_meta.config_ai_provider_credentials FOR EACH ROW
EXECUTE FUNCTION nova_meta.ai_credential_revision_guard();

CREATE FUNCTION nova_meta.ai_provider_credential_consistency() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE identity text; consistent boolean;
BEGIN
    IF TG_TABLE_NAME = 'config_ai_providers' THEN identity := NEW.id;
    ELSE identity := NEW.provider_id;
    END IF;
    SELECT p.has_api_key = (c.ciphertext IS NOT NULL)
        AND p.metadata_revision = c.metadata_revision
        INTO consistent FROM nova_meta.config_ai_providers p
        JOIN nova_meta.config_ai_provider_credentials c ON c.provider_id = p.id
        WHERE p.id = identity;
    IF consistent IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Incomplete provider credential revision' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$body$;
CREATE CONSTRAINT TRIGGER ai_provider_credential_consistency
AFTER INSERT OR UPDATE ON nova_meta.config_ai_providers
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION nova_meta.ai_provider_credential_consistency();
CREATE CONSTRAINT TRIGGER ai_credential_provider_consistency
AFTER INSERT OR UPDATE ON nova_meta.config_ai_provider_credentials
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION nova_meta.ai_provider_credential_consistency();

CREATE FUNCTION nova_meta.ai_model_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
DECLARE live_provider boolean;
BEGIN
    -- Serialize child changes with provider deletion, including direct writes.
    SELECT NOT is_deleted INTO live_provider
        FROM nova_meta.config_ai_providers WHERE id = NEW.provider_id FOR UPDATE;
    IF live_provider IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Provider is unavailable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.provider_id IS DISTINCT FROM OLD.provider_id
           OR NEW.type IS DISTINCT FROM OLD.type
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR NEW.metadata_revision <> OLD.metadata_revision + 1
           OR (OLD.is_deleted AND NOT NEW.is_deleted)
           OR (OLD.type = 'embedding' AND ROW(NEW.name, NEW.revision, NEW.dimensions,
                    NEW.modality, NEW.metric) IS DISTINCT FROM ROW(OLD.name, OLD.revision,
                    OLD.dimensions, OLD.modality, OLD.metric)) THEN
            RAISE EXCEPTION 'Invalid model revision' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER ai_model_revision_guard BEFORE INSERT OR UPDATE
ON nova_meta.config_ai_models FOR EACH ROW
EXECUTE FUNCTION nova_meta.ai_model_revision_guard();

ALTER TABLE nova_meta.config_ai_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_ai_providers FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_ai_provider_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_ai_provider_credentials FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_ai_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_ai_models FORCE ROW LEVEL SECURITY;

CREATE POLICY ai_provider_actor ON nova_meta.config_ai_providers AS RESTRICTIVE
    TO nova_meta_app, nova_meta_readonly USING (nova_meta.actor_context_valid());
CREATE POLICY ai_model_actor ON nova_meta.config_ai_models AS RESTRICTIVE
    TO nova_meta_app, nova_meta_readonly USING (nova_meta.actor_context_valid());
CREATE POLICY ai_credential_actor ON nova_meta.config_ai_provider_credentials AS RESTRICTIVE
    TO nova_meta_app USING (nova_meta.actor_context_valid());

CREATE POLICY ai_provider_read ON nova_meta.config_ai_providers FOR SELECT
    TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent,
       nova_meta_task, nova_meta_ingestion, nova_meta_ml, nova_meta_automation,
       nova_meta_maintenance, nova_meta_readonly USING (true);
CREATE POLICY ai_model_read ON nova_meta.config_ai_models FOR SELECT
    TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent,
       nova_meta_task, nova_meta_ingestion, nova_meta_ml, nova_meta_automation,
       nova_meta_maintenance, nova_meta_readonly USING (true);
CREATE POLICY ai_credential_read ON nova_meta.config_ai_provider_credentials FOR SELECT
    TO nova_meta_web, nova_meta_agent
    USING (current_user = 'nova_meta_agent' OR nova_meta.actor_is_administrator());

CREATE POLICY ai_provider_insert ON nova_meta.config_ai_providers FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.actor_is_administrator());
CREATE POLICY ai_provider_update ON nova_meta.config_ai_providers FOR UPDATE
    TO nova_meta_web USING (nova_meta.actor_is_administrator())
    WITH CHECK (nova_meta.actor_is_administrator());
CREATE POLICY ai_model_insert ON nova_meta.config_ai_models FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.actor_is_administrator());
CREATE POLICY ai_model_update ON nova_meta.config_ai_models FOR UPDATE
    TO nova_meta_web USING (nova_meta.actor_is_administrator())
    WITH CHECK (nova_meta.actor_is_administrator());
CREATE POLICY ai_credential_insert ON nova_meta.config_ai_provider_credentials FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.actor_is_administrator());
CREATE POLICY ai_credential_update ON nova_meta.config_ai_provider_credentials FOR UPDATE
    TO nova_meta_web USING (nova_meta.actor_is_administrator())
    WITH CHECK (nova_meta.actor_is_administrator());

GRANT SELECT ON nova_meta.config_ai_providers, nova_meta.config_ai_models
    TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent,
       nova_meta_task, nova_meta_ingestion, nova_meta_ml, nova_meta_automation,
       nova_meta_maintenance, nova_meta_readonly;
GRANT SELECT ON nova_meta.config_ai_provider_credentials TO nova_meta_web, nova_meta_agent;
GRANT INSERT (id, name, type, endpoint, default_params, is_active, has_api_key, api_key_masked),
    UPDATE (name, type, endpoint, default_params, is_active, has_api_key, api_key_masked,
            metadata_revision, is_deleted)
    ON nova_meta.config_ai_providers TO nova_meta_web;
GRANT INSERT (provider_id, metadata_revision, ciphertext), UPDATE (metadata_revision, ciphertext)
    ON nova_meta.config_ai_provider_credentials TO nova_meta_web;
GRANT INSERT (id, provider_id, name, display_name, type, max_tokens, logical_alias,
              revision, dimensions, modality, metric, default_params, is_active),
    UPDATE (name, display_name, max_tokens, logical_alias, revision, dimensions, modality,
            metric, default_params, is_active, metadata_revision, is_deleted)
    ON nova_meta.config_ai_models TO nova_meta_web;
