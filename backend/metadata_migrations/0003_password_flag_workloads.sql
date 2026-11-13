-- Managed ALTER USER password policy runs in both SQL consumers. Personal UI
-- settings and News administration remain web operations; self-clear follows
-- the web authentication service's verified password change.
ALTER POLICY preference_admin_flags_read ON nova_meta.config_user_preferences
    TO nova_meta_web, nova_meta_query, nova_meta_proxy
    USING (user_name <> '__system__'
        AND (pref_key = 'nova.must_change_password'
             OR (current_user = 'nova_meta_web' AND pref_key = 'nova.news_enabled'))
        AND current_setting('nova.active_role', true) IN
            ('ACCOUNTADMIN', 'SECURITYADMIN', 'user_admin', 'security_admin'));

ALTER POLICY preference_write_scope ON nova_meta.config_user_preferences
    TO nova_meta_web, nova_meta_query, nova_meta_proxy
    WITH CHECK (
        (user_name <> '__system__' AND (
            (current_user = 'nova_meta_web'
             AND user_name = current_setting('nova.principal', true)
             AND nova_meta.preference_is_personal(pref_key))
            OR (pref_key IN ('nova.must_change_password', 'nova.news_enabled') AND (
                (current_setting('nova.active_role', true) IN
                    ('ACCOUNTADMIN', 'SECURITYADMIN', 'user_admin', 'security_admin')
                 AND (current_user = 'nova_meta_web' OR pref_key = 'nova.must_change_password'))
                OR (current_user = 'nova_meta_web'
                    AND user_name = current_setting('nova.principal', true)
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
ALTER POLICY preference_insert ON nova_meta.config_user_preferences
    TO nova_meta_web, nova_meta_query, nova_meta_proxy;
ALTER POLICY preference_update ON nova_meta.config_user_preferences
    TO nova_meta_web, nova_meta_query, nova_meta_proxy;

GRANT INSERT (user_name, pref_key, pref_value), UPDATE (pref_value, revision, is_deleted)
    ON nova_meta.config_user_preferences TO nova_meta_query;
