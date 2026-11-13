# AI registry and outbound credential resolution

The runtime AI service uses `ai_ml.repository.AIRegistryRepository` and PG
actor-bound transactions. Import preserves the configured provider/model IDs
and encrypted keys. Actual activation and live AI results belong to the
[delivery report](../../reports/production-refactor/importer-agent-cutover/README.md);
passing registry storage tests does not establish live provider acceptance.

## Identities, values and credentials

Provider/model IDs, provider references, names and model descriptors retain
their original values. JSON parameters remain validated text; the repository
does not reserialize whitespace or numeric representations. Source null
timestamps/authors remain representable. Historical timestamp conversion still
requires producer/zone provenance. The model's string `revision` remains its
vendor/model descriptor. The new integer `metadata_revision` controls writes.

`CONFIG_AI_PROVIDERS.api_key` maps to the companion
`nova_meta.config_ai_provider_credentials.ciphertext`, keyed by the unchanged
provider ID. Import creates one companion row even when the original key is null.
The public provider row carries only key presence and the existing masked hint.
Import must validate ciphertext using the deployment's stable encryption key
and derive the hint without exporting plaintext. It must not replace the
original ciphertext or silently adopt a new encryption key.

The credential column permits 4,096 ciphertext characters. The source column's
512-character capacity cannot hold all encrypted values accepted by the API:
a 512-character ASCII key produces 784 ciphertext characters, and UTF-8 keys
can require more. The new repository validates authenticated ciphertext and a
nonempty plaintext of at most 512 characters before database I/O. A synthetic
512-character Unicode key round-trips through PG without API secret readback.
Provider creation, update and key replacement use this shared validation boundary.

## Transaction and access contract

The service supplies one actor-bound connection; the repository never commits
the outer transaction or retries a write. Mutations use savepoints, so a caught
conflict cannot leave a partial operation in the enclosing transaction. Provider
and credential rows share one revision through a deferred foreign key and
constraint triggers. Updating only the public row, only the secret, or key
presence without its corresponding ciphertext cannot commit.

| Workload | Provider/model metadata | Ciphertext | Writes |
| --- | --- | --- | --- |
| Web, active administrator | Read | Read, including inactive providers for configuration edits | Revisioned provider/model mutation |
| Web, other active role | Read | No rows | Refused |
| Agent | Read | Active, non-deleted providers only | Refused |
| Query, proxy, task, ingestion, ML, automation, maintenance, scoped readonly | Read | No table permission | Refused |
| Scheduler, notebook controller, outbox | No table permission | No table permission | Refused |

Every table forces RLS and requires principal, one active role, positive security
context version and USER/SERVICE principal type. Administrator roles match the
existing account-administration contract. Workload credentials are trusted
backend credentials, never credentials for user SQL. An unscoped connection
sees no rows. These policies do not replace caller-data authorization in Ranger.

Revision 0043 adds `ai_runtime_credential(text)`, a fixed SECURITY DEFINER
projection for the current outbound invocation owners: web, agent, query, proxy,
task, ingestion, ML, automation and maintenance. It requires a valid SERVICE
actor with principal `nova-ai` and active role `NOVA_WORKER`. It returns only the
requested active, non-deleted provider's ciphertext at its matching metadata
revision. The owner policies are restricted to that same runtime identity and
login allowlist; public execution is revoked. Scheduler, notebook, outbox and
readonly receive no execute grant. USER actors, including administrators, cannot
invoke this projection. Existing direct-table grants in the table above remain
unchanged.

The AI service uses that projection for outbound calls; explicit configuration
actors retain the administrator reader. Provider invocation reads metadata and
ciphertext together in one query. An
agent cannot resolve an inactive provider; an administrator can preserve its
key while editing configuration. The credential object suppresses its contents
in representations and is not an API response model. Endpoint resolution and
per-request SSRF protection remain the owning service's responsibility; the
repository performs no external network I/O.

Every provider/model mutation first acquires the same transaction-scoped
configuration lock used by aliases and function-registration intents. This order
also applies to direct repository calls. Model writes then lock the provider
before changing the child. Provider deletion
locks that same row, tombstones its models, tombstones the provider and clears
its ciphertext in one transaction. IDs and revisions remain reserved; stale
creates cannot reuse a deleted identity. Runtime grants prohibit physical
deletion, truncation and changes to original author/time fields. A unique index
enforces live embedding alias uniqueness, and a trigger protects immutable
embedding descriptor fields. These are metadata tests, not live embeddings.

## Remaining work

The current runtime still invokes providers from several backend owners.
The restricted projection makes their existing encrypted resolver work without
adding raw credential-table grants. It does not deliver the target standalone
Agent runtime or isolate ML execution. Those boundaries remain tracked in the
[delivery backlog](DELIVERY_STATUS_AND_QA_BACKLOG.md); future process moves must
move invocation ownership and revoke obsolete projection callers together.
Web configuration edits preserve omitted keys and use the private administrator
projection for inactive providers.

Function-alias storage and reference guards are now prepared separately in
[revision 0007](PG_MODEL_ALIASES.md). Referenced providers/models cannot be
tombstoned; the service must compose alias deletion with provider deletion.
The [AI configuration service](PG_AI_CONFIGURATION.md) now binds selected
function definitions to durable registration jobs. Default model references,
native UDF execution and its verified receipt remain open before any runtime
port is complete. Preserve
the distinction between metadata commit and observed external registration.
Revision 0009 adds [atomic provider/model success audit](PG_AUDIT.md), without
configuration values or encrypted credentials. Also pending: live refusal
audit, revision tokens in APIs/UI, final import
constraints/timestamp reconciliation, rehearsals, all-domain cutover and the
matching product docs. The storage tests cannot certify those paths.
