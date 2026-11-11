---
title: Welcome to Nova
description: Learn what Nova is, what each part of the app is for, and where to start.
---

## What Nova is

Nova is one place to store, query, understand, and act on your organization's
data. You can:

- ask questions with SQL in a worksheet, or in plain language with Nova Studio;
- load files and connect other databases;
- describe what your data means for the business (semantic views and the
  business ontology), so people and AI agents use the same definitions;
- run work on a schedule with tasks;
- control exactly who may see which data, down to single rows and columns.

Every query, whether you type it or an AI agent writes it for you, runs with
**your** permissions. Nova never borrows someone else's access to answer you.

## Words you will see everywhere

| Word | Meaning |
| --- | --- |
| User | A person who signs in to Nova with a username and password. |
| Role | A named group of permissions, such as "Sales Jakarta". People get data through roles. |
| Active role | The one role you are working under right now. What you can see depends on it. |
| Database | A container for tables. A table holds rows and columns of data. |
| Worksheet | A page where you write and run SQL. |
| Stage | A named folder of files that Nova can read. |

Read [Access control concepts](/docs/access-control/concepts) for more on users
and roles.

## Map of the app

The menu on the left is grouped like this. Some groups appear only if your role
is allowed to use them.

| Menu group | Items | What you do there |
| --- | --- | --- |
| General | **Home** | Start a query, reopen recent work, open a starter template. |
| Data Management | **Workspaces** | Write and run SQL and notebooks. |
| | **Database Explorer** | Browse databases, tables, views, and columns. |
| | **Tasks** | Run SQL or Python on a schedule or when data changes. |
| | **AI & ML** | Agents, Semantic Views, Business Ontology, AI Search, Skills, Tools, Nova Studio, BI, and machine learning (Entities, Feature Store, ML Models). |
| Access Control | **Users**, **Roles**, **Data Access**, **Audit** | Manage who can sign in, what each role may see, and review what happened. |
| Administrator | **AI Providers**, **Admin Settings** | Connect AI model providers and change engine settings. |
| Operations | **Migration** | Move data and objects in from another system. |
| | **Monitoring** | Follow AI usage, queries, data loads, the cluster, and its health. |

Your own account menu sits at the bottom of the left menu. See
[Find your way around](/docs/getting-started/navigating).

## Where to start

1. [Sign in and choose your role](/docs/getting-started/sign-in-and-roles).
2. On **Home**, click **New query** to open a worksheet, or pick a starter
   template under **Start with a template**.
3. If you are an administrator, create users and roles next:
   [Users](/docs/access-control/users), [Roles](/docs/access-control/roles),
   and [Data access](/docs/access-control/data-access).
