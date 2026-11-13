---
title: Sign in and choose your role
description: Sign in to Nova, set a new password when asked, and switch the role you work under.
---

## What happens when you sign in

Nova checks your username and password, then starts a session under your
**default role**: the role your administrator chose for you when they created
your account. Everything you see and every query you run uses that role until
you switch to another one.

## Before you start

- You need a username and password from your Nova administrator.
- Your account needs a default role. Nova requires one for every user it
  creates.

## Sign in

1. Open Nova in your browser. The **Sign in to Nova** page appears.
2. Type your **Username** and **Password**.
3. Click **Sign in**.

After a successful sign-in, a full-screen Nova phoenix briefly flaps its wings
before your workspace opens. If you opened a link to a specific Nova page,
you continue to that page. The transition also appears after a required
password change. With reduced motion enabled on your device, the phoenix
stays still and the transition is shorter.

If the details are wrong, Nova shows **Invalid username or password.**

## Set a new password when Nova asks

Nova asks for a new password in two cases:

- **Your administrator requires it.** After you sign in, the form says *Your
  administrator requires a new password before first use.* Type the new
  password in both fields and click **Change Password & Continue**. Nova signs
  you in with the new password.
- **The very first sign-in of a new Nova installation.** The built-in
  administrator sees *First login detected. Please set a new admin password.*
  Fill in **New Password** and **Confirm Password** and click
  **Set Password & Continue**. After this, the factory password no longer works.

New passwords must be at least 8 characters, and both fields must match.

## Switch your role

If you have more than one role, you can change the one you work under at any
time.

1. Click your name at the bottom of the left menu.
2. Under **Switch Role**, point at the current role name. A list of your roles
   opens.
3. Type in **Search roles...** to filter a long list, then click a role.

Nova reloads the page under the new role. The role name under your username
shows which role is active.

### Switch your role from SQL

In a worksheet, run one of these statements on its own:

```sql
USE ROLE DOCS_QA_ACL_VIEWER;
```

```sql
SET ROLE DEFAULT;
```

Nova answers with the role that is now active:

```text
CURRENT_ROLE()       SECURITY_CONTEXT_VERSION
DOCS_QA_ACL_VIEWER   2
```

`SET ROLE DEFAULT` goes back to your default role. The change applies to your
whole session, including later worksheets.

> **Note:** Run `USE ROLE` or `SET ROLE` by itself. If you put it in a script
> with other statements, Nova refuses the whole run with *Run USE ROLE or SET
> ROLE on its own, then run the other statements.*

## Limits and errors

| Message | What it means |
| --- | --- |
| *Role is not granted to this user* | You do not have that role, or it was given to you after you signed in. Sign out and sign in again to pick up new roles. |
| *Exactly one named role must be active* | `SET ROLE ALL` and `SET ROLE NONE` are not allowed. Nova always works under exactly one role. |
| *Invalid username or password* | The username or password is wrong. Ask your administrator to reset your password. |

## Sign out

1. Click your name at the bottom of the left menu.
2. Click **Sign out**, then confirm with **Sign out**.
