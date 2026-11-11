---
title: Register entities
description: Identify the records that search indexes and feature groups refer to.
---

## What an entity is

An entity names a kind of record, such as a customer, and the columns that
identify it. Registering an entity does not create or copy the source table.
Feature Store uses this definition to match feature values to a record.

## Before you start

You need a source table you can read, including its key columns. Use the role
that will manage the entity. If creation is unavailable, ask an administrator
for access to manage AI & ML resources.

## Register a table

1. Open **AI & ML → Entities** in the sidebar.
2. Enter an entity name, for example `customer_activity`.
3. Select the database and table in **Source relation**.
4. Choose **Key columns**. For a customer table this might be `customer_id`.
   Include every column needed to identify a record.
5. Click **Create entity**. The entity appears in the list with its source
   and key columns.

Use the entity when creating a [Feature View](/docs/ai-ml/feature-store).

## Limits and errors

- Names use letters, numbers and underscores, and must start with a letter
  or underscore. A name already in use cannot be registered again in the same scope.
- Registering a table does not grant anyone permission to read it. Other
  roles still need access to the source.
- **Delete** retires the entity definition; it does not delete source rows.
  Check the feature groups that depend on it before removing it.
