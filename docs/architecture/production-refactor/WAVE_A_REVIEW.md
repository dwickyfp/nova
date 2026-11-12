# Workspace, preference, stage and audit review

This review classifies the source calls named below. It does not close WP00 or
authorize migration. The remaining domains and all cutover gates are still open.

## Preference ownership

`CONFIG_USER_PREFERENCES` is shared infrastructure, not the SQL variables
domain. Preserve its `(user_name, pref_key)` identity and text value representation.
The SQL variables module operates on the caller's engine session.

| Key family | Logical owner and current writer | Required scope |
| --- | --- | --- |
| `nova.must_change_password` | `common/user_flags.py` | Account administration and the authenticated password-change flow |
| `nova.news_enabled` | `common/news_entitlement.py` | Administrator write, current principal read |
| `account.profile` | `auth/profile.py` | Current principal only |
| `workspace.*` | `workspaces/service.py` through `workspaces/repository.py` | Current principal's fixed UI keys; never arbitrary reserved keys |
| `studio.*` | `agents/studio_service.py` | Current principal; fixed theme/language/name/role/warehouse/thinking keys |
| `setup_complete` | `common/nova_system.py`, owner `__system__` | Bootstrap/setup service only |
| `default_llm_model` | `ai_ml/default_model.py`, owner `__system__` | Active administrator write; resolved model reference |
| `tavily_api_key` | `ai_ml/web_search_settings.py`, owner `__system__` | Active administrator write; tool-only secret resolution |

Values are not uniformly JSON. Keep booleans, scalar text, JSON and encrypted
secret envelopes byte-preserving unless a separately reviewed conversion is
required. The external-search value contains authenticated ciphertext and a
fixed public hint. Do not log values or put them in migration manifests.
All these current preference writers use engine `NOW()`; their timestamp
conversion must use the verified source session timezone, not assume UTC.

The preference storage review verified the router boundary again:
`get_workspace_tree` and `save_workspace_state` pass `user["username"]` to
preference consumers. `get_shared_tree` reads entries through the resolved
shared namespace without reading preferences. Shared file authorization below
must not be incorrectly applied to these personal UI preference rows.

## Workspace constraints and authorization

`SpaceRepository` owns workspace names, ownership and role shares. Shared files
use namespace `~ws/<id>` in the entry/version tables. The sharing contract uses
assigned roles regardless of the currently active role. Query execution still
uses the caller's single active role and Ranger. A PG policy limited to
`user_name = principal` would break shared workspaces; an unrestricted service
role would remove the intended boundary.

`AccessControlService.revoke_role` currently updates Ranger and the engine but
does not invalidate cached sessions. `SpaceService._shared` consumes cached
assignments. This leaves revoked file access until session renewal. The new
security-revision/revocation design must retire stale sessions and delegated
tokens and prove concurrent revocation cannot create a fresh stale session.

`WorkspaceRepository.next_version_number` reads `MAX(version)+1` separately from
the insert. Object storage keys also include this number. New saves require a
transactionally reserved revision, immutable object receipt and CAS publication;
an application-only existence check cannot prevent concurrent overwrites.
Workspace deletion plus share removal, and share replacement plus all its
inserts, each become one metadata transaction. File create/rename/save/delete
must coordinate object receipts and metadata rather than claim cross-store
atomicity from a PG transaction.

Read-only aggregate evidence is in `CONSTRAINT_PROFILE.json`. It found two
duplicate `(entry_id, user_name, version)` groups in the current development
data. Neither group has differing non-null object-key/etag/size values in the
aggregate comparison; actual object contents have not been validated.
The remaining checked workspace path, orphan, namespace, enum, size and
null-deletion candidates had zero violations at observation time. These are
non-transactional observations while the application remains writable.

Do not impose a unique revision constraint and drop colliding rows. Preserve
every opaque version ID and source row, verify object digests and historical
payload availability, then define explicit conflict reconciliation. Import must
stop with a redacted conflict manifest until that policy is implemented. Counts
alone do not prove the historical objects are intact.

A subsequent read-only object probe found four source rows in the same two
groups. Each group referenced one existing object; stored lengths and ETags
matched the bytes read, and the duplicate metadata was unchanged between the
probe's two reads. This checks currently available payloads, without a writer
freeze or reconstruction of overwritten historical bytes. All IDs and rows
remain untouched. See the [bounded evidence](../../reports/production-refactor/workspace-sharing/historical-object-profile.json).

Revision 0013 and [the Workspace store](PG_WORKSPACES.md) now prepare ownership
and shares with forced RLS, aggregate revision CAS, atomic replacement and
transactional success audit. File/version storage, object receipts, runtime
caller ports and session revocation remain open. This does not close R19 or
activate PostgreSQL authority.

## Stages and audit

The four `StageService.system_conn` blocks only read/write `CONFIG_STAGES`.
Their storage actions remain in the existing managed-storage path, after stage
authorization. Stage identity stays `id`; qualified-name uniqueness additionally
needs consistent engine case semantics and concurrent-create tests. The observed
qualified-name duplicate count is zero, which is not a concurrency guarantee.

`common/audit.py` generates INSERTs only into `AUDIT_LOG`, including a bounded
batch and a narrow compatibility retry for proven statement rejection. Its
event timestamp uses engine `NOW()`. The PG application role needs append-only
grants and partition-aware retention through a separate maintenance role.
Current `log_id` values had no duplicates, but the source Duplicate Key model
does not enforce that property. Preserve the source ID and every row; derive a
separate import identity for collisions rather than silently deduplicate.

The ownership CSV remains provisional for unreviewed consumers. Source-call
classification means the SQL destination was inspected; it does not mean the
caller's authorization, crash recovery or target repository has passed QA.
