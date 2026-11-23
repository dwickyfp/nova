-- MCP connectors: write-only auth headers, and the tool annotations each
-- server declares. Additive only: existing rows keep their meaning (no
-- headers, no annotations).
--
-- config_mcp_servers
--   auth_headers       Fernet ciphertext ('enc:' prefix) of a JSON object of
--                      header name -> value. Never plaintext; the API returns
--                      only auth_header_names.
--   auth_header_names  The names that are set, for display.
--
-- config_tools (MCP rows)
--   annotations  The server's tool annotations from the last Discover
--                (readOnlyHint, destructiveHint, idempotentHint, openWorldHint,
--                title). Untrusted hints, shown to administrators as
--                information; they never decide how a call runs.
ALTER TABLE nova_meta.config_mcp_servers
    ADD COLUMN auth_headers text
        CHECK (auth_headers IS NULL OR (auth_headers LIKE 'enc:%' AND octet_length(auth_headers)<=65536)),
    ADD COLUMN auth_header_names jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(auth_header_names)='array' AND jsonb_array_length(auth_header_names)<=8);

ALTER TABLE nova_meta.config_tools
    ADD COLUMN annotations jsonb
        CHECK (annotations IS NULL OR jsonb_typeof(annotations)='object');

-- Only the web workload (an active ACCOUNTADMIN, by the existing platform
-- policies) writes these; agent and automation workloads keep read access.
GRANT INSERT (auth_headers,auth_header_names) ON nova_meta.config_mcp_servers TO nova_meta_web;
GRANT UPDATE (auth_headers,auth_header_names) ON nova_meta.config_mcp_servers TO nova_meta_web;
GRANT INSERT (annotations) ON nova_meta.config_tools TO nova_meta_web;
GRANT UPDATE (annotations) ON nova_meta.config_tools TO nova_meta_web;
