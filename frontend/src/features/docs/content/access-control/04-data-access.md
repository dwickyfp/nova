---
title: Data access
description: Let a role use a table, show each person only their rows, hide sensitive columns, and check what someone really gets.
---

## What it is

The **Data Access** page decides who can see which data. You work in up to
three steps:

1. **Let a role use a table.** Without this, the role cannot see the table at
   all.
2. **Show a person only some rows**, such as only the cities they look after.
3. **Hide a sensitive column**, such as email addresses.

Nova turns each step into a rule (a *policy*) that Apache Ranger enforces on
every query.

## Before you start

- Your active role must be able to manage security, such as `ACCOUNTADMIN`.
- The role, the users, and the table must already exist. See
  [Roles](/docs/access-control/roles) and [Users](/docs/access-control/users).

## The page at a glance

In the left menu, under **Access Control**, click **Data Access**. The page is
titled **Access Control** and has four tabs:

| Tab | What it is for |
| --- | --- |
| **Roles** | Every role with its description and number of **People**, plus **Create a role**. |
| **Data access** | The three steps above. |
| **Policies** | Every rule Nova has written, for review. |
| **Check access** | What one person really gets from one table under one role. |

Next to **Refresh** at the top, a status shows **Ranger healthy** (or the
problem), how many changes are still **syncing** to the query engine, and how
many are **out of sync**.

## Step 1: Let a role use a table

1. Open the **Data access** tab.
2. Under **Let a role use a table**, choose the **Role**, the **Database**, and
   the **Table**.
3. In **Allowed to**, choose what the role may do: **Read**, **Add rows**,
   **Change rows**, **Delete rows**. **Read** is selected for you.
4. Click **Give access**. Nova shows *Permission sent to Ranger*.

Wait a few seconds, then people working under that role can query the table.

You can do the same in a worksheet:

```sql
GRANT SELECT ON TABLE DOCS_QA_ACL.orders TO ROLE DOCS_QA_ACL_VIEWER;
```

```text
Action  Role                Authorization
GRANT   DOCS_QA_ACL_VIEWER  Ranger
```

Before the grant, a person working under the role gets an error such as:

```text
Access denied; you need (at least one of) the SELECT privilege(s) on COLUMN amount for this operation.
```

## Step 2: Show a person only some rows

A row limit shows one person, under one role, only the rows whose column value
is in their list.

1. Under **Show a person only some rows**, choose the **Person** and the
   **Role** they work under.
2. Choose the **Database** and **Table**.
3. In **Limit by**, choose what to limit by, for example `city`. **Matching
   column** fills in with the same column; change it if the table names it
   differently.
4. In **Allowed values**, type the values, separated by commas, for example
   `Jakarta, Bandung`. A row matching any of them is shown.
5. Click **Limit rows**. Nova shows *Data scope sent to Ranger*.

Saving again for the same person, role, and table replaces their values.

**Example.** The table `DOCS_QA_ACL.customers` has three customers in Jakarta,
Bandung, and Surabaya. After a row limit of `Jakarta, Bandung` for
`docs_qa_acl_user` under `DOCS_QA_ACL_ANALYST`, that person runs:

```sql
SELECT * FROM DOCS_QA_ACL.customers ORDER BY id;
```

```text
id  name  city     email
1   Ana   Jakarta  xxx@xxxxxxx.xxx
2   Budi  Bandung  xxxx@xxxxxxx.xxx
```

The Surabaya row is not shown. (The email column is hidden by step 3.)

> **Warning:** Once a table has a row limit for a role, **everyone** working
> under that role sees only their own allowed rows. A member of the role who has
> no values set sees **no rows at all**. Set values for every person in the
> role.

## Step 3: Hide a sensitive column

1. Under **Hide a sensitive column**, choose the **Role**, **Database**,
   **Table**, and **Column**.
2. In **Show values as**, choose how the column appears:

   | Choice | What people see |
   | --- | --- |
   | **Show as xxxx** | Letters become `x` or `X` and digits become `0`, so the shape stays: `ana@example.com` becomes `xxx@xxxxxxx.xxx`. |
   | **Replace with a hash** | A long code of letters and digits. The same value always gives the same code, so people can still count or match values without seeing them. |
   | **Show as empty** | An empty value (`NULL`). |

3. Click **Hide column**. Nova shows *Column mask sent to Ranger*.

People under the role still see the column, but not its real values.
Administrators and other roles are not affected.

## Review the rules

Open the **Policies** tab. Each rule shows its **Policy** name, **Kind**
(**Table access**, **Row limit**, or **Hidden column**), what it **Applies to**,
and its **Roles**. Search with **Search policies, roles or tables**.

## Check what a person really gets

1. Open the **Check access** tab.
2. Choose the **Person**, the role they are **Using**, the **Database**, and
   the **Table**.
3. Click **Check access**.

**What they get** shows:

| Rule | Example result |
| --- | --- |
| **Allowed to** | Read |
| **Rows shown** | `city IN ('Bandung','Jakarta')` |
| **Hidden columns** | `email (show as xxxx)` |

**Policies behind this result** lists the rules that produced it.

## Take access away

In a worksheet:

```sql
REVOKE SELECT ON TABLE DOCS_QA_ACL.orders FROM ROLE DOCS_QA_ACL_VIEWER;
```

After a few seconds, people under the role get *Access denied* again. The rule
disappears from `SHOW GRANTS FOR ROLE DOCS_QA_ACL_VIEWER;`.

## Limits and errors

- **One role per table, for now.** Giving a second role access to a table that
  another role already has fails (the console shows *Internal server error*;
  SQL shows *Ranger rejected ...*). Until this is fixed, put the people who
  need the same table under one role.
- **No remove button for row limits and hidden columns.** The console has no
  way to delete these two kinds of rule yet. To change a row limit, save new
  values; to change how a column is hidden, save the column again with another
  **Show values as** choice.
- **Changes take a few seconds.** Watch the **syncing** count at the top of the
  page.
- Values in **Allowed values** cannot contain commas, because the comma
  separates values.
- Roles that cannot manage security get *The active role is not authorized to
  administer access control*.
