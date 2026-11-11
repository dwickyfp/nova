---
title: Roles
description: Create, review, and delete roles, and see which people and permissions each role has.
---

## What a role is

A **role** is a named group of people who need the same data, such as
`SALES_JAKARTA` or `FINANCE`. You give data to the role once, then give the role
to each person who needs it. People work under one role at a time. See
[Access control concepts](/docs/access-control/concepts).

## Before you start

Your active role must be able to manage security, such as `ACCOUNTADMIN`.

## The Roles page

In the left menu, under **Access Control**, click **Roles**. The table lists
every role with:

| Column | Meaning |
| --- | --- |
| **Role** | The role name. **Protected** marks `ACCOUNTADMIN`. |
| **Type** | **Built-in** roles come with the engine. **Custom** roles were created by your team. |
| **Members** | How many users and roles belong to it. |
| **Privileges** | How many permissions it has. |
| **Actions** | **View Detail** and **Delete**. |

Filter with **Search roles...** or the **Type** filter (**Built-in** or
**Custom**). **Ask Neva** opens the assistant with a question about roles.

## Create a role

1. Click **Create Role**.
2. Type the **Role name**, for example `analyst_readonly`.
3. Click **Create**.

You can also create a role with a description on the **Data Access** page
(**Roles** tab, **Create a role**), or in SQL:

```sql
CREATE ROLE DOCS_QA_ACL_VIEWER;
```

A new role has no data. Give it data next: see
[Data access](/docs/access-control/data-access).

## See what a role has

Click a role's row, or **View Detail** in its **...** menu. The detail page
shows:

- **About**: **Role Name**, **Type**, **Status** (**Protected** or **Not
  Protected**), **Members**, and **Privileges**.
- **Privileges**: every permission, with **Privilege Type**, **Scope (Object
  Type)**, **Catalog**, **Database**, **Object Name**, and **Grantable**. Filter
  by **Scope** or **Grantability**.
- **Members**: the users who have the role.

In SQL, list a role's permissions with:

```sql
SHOW GRANTS FOR ROLE DOCS_QA_ACL_VIEWER;
```

```text
Role                Policy                                                                   Authorization  PolicyHealth
DOCS_QA_ACL_VIEWER  nova-managed/access/DOCS_QA_ACL_VIEWER/default_catalog/DOCS_QA_ACL/orders  Ranger         ACTIVE
```

List every role with `SHOW ROLES;`.

## Give a role to people

Give roles from the **Users** page (**Grant role**), or in SQL with
`GRANT <role> TO USER <user>;`. See [Users](/docs/access-control/users).

## Delete a role

1. In the role's **...** menu, click **Delete**.
2. Confirm with **Delete Role**.

Deleting a role removes it from everyone who had it. This cannot be undone.
**Delete** is turned off for built-in and protected roles.

In SQL, the worksheet asks you to confirm before it runs:

```sql
DROP ROLE DOCS_QA_ACL_VIEWER;
```

## The protected ACCOUNTADMIN role

Nova refuses every attempt to change `ACCOUNTADMIN`, whether from the console
or from SQL. These were run in a worksheet as an administrator:

```sql
DROP ROLE ACCOUNTADMIN;
-- ACCOUNTADMIN role cannot be dropped
```

```sql
ALTER ROLE ACCOUNTADMIN SET COMMENT = 'x';
-- ACCOUNTADMIN role cannot be altered
```

```sql
REVOKE ACCOUNTADMIN FROM USER docs_qa_acl_user;
-- ACCOUNTADMIN membership cannot be revoked here
```

Each refusal is recorded in the [audit trail](/docs/access-control/audit).

## Limits

- **Renaming a role is not supported.** `ALTER ROLE ... RENAME TO ...` is a
  syntax error. Create a role with the new name, give it the same data and
  people, then delete the old one.
- **Role inside a role does not pass on data access.** `GRANT role_a TO ROLE
  role_b` is accepted and shown on the role detail page, but people working
  under `role_b` do not get `role_a`'s tables. Give each role its own access.
- A role you create is ready to use at once, but data you give it takes a few
  seconds to apply.
