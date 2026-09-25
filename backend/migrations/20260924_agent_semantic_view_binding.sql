-- Canonical Agent Studio binding. Existing model columns remain for a read-only
-- migration bridge; migrated Semantic Views retain the same IDs.
ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN semantic_view_ids JSON;
