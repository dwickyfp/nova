# Module 29: Ranger Access Control

> Nova manages authorization centrally in Apache Ranger while StarRocks roles remain session markers.

---

## Concept/Overview

Every user-data operation carries a `SecurityContext` with a principal and
exactly one active role. StarRocks authenticates the principal and validates
that the marker role is assigned. The patched FE sends that same role to Ranger
for object access, row filtering, and masking.

```text
client -> Nova session -> SecurityContext(principal, active_role)
       -> SET ROLE active_role -> patched StarRocks FE
       -> Ranger request userRoles={active_role}
       -> permission + row filter + mask -> result
```

Ranger is the policy store and runtime authority. Nova is the management plane.
Native StarRocks object grants are not a fallback in full Ranger mode.

The invariant is:

```text
Every operation that can observe, derive, transform, export, cache, persist,
embed, or train on user data must use a valid SecurityContext. No user-data
operation may use root. Ranger enforcement happens in StarRocks, not in a UI,
prompt, or injected WHERE clause.
```

## Operations

Start the complete local stack:

```bash
cp docker/.env.example docker/.env
docker compose --env-file docker/.env \
  -f docker/docker-compose-engine.yml --profile app up --build -d
docker compose --env-file docker/.env \
  -f docker/docker-compose-engine.yml ps
```

After every service is healthy, run the deterministic real-stack acceptance
suite from the backend container:

```bash
docker compose --env-file docker/.env \
  -f docker/docker-compose-engine.yml exec nova-backend \
  python scripts/verify_ranger_e2e.py
```

It verifies connection-time `nova_role`, role switching, row filtering,
masking, invalid-role rejection, the public `root` guard, and Nova Studio's
`query_execute` tool against the live Ranger and patched FE.

The stack contains Ranger Admin 2.9.0, its PostgreSQL database, a health-checked
Solr service, the idempotent Ranger bootstrap, patched StarRocks 4.1.4 FE,
StarRocks BE, Redis, object storage, Nova API, and the Nova MySQL proxy. Ranger
Admin is bound to loopback on port 6080. Raw StarRocks SQL port 9030 is not
published.

For host-side application development, leave the `nova-backend` profile off and
use the development override before running `./dev.sh`:

```bash
docker compose --env-file docker/.env \
  -f docker/docker-compose-engine.yml \
  -f docker/docker-compose.dev.yml up -d
./dev.sh
```

The override binds the native SQL endpoint to `127.0.0.1:29030`; it never binds
to a LAN interface. `dev.sh` supplies that endpoint and the running Ranger
container's development configuration to the local backend, scheduler, and
worker. The standard Compose stack continues to keep native SQL internal.

The stock StarRocks Ranger client does not send Basic authentication to the
plugin download endpoints. Nova therefore uses an internal-only, read-only
policy bridge. It allowlists only policy, role, tag, and user-store downloads,
maps them to Ranger's authenticated `secure` endpoints, caps responses, emits
no credential-bearing logs, and has no host port. Only Ranger Admin, the bridge,
and FE join the internal `ranger-policy` network.

Use `http://localhost:6080` for emergency operator access. Local development
credentials are in `docker/.env`; production must inject secrets and TLS.

The public SQL endpoint is the Nova proxy:

```bash
mysql -h 127.0.0.1 -P 4406 -u alice -p analytics
```

An explicit default role is required when the client does not request one.
Drivers that support custom MySQL connection attributes can select another
assigned role during the handshake:

```text
nova_role=finance
```

For MySQL Connector/J, add a driver property named `connectionAttributes` with
the value `nova_role:finance`. In DBeaver, add the same property under Driver
Properties. An invalid or unassigned role rejects the connection before login
completes; Nova does not fall back to the default.

Role changes after login use one activation path:

```sql
SET ROLE marketing;
USE ROLE finance;
SET ROLE DEFAULT;
```

`SET ROLE ALL`, `SET ROLE NONE`, and comma-separated roles are rejected.

Security management from any Nova SQL surface uses the same services as the UI:

```sql
CREATE ROLE marketing;
GRANT marketing TO USER alice;
GRANT SELECT ON TABLE analytics.sales TO ROLE marketing;
REVOKE SELECT ON TABLE analytics.sales FROM ROLE marketing;
SHOW GRANTS FOR ROLE marketing;
SHOW AVAILABLE ROLES;
SHOW CURRENT ACCESS;
```

The object `GRANT` and `REVOKE` forms mutate Ranger policies, not native
StarRocks object privileges. Marker membership is synchronized to both systems.

Data scopes are role-specific user attributes. Values in one dimension compose
with OR through `FIND_IN_SET`; different dimensions compose with AND:

```text
nova_scope.marketing.city = Jakarta,Bandung
nova_scope.marketing.business_unit = Consumer

FIND_IN_SET(city, 'Jakarta,Bandung') > 0
AND FIND_IN_SET(business_unit, 'Consumer') > 0
```

