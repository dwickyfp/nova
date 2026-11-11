---
title: Audit trail
description: Find out who did what in Nova, including sign-ins, queries, refused statements, and access changes.
---

## What it is

The **audit trail** is Nova's record of what happened: every sign-in, every
SQL statement (including ones Nova refused), every change to users, roles, and
data access, and actions in other areas such as tasks and notebooks. Each entry
says who did it, when, and whether it succeeded.

Secrets such as passwords are never written to the audit trail.

## Before you start

Reading the audit trail needs an administrator role, such as `ACCOUNTADMIN`.

## Open the audit trail

In the left menu, click **Audit** under **Access Control**, or **Audit Trail**
under **Operations** > **Monitoring**. Both open the same **Audit Trail** page.

Each row shows:

| Column | Meaning |
| --- | --- |
| **Time** | When it happened. |
| **Event** | The kind of event, such as `query`, `login`, or `security_admin`. |
| **User** | Who did it. |
| **Action** | What they did, such as `execute`, `LOGIN`, `GRANT_ROLE`, or `PUT_MASK`. |
| **Status** | `SUCCESS`, or `ERROR` with the reason. |
| **Duration** and **Rows** | For queries: how long it took and how many rows it touched. |

Click a row to see the details: the **SQL Text**, the **Error Message**, and
the **Log ID**, **Session**, **IP Address**, **Database**, and **Schema**.

## Find an event

1. Use the **Event Type** filter: **query**, **workspace**, **login**, or
   **security_admin** (changes to users, roles, and data access).
2. Use **Status** to show only **SUCCESS** or **ERROR**.
3. Use **User** to show one person's events.
4. Type in **Search action, object, SQL...** to narrow the rows on the current
   page.

> **Note:** The search box filters only the page you are looking at. Use the
> filters first to narrow the whole history, then search.

## What access changes look like

Changes made on the Users, Roles, and Data Access pages, or with security SQL,
appear as `security_admin` events. For example, after the steps in
[Data access](/docs/access-control/data-access):

| Action | Object |
| --- | --- |
| `CREATE_ROLE` | `DOCS_QA_ACL_TEMP` |
| `GRANT_ROLE` | `DOCS_QA_ACL_VIEWER:docs_qa_acl_user` |
| `REVOKE_ROLE` | `DOCS_QA_ACL_VIEWER:docs_qa_acl_user` |
| `GRANT_ACCESS` | the table access rule for the role |
| `PUT_DATA_SCOPE` | the row limit rule |
| `PUT_MASK` | the hidden column rule |
| `DROP_ROLE` | `DOCS_QA_ACL_TEMP` |

## Refused statements are recorded too

A statement Nova refuses before it reaches the engine still leaves an `ERROR`
entry. For example, `DROP ROLE ACCOUNTADMIN` appears with the message
*ACCOUNTADMIN role cannot be dropped*. Queries refused for missing permissions
appear with the engine's *Access denied* message.
