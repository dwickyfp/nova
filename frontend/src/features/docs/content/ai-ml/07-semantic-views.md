---
title: Semantic Views
description: Define business measures and breakdowns, check a draft, and publish it for agents and BI.
---

## What it is

A Semantic View gives data shared business meaning. A **measure** is a number,
such as total revenue. A **breakdown** is a field used to group that number,
such as store or sale date. Agents and BI use the published definition.

## Before you start

Your source tables must exist and be readable under your active role. Know what
one row represents and which columns identify it. Creating a view does not
grant access to its sources.

## Build a draft

1. Open **AI & ML → Semantic Views** and click **Build visually**.
2. Enter **View name** and **Description**.
3. Choose **Source database** and **Source table**, then click **Add table**.
4. Review the imported fields and their expressions. Select **Breakdown** for
   fields people should group by. Text and date fields start selected; numeric
   fields can remain facts used in calculations. Select **time** for a date
   used in time-based questions.
5. Fill **Primary key** with the columns that identify a row.
6. Click **Add metric** for a calculation. Enter its name and expression using
   the dataset and column names shown in the builder.
7. If the view uses several tables, review their matching keys with
   **Add relationship**.
8. Click **Create draft**, review the summary, then click **Create draft** again.

## Check and publish

1. In **Versions**, click **Check** for the draft.
2. Resolve blocking problems. Review suggestions even when the check passes.
3. Click **Make live** for the checked version.
4. Confirm that **At a glance** identifies the live version.

A saved draft is not live. Publishing selects the version used by agents.
Source changes or missing access can still prevent a query from running after
a definition passes validation.

## Try a question and run a query

**Try a question** shows how the selected version interprets a business
question. It does not run the question on the data. Use **Definitions** to
inspect measures and fields, and **Health check** for definition quality.

Under **Explore the numbers**, choose **Numbers**, **Break down by**, and any
available **Filters**, then click **Show results**. This runs a published
version using your active role. A source column must be defined as a breakdown
before it can be used to group the result.

**Approved examples** holds reviewed examples. **Concepts** connects the view
to business definitions. **Suggestions** holds proposed improvements.

## Versions and BI

**Use as draft** loads a saved version for another edit. Changes create a new
version instead of rewriting published history. **Edit definition directly
(advanced)** exposes the definition for detailed editing.

**BI datasets** shows the datasets associated with the published view. Review
these before building [charts](/docs/dataviz/build-a-chart) from its measures.

## Limits and errors

- A view with no live version cannot run **Explore the numbers**.
- Unknown measures or breakdowns are validation errors. Check the selected
  version's definitions rather than guessing a column name.
- A metric without a default time dimension needs an explicit date definition
  before time-based questions can be resolved reliably.
- A successful preview describes a plan; only a completed query supplies data.