Missing attributes use a non-matching sentinel, so a scope-governed resource
returns no rows. A scope policy never grants `SELECT`; an access policy is still
required. Nova persists these attributes through Ranger's supported usersync
endpoint and uses `GET_USER_ATTR_Q` in the row-filter expression.

## Nova UI

The sidebar groups Users, Roles, Data Access, and Audit under Access Control.
The Access Control page can provision synchronized roles, object permissions,
role-scoped data assignments, and masks. It also shows provider health,
pending propagation, drift, Nova-managed policies, and an effective-access
explorer. The UI displays "Managed by Nova. Enforced by Ranger." and does not
expose credentials or raw provider payloads as editable state.

## Implementation Notes

Pinned components:

| Component | Pin |
|---|---|
| StarRocks | 4.1.4, commit `4a9848edf03f5c936dac664b2d52527f48e72eb0` |
| Ranger Admin, DB, Solr | 2.9.0 |
| StarRocks Ranger service definition | SHA-256 `0266f53186c2353d4a93eec0da9ca59e42a08f2d85885702697b693d85fbb097` |
| Nashorn expression engine | 15.4, SHA-256 `6f816e84dfd63a81d4eaa7829c08337bbaff3ec683ff3bf6bbd90d017a00dc6f` |
| ASM Commons | 9.4, SHA-256 `0c128a9ec3f33c98959272f6d16cf14247b508f58951574bcdbd2b56d6326364` |

The repository patch is `patches/starrocks/4.1.4-ranger-active-role.patch`.
Verify it against a clean upstream checkout:

```bash
./patches/starrocks/verify.sh
```

Policy writes are recorded as `PROPAGATING` because Ranger acceptance does not
prove that every FE has refreshed. Health reports pending rows and detected
role drift. Reconciliation must move a policy to `ACTIVE` only after a plugin
refresh is observed.

Agent, semantic, ML, artifacts, and scheduled tasks preserve the authenticated
principal and active role. Role switches increment `security_context_version`,
which partitions data-derived caches and prevents old-role observations from
being reused. Scheduled work stores its owner role and revalidates assignment
before execution.

Prepared statements remain blocked at the proxy, including binary protocol
commands and textual `PREPARE`/`EXECUTE`, until the pinned FE path is proven not
to bypass row filtering or masking.

StarRocks 4.1.4 can activate a configured default role while reporting every
`applicable_roles.IS_DEFAULT` value as `NO` in full Ranger mode. Nova never
chooses the first role. It reads marker assignments from `SHOW GRANTS` and, on
a fresh authenticated connection, treats the single role already active in
`CURRENT_ROLE()` as StarRocks' explicit default marker. Subsequent role switches
retain that captured default for `SET ROLE DEFAULT`.

### Migration

Back up StarRocks and Ranger before cutover. Then:

```bash
cd backend
uv run python scripts/nova_security.py migrate-to-ranger --dry-run
uv run python scripts/nova_security.py migrate-to-ranger --apply
uv run python scripts/nova_security.py verify-ranger
```

Review untranslatable hierarchy, system, function, and newer object grants in
the report. Validate expected permissions before enabling `access_control =
ranger`. After cutover, stop native object-grant management and restrict port
9030 to the internal network. The migration command does not delete native
grants automatically, which keeps rollback possible. Rollback restores the FE
configuration and the pre-cutover StarRocks backup; do not attempt a hybrid
authorization period.

### Troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| role not assigned | Requested marker role is absent | Assign it, then reconnect |
| no default role | Login omitted `nova_role` and no explicit default exists | Set one explicit default |
| projection drift | Ranger and marker role disagree | Run `verify-ranger`, reconcile before activation |
| Ranger unavailable | Control-plane calls exceeded bounded retry budget | Restore Ranger; writes fail closed |
| policy propagating | Ranger accepted the write but FE refresh is pending | Wait for the configured poll interval and verify |
| unsupported resource | Pinned service definition has no matching capability | Extend and test the service definition; never fall back |

## Limitations

Ranger policy distribution is eventually consistent. Nova exposes this state
instead of claiming immediate activation. The local stack uses HTTP and known
development credentials; production requires TLS, secret management, backups,
and a separately protected Ranger operator plane.

The official StarRocks 4.1.4 FE image contains Ranger audit core but not the
Solr or Log4j destination providers. Enabling either destination makes FE fail
at startup, so the local image keeps the embedded Ranger audit dispatcher
disabled. Nova's own query, role-switch, and security-administration audit rows
remain enabled in `NOVA_SYSTEM.AUDIT.LOG`; the Solr container is reproducible
and health checked, but it does not receive FE authorization decisions until
StarRocks ships the matching audit destination modules (or Nova pins and
verifies that additional runtime set).
