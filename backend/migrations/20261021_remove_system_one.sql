-- Retire System One (Jev) decision routing. Studio uses its configured LLM
-- planner directly, so the routing preference and decision registry entries
-- are no longer read. Deleting the providers also removes their stored keys.
DELETE FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES
WHERE user_name = '__system__' AND pref_key = 'studio_decision_mode';

DELETE FROM NOVA_SYSTEM.CONFIG_AI_MODELS WHERE type = 'decision';

DELETE FROM NOVA_SYSTEM.CONFIG_AI_MODELS
WHERE provider_id IN (
    SELECT id FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS WHERE type = 'decision'
);

DELETE FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS WHERE type = 'decision';
