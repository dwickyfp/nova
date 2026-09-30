# Access Control Agent Guide

Inherit the [root](../../../../AGENTS.md) and
[backend](../../../AGENTS.md) contracts. Read
[Ranger architecture](../../../../docs/arch-08-ranger-authorization.md) and
[access-control operations](../../../../docs/29-ranger-access-control.md).
Paths below are repository-relative; local Python commands run from `backend/`.

## Security ownership

- StarRocks authenticates principals and owns role-marker assignments/defaults.
  Ranger-enabled user-data authorization is principal + exactly one active role
  + Ranger policy, including row filters and masks in the patched FE.
- `SecurityContext` owns validated caller state. Role activation validates
  assignment and Ranger projection, sets the requested role, and verifies it
  before updating session state. Preserve assigned, default, and active roles as
  distinct values; never choose a role by list order or activate all roles.
- Propagate principal, active role, session identity, and context version through
  SQL, Semantic Views, ML, Studio, delegation, and caches. Revalidate delegated
  state at established checkpoints. Cache data with the existing security
  partition and any policy scope required by its owner.
- `SHOW GRANTS` is valid for marker assignment and existing native-RBAC
  compatibility. It is not a Ranger policy engine. Preserve the explicitly
  configured Ranger-disabled path without turning failures in governed mode
  into silent native fallback.
- Never substitute root/system, agent-owner, or more privileged service
  credentials for the caller. Existing scheduled service work must use its
  explicitly authorized principal/role and revalidation contract. Control-plane,
  bootstrap, and health privileges do not authorize user-data reads.
- UI and SQL governance converge on the existing access-control service and
  Ranger integration. Policy/filter/mask logic belongs there; do not create
  a parallel policy engine. Unsupported managed governance forms fail closed.
- Preserve `ACCOUNTADMIN` guards in both generic SQL and managed operations.
  Audit role switches, policy administration, and denied execution with redacted
  fields. Never expose control-plane credentials or raw provider secret payloads.
- Ranger acceptance and FE propagation are different states. A successful write
  does not prove every FE applies the policy; retain propagation/drift checks.

Read this guide for `backend/app/core/` role/auth gates,
`backend/app/modules/auth/`, `backend/app/integrations/ranger/`, stage access,
and user-data adapters even though they are outside this directory. Read the
[SQL guide](../../sql_frontend/AGENTS.md) for managed SQL and the
[patch guide](../../../../patches/starrocks/AGENTS.md) for FE changes.

## Validation

Run applicable backend CI gates and seeded integration. Focused security checks:

```bash
uv run pytest tests/unit/test_ranger_access_control.py tests/unit/test_query_active_role.py tests/unit/test_active_role_gate.py tests/unit/test_shared_agent_rbac.py tests/unit/test_ranger_policy_proxy.py
uv run pytest tests/eval/test_security_context_boundary.py
uv run python scripts/check_user_data_system_access.py
```

Exercise assigned-but-inactive roles, missing/multiple roles, revoked assignments,
session-version changes, unauthorized delegation, cache isolation, row filters,
masking, `ACCOUNTADMIN`, policy failures, and native compatibility as affected.

The ordinary `backend/docker-compose.test.yml` stack does not enable the patched
Ranger path. Use the governed stack and fixture setup in the access-control
runbook. Its local acceptance command runs inside the configured backend
container, from the repository root:

```bash
docker compose -f docker/docker-compose-engine.yml exec nova-backend python scripts/verify_ranger_e2e.py
```

This requires the running `app` profile and the documented Alice/Bob policy
fixtures; it is not a read-only production probe. Verify expected proxy, role,
row-filter, masking, root-guard, and agent-tool results. Do not claim Ranger
acceptance from unit mocks or an unpatched integration result. If required
infrastructure is missing, report the unmet gate before commit/push.
