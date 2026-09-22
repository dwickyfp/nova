# Architecture 8: Ranger Authorization

> One active StarRocks marker role becomes the sole Ranger role context for every user-data path.

---

## Architecture

```text
HTTP / MySQL / Studio / ML / task
              |
              v
 SecurityContext(principal, active_role, version)
              |
              v
 Governed execution -> SET ROLE -> CURRENT_ROLE verification
              |
              v
 patched StarRocks Ranger bridge
              |
 user + groups + userRoles={active_role}
              |
              v
 Ranger: permission + row filter + masking
```

Trust boundaries:

1. StarRocks authenticates users and owns marker assignment/default state.
2. Nova validates exactly one role and reasserts it on every delegated query.
3. Ranger owns policy decisions and role-scoped user attributes.
4. Root and system pools are limited to `NOVA_SYSTEM`, health, and bootstrap.
5. StarRocks port 9030 stays on the compose network; clients enter through 4406.
6. FE reaches Ranger downloads only through an authenticated, allowlisted
   bridge on an internal Docker network; the bridge has no public listener.

## Implementation

`SecurityContext` rejects missing principals, root users, empty roles, reserved
role tokens, and multi-role strings. `RoleActivationService` validates
assignment, Ranger projection, `SET ROLE`, and `CURRENT_ROLE()` before session
state changes. Redis and proxy sessions store assigned, default, and active role
separately and advance the context version after a switch.

`SecurityStatementRouter` intercepts security DDL/DCL before ordinary query
execution. UI and SQL calls converge on `AccessControlService`. Ranger writes
are bounded, authenticated, paginated, idempotent, and credential-safe.

The FE patch adds `RangerAccessRequest.setUserRoles()` for access, row-filter,
and mask requests. `active_role` mode rejects zero or multiple active role IDs.
The upstream-compatible default remains `assigned_roles`; Nova's XML opts in.

## Integration Points

- Auth and proxy: explicit default role and optional `nova_role` connection attribute.
- Query: role is reasserted for each delegated execution.
- Studio and Agent: active role comes only from the authenticated session.
- Semantic and ML: generated/source SQL keeps the caller context.
- Tasks: owner principal and execution role are persisted and revalidated.
- Caches: principal, role, and context version partition data-derived entries.
- Governance: row filters and masks compile to Ranger policies only.
- Audit: Nova records queries, role switches, and security administration in
  `NOVA_SYSTEM`; see the deployment limitation for FE Ranger audit destinations.

## Configuration

Set `RANGER_ENABLED=true`, the Ranger Admin URL and service name, control-plane
credentials, TLS verification, bounded timeouts, and the managed policy prefix.
StarRocks FE must set `access_control = ranger` and load
`conf/ranger-starrocks-security.xml` plus `conf/ranger-starrocks-audit.xml`.
See `29-ranger-access-control.md` for deployment and migration.
