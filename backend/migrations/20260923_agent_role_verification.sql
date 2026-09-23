-- Existing grants start unverified. Owners must run Verify Access before an
-- agent becomes available in Nova Studio.
ALTER TABLE NOVA_SYSTEM.CONFIG_AGENT_ROLES
    ADD COLUMN verified_fingerprint VARCHAR(64);
ALTER TABLE NOVA_SYSTEM.CONFIG_AGENT_ROLES
    ADD COLUMN verified_at DATETIME;
