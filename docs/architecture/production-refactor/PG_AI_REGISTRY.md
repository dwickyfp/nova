# AI registry storage before cutover

Revision `0004_ai_registry.sql` and `ai_ml.repository.AIRegistryRepository`
prepare provider/model storage for the PG cutover. The running product still
uses the legacy AI services and StarRocks. No caller, credential resolver or
SQL function registration has switched authority. This is not a completed
provider/alias migration or live AI acceptance.

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
The legacy runtime remains affected until its caller/schema migration; R32 is
still open. The unbounded legacy key-rotation request also needs alignment.

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

Provider invocation reads metadata and ciphertext together in one query. An
agent cannot resolve an inactive provider; an administrator can preserve its
key while editing configuration. The credential object suppresses its contents
in representations and is not an API response model. Endpoint resolution and
per-request SSRF protection remain the owning service's responsibility; the
repository performs no external network I/O.

Model writes lock the provider before changing the child. Provider deletion
locks that same row, tombstones its models, tombstones the provider and clears
its ciphertext in one transaction. IDs and revisions remain reserved; stale
creates cannot reuse a deleted identity. Runtime grants prohibit physical
deletion, truncation and changes to original author/time fields. A unique index
enforces live embedding alias uniqueness, and a trigger protects immutable
embedding descriptor fields. These are metadata tests, not live embeddings.

## Remaining work

Port all callers in [WAVE_I_REVIEW.md](WAVE_I_REVIEW.md) with their authenticated
actor and expected revision. Inference callers currently running in query,
ingestion or automation must use the agent service's credential-resolution path;
the new grants deliberately do not give those workers provider keys. Web
configuration edits must preserve omitted keys and use the private administrator
projection for inactive providers.

Function-alias storage and reference guards are now prepared separately in
[revision 0007](PG_MODEL_ALIASES.md). Referenced providers/models cannot be
tombstoned; the service must compose alias deletion with provider deletion.
Default model references, native UDF registration and its durable job/receipt
remain open before any runtime port is complete. Preserve
the distinction between metadata commit and observed external registration.
Also pending: atomic audit integration, revision tokens in APIs/UI, final import
constraints/timestamp reconciliation, rehearsals, all-domain cutover and the
matching product docs. The storage tests cannot certify those paths.
