---
title: Admin settings
description: Look up and change the query engine's variables, such as the query timeout, for the whole engine.
---

## What it is

The query engine has hundreds of **variables**: named settings such as
`query_timeout` (how many seconds a query may run before it is stopped). The
**Admin Settings** page lists them and lets an administrator change them.

There are two scopes:

| Scope | Meaning |
| --- | --- |
| **Session variables** | The values your queries start with. |
| **Global variables** | The engine-wide values. Changing one affects every user's new queries. |

## Before you start

- Reading the lists works for any signed-in user.
- Changing a global variable needs a role the engine allows to change global
  settings, such as `ACCOUNTADMIN`.

## Find a variable

1. In the left menu, under **Administrator**, click **Admin Settings**.
2. Choose the **Session variables** or **Global variables** tab.
3. Type part of the name in **Search variables**, for example `timeout`.

Each row shows the **Name**, the current **Value**, and the engine's
**Default** when the engine reports one. When the engine reports a default and
the value differs from it, the value is highlighted, a **changed** count
appears above the table, and the row gets a **Reset** button.

## Change a global variable

1. Open the **Global variables** tab and find the variable.
2. Click the pencil button at the end of its row. The **Set ...** window opens.
3. Type the new **Value**. Leave it blank to return to the engine default.
4. Click **Save**. Nova shows, for example, *query_timeout updated*.

The new value applies to queries that start after the change.

> **Warning:** A global change affects everyone using this Nova. Note the old
> value before you change it.

## Limits and errors

- **Session changes do not carry over.** Nova runs each statement on its own
  connection, so a value set on the **Session variables** tab (or with
  `SET query_timeout = ...` in a worksheet) does not reach your later queries,
  even though Nova reports it as updated. Use this tab to look values up, and
  change the **Global variables** tab when you need a lasting change.
- A name the engine does not know is refused, for example: *Unknown system
  variable 'no_such_variable_docsqa', the most similar variables are ...*.
- Many variables do not report a **Default**, so they show **-** there and
  never show a **Reset** button.
