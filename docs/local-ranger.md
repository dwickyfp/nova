# Local Ranger configuration

The operator requested local credential repair and Docker configuration alignment
on 2026-10-23. Credential values are stored only in ignored, owner-readable files:

- `docker/.env`: separate `RANGER_ADMIN_PASSWORD` and `RANGER_DB_PASSWORD`.
- `backend/.env`: the matching Ranger client credential; existing unrelated
  application settings are preserved.
- `backend/.env.ranger.local`: explicit governed acceptance endpoints, signing
  configuration and Ranger credential. This file is not a production template.

The local administrator login is `admin`. Read its password from the environment
file locally; do not paste it into chat, command arguments, screenshots or logs.
After integrating the Ranger-only upstream change, obsolete `RANGER_ENABLED` and
`RANGER_STRICT_SINGLE_ACTIVE_ROLE` entries were removed from the local files.
Current code always requires Ranger and one active role; these are no longer
configuration switches. Credential values were retained.

## Existing Docker project

The existing `docker` project's temporary override was missing. Its running
container configuration was backed up and its resource settings and patched FE
image were retained in the ignored `docker/data/runtime-overrides.json` file.
Use the complete file list from the repository root:

```sh
docker compose --env-file docker/.env -p docker \
  -f docker/docker-compose-engine.yml \
  -f docker/docker-compose.dev.yml \
  -f docker/data/runtime-overrides.json ps
```

Only `ranger-admin` and `ranger-policy-proxy` are recreated for this repair.
Do not recreate FE/BE, delete volumes or rerun the disposable seed script against
this project. The separate `nova-smart-unlimited` project is the disposable test
stack and does not establish Ranger acceptance.

The pinned Ranger image initializes its administrator with the database password.
`docker/ranger/admin-start.sh` changes that one initialization setting to use the
separate administrator secret and leaves service provisioning to Nova's existing
`ranger-bootstrap`. The adapter rejects an unexpected upstream script layout.
Existing accounts are not rotated merely by editing an environment file.

## Recovery record

Restricted backups are under `docker/data/ranger-recovery/20261023T005412Z/`:
the original local environment files, live container configurations, Ranger DB
dump and the administrator record. Preserve these until recovery is verified;
they contain sensitive material and must remain outside Git.

The local admin password was updated using the installed Ranger `UserMgr` service
inside a transaction with Ranger password-change auditing enabled. The plaintext
was supplied on stdin, not in command arguments. Only the password and its update
timestamp changed in the portal account record.

Ranger's five-minute login lock is based on failed authentication records, including
locked attempts. Repeated old-credential proxy/health requests kept it locked.
The repair retains login history and autolock settings; a temporary maintenance
pause lets the configured window expire before the corrected clients reconnect.

Relevant upstream implementation: [Ranger UserMgr](https://github.com/apache/ranger/blob/release-ranger-2.9.0/security-admin/src/main/java/org/apache/ranger/biz/UserMgr.java)
and [SessionMgr](https://github.com/apache/ranger/blob/release-ranger-2.9.0/security-admin/src/main/java/org/apache/ranger/biz/SessionMgr.java).

## Verification

After Admin and the policy proxy are healthy, verify authenticated service/role
reads, then run the governed Studio acceptance from `backend/`:

```sh
uv run --env-file .env.ranger.local pytest \
  tests/integration/test_governed_studio_ranger.py -v --tb=short
```

The fixture creates uniquely named test users, roles, policies and data, then
cleans them up. A healthy HTTP root page alone does not establish authentication;
the Docker health check requests the authenticated service endpoint.

Verified on 2026-10-23: authenticated service and role reads returned HTTP 200,
Admin and policy proxy were healthy, and all four local environment/override
files above are Git-ignored with mode `600`. The admin secret in all three local
environment files matches and differs from the database secret.
