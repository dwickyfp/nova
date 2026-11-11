---
title: Access control concepts
description: Understand users, roles, the active role, and how Nova decides what data a person can see.
---

## The idea in one paragraph

People sign in as **users**. Users do not get data directly: you give data to a
**role**, and you give roles to users. A person always works under exactly
**one** of their roles at a time, called the **active role**, and sees exactly
what that role is allowed, no more.

## Terms

| Term | Plain meaning | Example |
| --- | --- | --- |
| User | One person's sign-in. | `budi` |
| Role | A named group of people who need the same data. | `SALES_JAKARTA` |
| Default role | The role a user starts with when they sign in. Every user has one. | `SALES_JAKARTA` |
| Active role | The role a user is working under right now. | Switch it from the account menu or with `USE ROLE`. |
| Permission | What a role may do with a table: **Read**, **Add rows**, **Change rows**, **Delete rows**. | `SALES_JAKARTA` may read `orders`. |
| Row limit | A rule that shows a person only some rows of a table, such as only their own cities. | Budi sees only Jakarta rows. |
| Hidden column | A rule that replaces a column's real values with masked ones for a role. | Emails appear as `xxx@xxxxxxx.xxx`. |
| Policy | One stored rule. Nova writes policies for you when you give access. | `Table access` on `orders` for `SALES_JAKARTA`. |

## How Nova decides what you see

For every query, Nova checks three things:

1. **Who you are** (your user).
2. **Your active role**, only that one.
3. **The policies for that role**: which tables it may use, which rows are
   shown, and which columns are hidden.

Nova uses Apache Ranger, an authorization service, to store and enforce these
policies. Nova passes your user and active role along with every query it runs
for you, so features built on top of SQL, such as AI agents, work within your
access rather than around it.

> **Note:** Because only the active role counts, a person who has both
> `SALES_JAKARTA` and `FINANCE` cannot combine them in one query. They switch
> roles to see the other data.

## Changes take a few seconds

When you give or remove access, the change is sent to Ranger at once, then
reaches the query engine within a few seconds. The **Data Access** page shows
how many changes are still syncing. Test a new rule after a short wait.

## The ACCOUNTADMIN role

`ACCOUNTADMIN` is Nova's built-in administrator role. Nova protects it: it
cannot be dropped, altered, renamed to, or taken away from its holders, from
the console or from SQL. The Users and Roles pages mark protected items with a
**Protected** badge and turn off their actions. See
[Roles](/docs/access-control/roles#the-protected-accountadmin-role).

## Who can manage access

Managing users, roles, and data access needs an active role that administers
security, such as `ACCOUNTADMIN`. Other roles are refused with
*The active role is not authorized to administer access control* or
*Requires active role to be one of ...*.

## Next steps

- [Users](/docs/access-control/users)
- [Roles](/docs/access-control/roles)
- [Data access](/docs/access-control/data-access)
- [Audit](/docs/access-control/audit)
