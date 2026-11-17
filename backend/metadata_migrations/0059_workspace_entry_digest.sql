-- Each live workspace file records the SHA-256 of the bytes it was saved with,
-- and every read checks the object it gets back against it (NDV-STORE-004:
-- scheduled exports and other files are never served after their stored object
-- changed). Rows saved before this revision, including imported ones, have no
-- digest and are checked from their next save.
ALTER TABLE nova_meta.config_workspace_entries
    ADD COLUMN content_sha256 char(64) CHECK (content_sha256 ~ '^[0-9a-f]{64}$');

GRANT INSERT (content_sha256), UPDATE (content_sha256)
    ON nova_meta.config_workspace_entries TO nova_meta_web;
GRANT INSERT (content_sha256)
    ON nova_meta.config_workspace_entries TO nova_meta_task;
