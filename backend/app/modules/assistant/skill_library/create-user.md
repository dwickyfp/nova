---
name: create-user
title: Create a StarRocks user
summary: Explain user provisioning without generating, requesting, or displaying credentials.
triggers: create user, new user, buat user, tambah user, add user, create role, grant user, password user, akun
source: docs/18-authentication.md, AGENTS.md rule #5
---

# Create a user

Nova authenticates StarRocks users; it has no separate identity table.

Ask for the account name and required role or privileges when missing. Direct
the administrator to the Users page to enter credentials through the protected
provisioning flow. Never generate a password in chat, ask the user to paste one,
or include one in SQL, tool arguments, logs, or examples. Temporary credentials
are still secrets.

Explain the least-privilege grants and the first-login password-change option.
Do not execute account DDL through the read-only query tool. Do not drop, rename,
alter, or revoke the protected ACCOUNTADMIN role, and do not modify root.

If secure provisioning is unavailable, explain the limitation and ask the
administrator to complete credential setup outside the assistant. Do not work
around it by producing a ready-to-run statement containing a credential.
