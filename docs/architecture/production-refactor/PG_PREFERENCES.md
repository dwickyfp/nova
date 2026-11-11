# Preference storage before cutover

Revision `0002_user_preferences.sql` installs the first domain table,
`nova_meta.config_user_preferences`. The PG repository is
`backend/app/common/preferences.py`. Both are tested using the production
bootstrap and migrator on the existing disposable PG17 fixture. Revision
`0003_password_flag_workloads.sql` corrects grants for both SQL consumers without
rewriting revision 0002 or existing preference rows.

The running application still uses StarRocks. No caller has switched to this
repository, no second write is made, and no deployment epoch has been published.
The whole-domain cutover must move every reader/writer listed in
[WAVE_A_REVIEW.md](WAVE_A_REVIEW.md), including authentication, bootstrap, account
flags, Workspace, Studio and AI settings. This checkpoint does not complete WP03.

## Preserved data and revisions

The composite identity remains `(user_name, pref_key)`. Values remain text:
profile JSON, encrypted search-key envelopes, null values and Unicode are not
normalized or reserialized by the store. The original `updated_at` can be null;
non-null historical timestamps require the provenance recorded in the import
contract. Current engine timezone is not evidence for all historical rows.

Imported rows start at revision 1. New writes require an explicit expected
revision; zero creates a never-existing key. A batch succeeds in one transaction
or rolls back completely. The service supplies an active actor-bound connection,
so another repository can participate in the same outer transaction. Each
preference batch uses a savepoint: even a service that catches a conflict cannot
commit the earlier writes from that failed batch. Escaped repository calls
without an active transaction are refused. The service returns success only after
its outer transaction commits. Concurrent writers with the same revision
have one winner and return the existing metadata conflict (`409`) for stale revisions.
Runtime identities cannot rewrite identity columns, set timestamps, bypass the
revision increment, physically delete rows or truncate the table.

Deletion clears the value and retains a tombstone and increasing revision.
Reading a deleted key returns its revision and `deleted=true`, so its owner can
recreate it using the current revision. Old create/update requests cannot replace
the new incarnation. Tombstone retention is required; no garbage collector is
authorized to reset this history. Failed or uncertain writes are not retried.

## Access contract

Every runtime transaction supplies principal, exactly one active role, security
context version and principal type. RLS is enabled and forced. An unscoped
connection sees no rows. Workload credentials are trusted backend credentials;
these GUCs are not an authentication mechanism for untrusted SQL clients.
The SQL gateway must continue denying access to metadata and its credentials.

| Row category | Read | Write |
| --- | --- | --- |
| Reviewed profile, Workspace and Studio keys | Own rows through web/query/proxy/agent or scoped readonly | Own rows through web |
| Password-change flag | Own rows through the same readers; web/query/proxy administrators may inspect target-user flags | Web/query/proxy with active `ACCOUNTADMIN`, `SECURITYADMIN`, `user_admin` or `security_admin` |
| News entitlement | Own rows through the same readers and automation; target-user rows through web administrators | Web with one of the active administration roles above |
| Default model | Web/query/proxy/agent/task/automation and scoped readonly | Web with one of the active administration roles above |
| Setup marker | Web | Web with active `ACCOUNTADMIN` |
| Encrypted search credential | Web and agent only | Web with one of the active administration roles above |

The web caller may clear its own password-change flag to `false`; a caller without
an administration role cannot set the flag or delete it. Query/proxy cannot use
this self-clear exception. The authentication port must keep that call after verified
StarRocks password-change success. Saving `workspace.last_role` or `studio.role`
does not change the active security context or grant an entitlement.

Scheduler, ingestion, ML, notebook controller, maintenance and outbox identities
have no table access. Task and automation identities cannot read personal UI
preferences or credentials. The web service can read encrypted search settings
to compute the existing configured/hint response; it must never return the raw
repository record through an API. The agent service decrypts only for tool use.

The store refuses a new search credential unless it is an authenticated Fernet
ciphertext under the configured encryption key, either bare or in the existing
`api_key`/`hint` envelope. Unknown fields, duplicate JSON keys and non-public
hints are refused. This validation occurs before database I/O. Payload fields
are omitted from record/change representations. Existing imported ciphertext
must be preserved and separately checked with the deployment's stable key.

Unknown historical preference keys remain representable for lossless import,
but have no runtime RLS policy. They require reconciliation before cutover;
silently dropping them or making every namespace writable is not permitted.
A read-only source probe found 17 rows across 10 reviewed keys on 2026-11-11;
it exported counts and key names only. This was not a writer-frozen snapshot.

## Evidence and remaining wiring

The combined PG suite passes 99 cases without skips, including fresh install,
upgrades from revisions 1 and 2, migration rollback, PgBouncer isolation and 30 preference
cases. Preference cases cover concurrent create/update, cross-user access,
inactive administration, every workload identity, secret boundaries, saved-role
spoofing, atomic batches, lock timeout/recovery, maintenance, tombstones and
preserved imported values. The [WP03 report](../../reports/production-refactor/WP-03/README.md)
records exact commands and source versions.

The managed SQL adapter `_execute_force_password_change` runs in query and proxy
workloads. Regression tests initially exposed a missing query write grant and a
proxy self-clear overgrant. Revision 0003 grants only administrator password-flag
updates to these workloads; tests also refuse News and personal-preference writes.
The upgrade test retains an existing flag and its revision unchanged.

Still required: port every caller with its real actor and revision, preserve
pre-authentication/bootstrap semantics, atomically persist relevant audit/job
records, reconcile historical timestamps/keys, run import rehearsals and verify
one-way cutover. HTTP/UI revision tokens and user documentation must be updated
together when those runtime paths change. These storage tests do not prove live
profile/settings, authentication or an all-domain migration.
