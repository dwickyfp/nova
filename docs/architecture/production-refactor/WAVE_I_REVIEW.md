# Direct connections and cursor writers

The expanded scan adds 84 connection/cursor candidates to the original 791.
This includes caller-data queries in mixed functions: candidates are reviewed
by SQL and identity, not automatically treated as metadata.

Provider/model CRUD and the API-key update route write `CONFIG_AI_PROVIDERS`
and `CONFIG_AI_MODELS` through direct connections. AI function aliases also read
those stores and write `CONFIG_MODEL_ALIASES`. These writes move to PG. Default
alias replacement, provider/model deletion and alias uniqueness checks need
transactions and constraints. Preserve encrypted provider keys and IDs without
plaintext export. Reading a provider for invocation is a restricted secret
resolution operation, distinct from its masked public projection.

`LLMFunctionService._connect` additionally registers and observes native UDFs;
its factory is mixed. The CREATE/DROP/SHOW GLOBAL FUNCTION paths remain a
restricted engine-administration responsibility, with operation receipts and
redacted diagnostics. An alias metadata commit must not imply that registration
on the engine succeeded.

ML registry reservation, publication, run records and aliases are PG metadata.
The current MAX-version reservation and status-only publication rely on Redis
and process-local locks. The reservation token must fence publication in PG;
an old training worker must not replace a newer result. Model/version/current
pointer publication must transact with the artifact receipt. Model deletion
must retain artifact provenance and unresolved external cleanup. Existing
tenant, owner and database scopes remain distinct, including legacy alias
scopes. Preserve signed/unsigned provenance status rather than trusting imported
pickle bytes merely because their checksum matches.

Ephemeral ML descriptors are durable despite their name. Preserve run ID,
scope key, fingerprint, descriptor and epoch expiry; local cache eviction must
not delete referenced artifacts. Service-level list/get/delete calls also read
these ML metadata stores through the registry connection and must move with it.

Migration source configuration and schema inspection are PG metadata. The
legacy MigrationRepository factory currently hardcodes a blank root password
and also observes target engine backends. This path must be removed by the
metadata/admin split; it cannot support a secured deployment unchanged. Source
database connections are separate, explicitly configured user-data adapters,
and are not Nova PG metadata connections.

User administration's raw factory is mixed: SHOW USERS/AUTHENTICATION/role
edges remain engine observations, while last-login aggregation moves to PG
audit. Login/password verification and the authenticated role lookup stay with
StarRocks. The monitoring blank-root probe is a security observation, not a
fallback credential path. Core user connections remain caller data; the core
system pool remains mixed until all consumers are replaced.

Stage cursor CRUD and buffered AI usage are the same metadata writers reviewed
in Waves A/H, now inventoried at the terminal SQL call as well. Feature training
range/preview reads and Search's final governed source fetch use QueryService
with the caller identity; these three new candidates are engine data, not PG.
Catalog source discovery uses its configured connection and only observations.

Direct AI/ML root connections do not pin their session timezone. Their NOW or
DEFAULT timestamps follow the engine's historical session/global setting;
import cannot assume that Python's configured timezone alone proves provenance.
Record and verify the source producer/zone before transforming these fields.
