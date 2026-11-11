---
title: Users
description: Create Nova users, give them roles, reset their passwords, and remove them.
---

## What a user is

A **user** is one person's sign-in: a username and a password. A user gets data
only through the roles you give them. Every user needs a **default role**, the
role they start with when they sign in.

## Before you start

- Your active role must be able to manage security, such as `ACCOUNTADMIN`.
- Create the role you want to give the user first. See
  [Roles](/docs/access-control/roles).

## Open the Users page

In the left menu, under **Access Control**, click **Users**. The page is titled
**Users & Roles** and lists every user with:

| Column | Meaning |
| --- | --- |
| **Name** | The username. A **Protected** badge marks accounts Nova will not let you change, such as the built-in administrator. |
| **Last Login** | When the user last signed in. |
| **Default Role** | See the note under *Limits*. |
| **Grant Roles** | How many roles the user has. Click the button to list them. |
| **Actions** | The **...** menu for this user. |

Use **Search user, auth, role...** to filter the list, and the **Access** filter
to show only **Protected** or **Standard** users. Click a row to open the user's
detail page.

## Create a user

1. Click **Create User**. The **New user** window opens.
2. Type the **User name**.
3. Choose the **Default role**.
4. Type the **Password** and type it again in **Confirm password**.
5. Click **Create User**.

Nova creates the account, gives it the default role, and makes that role the
one the user starts with. Give the user their password through a safe channel.

> **Tip:** To make the person choose their own password at first sign-in, run
> this in a worksheet after you create the user:
>
> ```sql
> ALTER USER docs_qa_acl_user2 REQUIRE PASSWORD CHANGE;
> ```
>
> Nova answers with `must_change_password` = `true`. At the next sign-in the
> user is asked for a new password before they can continue. To cancel the
> requirement, run `ALTER USER docs_qa_acl_user2 REQUIRE PASSWORD CHANGE OFF;`.

### Create a user with SQL

You can do the same in a worksheet. Create the account, give it a role, then
make that role the default:

```sql
CREATE USER docs_qa_acl_user4 IDENTIFIED BY '<a strong password>';
GRANT DOCS_QA_ACL_ANALYST TO USER docs_qa_acl_user4;
SET DEFAULT ROLE DOCS_QA_ACL_ANALYST TO docs_qa_acl_user4;
```

> **Note:** `CREATE USER ... DEFAULT ROLE ...` in one statement is refused with
> *Assign roles through Ranger after creating the account*. Use the three
> statements above.

## Give a user another role

1. In the user's row, open the **...** menu and click **Grant role**.
2. In **Role to grant**, choose the role.
3. Click **Grant**.

Or in SQL:

```sql
GRANT DOCS_QA_ACL_VIEWER TO USER docs_qa_acl_user;
```

The person sees the new role after they sign out and sign in again.

## Take a role away

1. Open the **...** menu and click **Revoke role**.
2. In **Role to revoke**, choose one of the user's roles.
3. Click **Revoke**.

Or in SQL:

```sql
REVOKE DOCS_QA_ACL_VIEWER FROM USER docs_qa_acl_user;
```

## Reset a password

1. Open the **...** menu and click **Reset password**.
2. Click **Reset Password**.
3. Copy the **Generated password** that appears. It is shown only once.
4. Click **Close**.

The old password stops working at once. The generated password does not force
a change by itself; run the `REQUIRE PASSWORD CHANGE` statement above if you
want the person to choose their own.

## See one user's details

Click a user's row. The detail page shows:

- **About**: **Login name**, **Studio News** (a switch that shows or hides the
  News page in Nova Studio for this user), **Default role**, **Last login**,
  **Status**, and **Roles**.
- **Roles**: a table of every role the user has, with **Assignment**,
  **Default Role**, and **Status**. Filter it with **Search roles...** or the
  **Assignment** filter (**Default role** or **Additional role**).

Use the **...** menu at the top right to **Refresh** or go **Back to users**.

## Delete a user

1. Open the **...** menu and click **Delete user**.
2. Read the confirmation and click **Delete**.

Nova takes away the user's roles, then removes the account. This cannot be
undone.

In SQL, `DROP USER docs_qa_acl_user4;` asks you to confirm before it runs,
because it cannot be undone.

## Limits and errors

- **The Default Role column and the Default role field can show "None"** even
  when the user has a default role. The user still signs in under the default
  role you chose. To check it, ask the person to look at the role under their
  name in the account menu right after signing in.
- **There is no disable or lock switch.** **Status** always shows **Enabled**.
  To stop someone signing in, delete the user. To stop them seeing data,
  revoke their roles.
- **Protected users** (such as the built-in administrator) cannot be reset,
  changed, or deleted from this page. Their actions are turned off.
- **Create User** without a **Default role** is refused with a message that
  exactly one explicit default role is required.
- A role you grant to someone who is already signed in reaches them after
  they sign in again.
