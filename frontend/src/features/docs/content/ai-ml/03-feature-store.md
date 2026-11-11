---
title: Feature Store
description: Define versioned feature values, group them by entity, and look up a record's values.
---

## What it is

A feature is a value used in an analysis or model, such as a customer's order
count. A **Feature View** defines the source columns and their observation
time. A **Feature Group** combines named versions of views for one entity.

## Before you start

[Register an entity](/docs/ai-ml/entities) first. Its source needs the entity's
key columns, a date-time column, and the feature columns. Your active role
must be able to read those columns. Use the owner account or an authorized
administrator to manage the definitions.

## Create a Feature View

1. Open **AI & ML → Feature Store**.
2. Under **Create Feature View**, enter a name and choose **Entity for new view**.
3. Choose **Event timestamp column**: the time when the feature values were observed.
4. Select **Feature columns**, then click **Create Feature View**.
5. Wait for the view to appear as **ACTIVE · v1**.

Use **Refresh** to build a new version. When it is ready, use **Activate v…**
to select it. A group names specific view versions; refreshing a view does
not silently replace the versions already selected by a group.

## Create a group and look up values

1. Choose **Entity for group**.
2. Enter **Feature Group name** and select **Active Feature View**.
3. Click **Create Feature Group**.
4. Select the group in the list.
5. Under **Lookup**, enter the entity key, such as a customer id, and click **Lookup**.

The result includes `values`, the group `version`, and `as_of`, the observation
time. `freshness` reports **FRESH** or **STALE** for each view. A successful
lookup can return stale values; check the timestamp before using them.

## Train from a group

The **Train model** form connects the group to a label table: a table holding
the outcome you want a model to learn. Select **Label relation**, its timestamp
column and **Target column**, then choose a model name and type.

The label table must contain the entity keys. Training matches feature values
to the label time, so later observations do not leak into earlier examples.
Check the model's training status before predicting with it.

## Limits and errors

- A lookup needs every registered entity key. Missing keys are refused.
- **Features not found for entity** means no accessible feature values matched
  the supplied key and time.
- Feature columns must be distinct from the event timestamp column.
- Creating these definitions does not grant access to the underlying data.
