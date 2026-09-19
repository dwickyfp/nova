---
name: create-user
title: Create a user (ready to run)
summary: Produce a ready-to-run CREATE USER with a generated password and force a password change at first login.
triggers: create user, new user, buat user, tambah user, add user, create role, grant user, password user, akun
source: docs/sql_docs/11-query-catalog.md, docs/18-authentication.md, AGENTS.md rule #5
---

# Skill: create-user

Nova has **no user table**: every login user *is* a StarRocks user
(`AGENTS.md` rule #5). Creating a user is therefore a StarRocks
`CREATE USER` you author for the user to run — it is not something the read-only
`query_execute` tool can run.

## Goal: a block that is ready to execute

Do not hand back a placeholder password that forces the user to edit the SQL
before they can run the whole block. Instead:

1. **Generate a strong random password** (16+ characters, mixed case, digits,
   symbols; avoid quotes so it embeds cleanly). State that it is generated.
2. Write the `CREATE USER` with that password, then grant what was asked.
3. Add the Nova statement that requires a password change at first login, so the
   generated password is temporary:

   ```sql
   ALTER USER '<name>' REQUIRE PASSWORD CHANGE;
   ```

   This is a **Nova** statement (StarRocks has no such attribute). Nova records
   the flag and asks the user to set a new password the first time they sign in.
   No statement is sent to StarRocks for it.

## Template

```sql
-- 1. Create the user with a generated temporary password
CREATE USER 'dwicky.f.putra' IDENTIFIED BY 'Xy7#kQ2pLm9Rt4Vz';

-- 2. Require a password change at first login (Nova metadata)
ALTER USER 'dwicky.f.putra' REQUIRE PASSWORD CHANGE;

-- 3. Grant the access the user needs (adjust to the request)
GRANT SELECT ON DATABASE NOVA_DEMO TO USER 'dwicky.f.putra';
```

## Rules that still apply

- The name may contain dots (`dwicky.f.putra`); quote it with single quotes so
  StarRocks reads it as one identity.
- Never use the protected-object operations: `DROP ROLE ACCOUNTADMIN`,
  revoke/alter on `ACCOUNTADMIN`, `DROP USER root`, `DROP GLOBAL FUNCTION` of a
  built-in UDF. Creating a new user or role is fine.
- Never invent a real credential beyond the generated temporary one; the user
  replaces it on first login. Do not ask the user for a password.
- To clear the requirement later: `ALTER USER '<name>' REQUIRE PASSWORD CHANGE OFF;`
  To change a password directly: `SET PASSWORD FOR '<name>' = PASSWORD('<new>');`
- The user runs these statements in a worksheet or the Users page. You author
  them; you never execute account DDL.

## Caveats

- The generated password is shown in the SQL, which is fine because it is
  temporary and single-use (replaced at first login). Prefer regenerating a new
  one rather than reusing one across users.
