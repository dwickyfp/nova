CREATE TABLE nova_meta.config_user_preferences (
    user_name varchar(128) NOT NULL,
    pref_key varchar(128) NOT NULL,
    pref_value text CHECK (char_length(pref_value) <= 65533),
    updated_at timestamptz DEFAULT clock_timestamp(),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    is_deleted boolean NOT NULL DEFAULT false,
    PRIMARY KEY (user_name, pref_key),
    CHECK (NOT is_deleted OR pref_value IS NULL)
);

-- Preserve unknown legacy keys for reconciliation, without granting access to
-- them. Only the reviewed namespaces below are available to runtime identities.
CREATE FUNCTION nova_meta.preference_is_personal(key text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog AS $body$
    SELECT key IN (
        'account.profile', 'studio.theme', 'studio.language',
        'studio.preferred_name', 'studio.role', 'studio.warehouse',
        'studio.extended_thinking', 'workspace.open_tabs', 'workspace.active_tab',
        'workspace.sidebar_state', 'workspace.assistant_state',
        'workspace.last_database', 'workspace.last_schema', 'workspace.last_role'
    )
$body$;

CREATE FUNCTION nova_meta.preference_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF NEW.user_name IS DISTINCT FROM OLD.user_name
       OR NEW.pref_key IS DISTINCT FROM OLD.pref_key
       OR NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'Invalid preference revision' USING ERRCODE = '23514';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER preference_revision_guard BEFORE UPDATE
ON nova_meta.config_user_preferences FOR EACH ROW
EXECUTE FUNCTION nova_meta.preference_revision_guard();

ALTER TABLE nova_meta.config_user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_user_preferences FORCE ROW LEVEL SECURITY;

CREATE POLICY preference_actor ON nova_meta.config_user_preferences AS RESTRICTIVE
    TO nova_meta_app, nova_meta_readonly
    USING (
        nullif(current_setting('nova.principal', true), '') IS NOT NULL
        AND nullif(current_setting('nova.active_role', true), '') IS NOT NULL
        AND current_setting('nova.security_context_version', true) ~ '^[1-9][0-9]*$'
        AND current_setting('nova.principal_type', true) IN ('USER', 'SERVICE')
    );

CREATE POLICY preference_personal_read ON nova_meta.config_user_preferences
    FOR SELECT TO nova_meta_web, nova_meta_query, nova_meta_proxy,
                  nova_meta_agent, nova_meta_readonly
    USING (user_name <> '__system__'
        AND user_name = current_setting('nova.principal', true)
        AND (nova_meta.preference_is_personal(pref_key)
             OR pref_key IN ('nova.must_change_password', 'nova.news_enabled')));
CREATE POLICY preference_news_read ON nova_meta.config_user_preferences
    FOR SELECT TO nova_meta_automation
    USING (user_name <> '__system__'
        AND user_name = current_setting('nova.principal', true)
        AND pref_key = 'nova.news_enabled');
CREATE POLICY preference_admin_flags_read ON nova_meta.config_user_preferences
    FOR SELECT TO nova_meta_web, nova_meta_proxy
    USING (user_name <> '__system__'
        AND pref_key IN ('nova.must_change_password', 'nova.news_enabled')
        AND current_setting('nova.active_role', true) IN
            ('ACCOUNTADMIN', 'SECURITYADMIN', 'user_admin', 'security_admin'));
CREATE POLICY preference_default_read ON nova_meta.config_user_preferences
    FOR SELECT TO nova_meta_app, nova_meta_readonly
    USING (user_name = '__system__' AND pref_key = 'default_llm_model');
CREATE POLICY preference_setup_read ON nova_meta.config_user_preferences
    FOR SELECT TO nova_meta_web
    USING (user_name = '__system__' AND pref_key = 'setup_complete');
CREATE POLICY preference_search_credential_read ON nova_meta.config_user_preferences
    FOR SELECT TO nova_meta_web, nova_meta_agent
    USING (user_name = '__system__' AND pref_key = 'tavily_api_key');

-- UPDATE needs visibility of the old row as well as permission for the new
-- value. Password-change completion may clear only the caller's own flag.
CREATE POLICY preference_write_scope ON nova_meta.config_user_preferences AS RESTRICTIVE
    FOR ALL TO nova_meta_web, nova_meta_proxy
    USING (true)
    WITH CHECK (
        (user_name <> '__system__' AND (
            (current_user = 'nova_meta_web'
             AND user_name = current_setting('nova.principal', true)
             AND nova_meta.preference_is_personal(pref_key))
            OR (pref_key IN ('nova.must_change_password', 'nova.news_enabled') AND (
                current_setting('nova.active_role', true) IN
                    ('ACCOUNTADMIN', 'SECURITYADMIN', 'user_admin', 'security_admin')
                OR (user_name = current_setting('nova.principal', true)
                    AND pref_key = 'nova.must_change_password'
                    AND pref_value = 'false' AND NOT is_deleted)
            ))
        ))
        OR (current_user = 'nova_meta_web' AND user_name = '__system__' AND (
            (pref_key = 'setup_complete'
             AND current_setting('nova.active_role', true) = 'ACCOUNTADMIN')
            OR (pref_key IN ('default_llm_model', 'tavily_api_key')
                AND current_setting('nova.active_role', true) IN
                    ('ACCOUNTADMIN', 'SECURITYADMIN', 'user_admin', 'security_admin'))
        ))
    );
CREATE POLICY preference_insert ON nova_meta.config_user_preferences
    FOR INSERT TO nova_meta_web, nova_meta_proxy WITH CHECK (true);
CREATE POLICY preference_update ON nova_meta.config_user_preferences
    FOR UPDATE TO nova_meta_web, nova_meta_proxy USING (true) WITH CHECK (true);

REVOKE ALL ON nova_meta.config_user_preferences FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.preference_is_personal(text)
    TO nova_meta_app, nova_meta_readonly;
GRANT SELECT ON nova_meta.config_user_preferences
    TO nova_meta_web, nova_meta_query, nova_meta_proxy, nova_meta_agent,
       nova_meta_task, nova_meta_automation, nova_meta_readonly;
GRANT INSERT (user_name, pref_key, pref_value),
      UPDATE (pref_value, revision, is_deleted)
    ON nova_meta.config_user_preferences TO nova_meta_web, nova_meta_proxy;
