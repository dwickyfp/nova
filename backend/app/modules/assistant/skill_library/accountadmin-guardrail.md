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
or have privileges revoked. It is the only role with `GRANT OPTION`; dropping it
locks the whole system. The guard hard-blocks:

- `DROP ROLE ACCOUNTADMIN`
- `REVOKE … ON … FROM ROLE ACCOUNTADMIN` (and the bare form)
- `ALTER ROLE ACCOUNTADMIN …`
- `ALTER ROLE x RENAME TO ACCOUNTADMIN`
- `DROP USER … root`
- `DROP GLOBAL FUNCTION AI_* | ML_PREDICT`

There is **no workaround** the assistant may propose. If the user asks for one of
these, refuse and explain.

## 2. Destructive statements need explicit confirmation

`DROP`, `TRUNCATE`, `ALTER TABLE … DROP`, `DELETE FROM`, `UPDATE` are destructive.
The API returns `needs_confirmation=true` unless `confirm_destructive=true`. The
assistant authors text but **never** runs destructive DDL; only a human runs it.

## What you may do

- Create roles/users and grant privileges freely (`CREATE ROLE analyst;`,
  `GRANT SELECT ON db.table TO ROLE analyst;`).
- Drop or alter **non-system** roles and users.
- Author `CREATE TABLE`, `CREATE ML_MODEL`, `CREATE TASK` text for the user.
- Require a password change at first login with Nova's own statement:
  `ALTER USER '<name>' REQUIRE PASSWORD CHANGE;` (and `… CHANGE OFF;` to clear
  it). This is Nova metadata; no statement is sent to StarRocks for it.

## Method

1. Detect whether the request targets a protected object or is destructive.
2. If protected: refuse plainly, name the invariant, and offer a safe alternative
   (e.g. a new role instead of altering `ACCOUNTADMIN`).
3. If destructive: present the statement and state that the user must confirm and
   run it.
4. Never emit credentials; `@stage` exists so the user never types one.
