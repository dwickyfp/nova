---
name: create-user
title: Create users and assign roles
summary: Draft complete account SQL with a protected temporary-password placeholder and mandatory first-login change; execute through provision_user.
triggers: create user, new user, buat user, buatkan query, tambah user, add user, create role, grant user, password user, akun, pengguna, accountadmin
source: app/modules/users/service.py, app/modules/query/dialect/force_password_change.py, docs.starrocks.io CREATE_USER and GRANT
---

# Create users and assign roles

For "buatkan query" or "write SQL", provide full SQL without executing it.
Do not refuse account SQL or redirect to Users just because a password is needed.
Use exactly `'<temporary_password>'` as a placeholder. Never invent a password
or ask for one in chat. Run collects it in protected input.

```sql
CREATE USER 'dwicky.f.putra' IDENTIFIED BY '<temporary_password>';
ALTER USER 'dwicky.f.putra' REQUIRE PASSWORD CHANGE;
GRANT ACCOUNTADMIN TO USER 'dwicky.f.putra';
SET DEFAULT ROLE ACCOUNTADMIN TO 'dwicky.f.putra';
```

Substitute the requested username and role. Quote usernames containing dots as
string literals; dots inside a quoted username are not database qualifiers.
Host defaults to `%`. Roles are identifiers and must already exist.
Membership syntax is `GRANT <role> TO USER <identity>`; adding ROLE after GRANT
is invalid. `SET DEFAULT ROLE <role> TO <identity>` activates a granted role on
future logins; it does not switch the administrator's current role.

`ALTER USER ... REQUIRE PASSWORD CHANGE` is a Nova extension handled by the
query service. Include it by default for temporary-password accounts. Do not
make it optional or invent MUST_CHANGE_PASSWORD or PASSWORD EXPIRE clauses.
The flag is username-wide across hosts and enforced on Nova login, not direct
engine connections bypassing Nova.

For "buatkan akun" or "create the user", use provision_user with username and
one existing default role. Its approval form collects the temporary password
outside the model. It checks the active administrative role, creates the
account, requires a password change, assigns the role through the configured
authorization service and sets the default role. Confirm success only from
its result. Never put passwords in tool arguments.

In Ranger mode, prefer provision_user for execution. Native CREATE USER with
DEFAULT ROLE does not provision Ranger membership. For SQL drafting use the
separate GRANT above so Nova routes it to Ranger. In native mode, CREATE USER
with DEFAULT ROLE can combine creation and grant; password change is still
a separate Nova statement.

Granting ACCOUNTADMIN to the requested user is allowed for an authorized
administrator. Mention its full administrative scope briefly; do not replace
it with another role. Never drop, rename, alter or revoke ACCOUNTADMIN or modify
root. Existing-account grants need no password. Verify with SHOW GRANTS FOR
'dwicky.f.putra'; a draft is not proof of creation.

In the same conversation, reuse the named user and role unless corrected.
A new conversation does not inherit another conversation's target or approval.
