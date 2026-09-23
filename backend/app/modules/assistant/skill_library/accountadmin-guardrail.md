---
name: accountadmin-guardrail
title: Account, role and guardrail rules
summary: The immutable-ACCOUNTADMIN rule and the protected-object guardrails, so requests that touch them are refused correctly.
triggers: accountadmin, role, grant, revoke, drop role, drop user, permission, rbac, privileged, root user, drop function
source: docs/sql_docs/09-guardrails-invariants.md, AGENTS.md rule #6
---

# Skill: accountadmin-guardrail

Two invariants shape every answer about accounts, roles, and privileges. Apply
them before proposing any statement.

## 1. ACCOUNTADMIN is immutable

`ACCOUNTADMIN` is Nova's SUPER USER role and must **never** be dropped, renamed,
altered, or have privileges revoked. It is the only role with `GRANT OPTION`;
dropping it locks the whole system. The guard hard-blocks:

- `DROP ROLE ACCOUNTADMIN`
- `REVOKE … ON … FROM ROLE ACCOUNTADMIN` (and the bare form)
- `ALTER ROLE ACCOUNTADMIN …`
- `ALTER ROLE x RENAME TO ACCOUNTADMIN`
- `DROP USER … root`
- `DROP GLOBAL FUNCTION AI_* | ML_PREDICT`

These are the protected operations. **Adding a Ranger access policy to ACCOUNTADMIN is
allowed.** Do not interpret "immutable" as a ban on new grants. In full Ranger
mode, Verify Access checks Ranger policies for the exact active role; native
StarRocks `ALL ON *.*` does not satisfy a missing Ranger policy. If Verify Access
reports a gap for ACCOUNTADMIN, keep ACCOUNTADMIN as the target. Use
`inspect_role_access` to check the effective policy, then `grant_role_access`
for missing permissions after approval. These are typed, in-process tools; do
not call or display internal HTTP endpoints. Do not create or suggest a
replacement role unless the user explicitly asks for one.

There is **no workaround** for the blocked operations above. If the user asks
for one of those, refuse and explain.

## 2. Destructive statements need explicit confirmation

`DROP`, `TRUNCATE`, `ALTER TABLE … DROP`, `DELETE FROM`, `UPDATE` are destructive.
The API returns `needs_confirmation=true` unless `confirm_destructive=true`. The
assistant authors text but **never** runs destructive DDL; only a human runs it.

## What you may do

- Create roles/users and grant privileges freely (`CREATE ROLE analyst;`,
  `GRANT SELECT ON db.table TO ROLE analyst;`). For Ranger-managed object
  permissions, use the typed Ranger access tools rather than native StarRocks grants.
- Drop or alter **non-system** roles and users.
- Author `CREATE TABLE`, `CREATE ML_MODEL`, `CREATE TASK` text for the user.
- Require a password change at first login with Nova's own statement:
  `ALTER USER '<name>' REQUIRE PASSWORD CHANGE;` (and `… CHANGE OFF;` to clear
  it). This is Nova metadata; no statement is sent to StarRocks for it.

## Method

1. Identify the requested operation and the exact target role. A grant to
   ACCOUNTADMIN is permitted; drop, revoke, and alter are not.
2. For a requested Ranger grant, inspect existing policy with
   `inspect_role_access`, prepare only missing accesses for that role, and use
   `grant_role_access` after approval. Recheck Verify Access after propagation.
3. If the operation is blocked, refuse plainly and name the invariant. Do not
   replace the user's requested role with another role.
4. If destructive, present the statement and state that the user must confirm
   and run it.
5. Never emit credentials; `@stage` exists so the user never types one.
