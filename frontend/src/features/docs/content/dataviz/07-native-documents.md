---
title: Native document revisions
description: Understand drafts, publications, permissions and change receipts in native document integrations.
---

## What is available

Nova stores dataset, chart and dashboard definitions with a history of their
changes. The [native dashboard editor](/docs/dataviz/native-dashboards) in
Dataviz and the Dataviz assistant both use these records; integrations can use them too.
Dashboards from the earlier BI tool were not converted.

## Table and view datasets

Integrations can inspect a table or view, select columns, and save a native
dataset in an accessible workspace. Nova checks the source identity, column
types and permission to read those columns before saving. Inspection returns
schema information without sampling data rows. Your active role needs both
schema-inspection and data-read permission.

The saved dataset retains the source identity and schema that you inspected.
If the source changes before saving, inspect it again. Reusing a table name
does not make a replacement table the same source.

This API currently accepts text, Boolean, signed integers up to 64 bits,
decimals up to 38 digits, floating-point numbers, dates and timestamps. Select supported columns when
a source also contains other types. Unsupported types are refused explicitly.

## SQL datasets

Integrations can also inspect and save a dataset from one read-only `SELECT`
or `WITH ... SELECT` query over governed tables and views. Nova validates the
query, resolves its source identities and obtains output types and nullability
from the SQL analyzer. It does not guess types from a sample of rows.

Use unique column names and aliases for expressions. Saving checks the query
and source schema again; a changed source requires a new inspection. SQL
datasets use the same workspace, revision, publication and permission rules
as table and view datasets.

The current API refuses recursive queries, mutations, multiple statements,
stage access and unsupported connected sources.

## SQL dataset parameters

A SQL dataset can use named parameters such as `:cities` or `:min_amount`.
Declare every parameter when you inspect and save the query. A declaration
names the parameter and its type: text, Boolean, integer, decimal, date,
timestamp or a fixed list of allowed text values. It can also allow several
values, NULL, a default, allowed values, and a minimum or maximum. Nova refuses
a query that uses an undeclared name.

```sql
SELECT id, city, amount
FROM analytics.sales
WHERE city IN (:cities) AND amount >= :min_amount
```

In this example, `cities` allows several text values and `min_amount` is a
decimal with a default of 0. The analyzer derives output types from the
declarations without reading data rows. Changing a declaration changes the
inspected schema, so inspect again before saving.

Supply values when you preview the dataset. Nova sends them to the engine as
typed values, never as SQL text, and checks each value against its declaration
first. A missing required value, a wrong type, a value outside the allowed range
or an unknown parameter name is refused before the query runs. An omitted value
uses its default. Values never widen access: row filters and masks for your
active role still apply. Preview values are not saved with the dataset and do
not appear in query history.

Parameters marked sensitive cannot be previewed yet. Nova refuses them until
the engine can keep their values out of its own logs.

## Semantic datasets

Integrations can select dimensions, metrics and named filters from a published
Semantic View. The dataset pins that model version and its definition. Nova uses
the model's declared relationships and calculation rules, checks your source and
entity access, and derives output types from the analyzer.

Publishing a newer model version does not change an existing dataset. Inspect
and save the new selection explicitly to adopt it. A draft or deprecated model,
an unavailable pinned version, or revoked source access refuses the request.
An older published version remains usable while its model is active and your
access still permits it.

### Metrics at the chart's level of detail

A semantic dataset keeps one row for each combination of its dimensions. When a
chart shows fewer of those dimensions, Nova asks the Semantic View for each
metric again at the chart's own grouping. A margin defined as
`SUM(profit) / SUM(revenue)` per region is therefore the ratio for the whole
region, not the sum of each day's margin. A chart with no dimensions shows one
value calculated over every row.

Two cases cannot be recalculated that way: grouping a date by a longer period
(for example by month on a dataset kept by day), and filtering on a dimension
the chart does not show. There, a metric that is a single `SUM` or `COUNT` is
added up as usual. Any other metric, such as a ratio, an average or a distinct
count, makes the chart say that its configuration cannot be run. Add the
missing dimensions to the chart, or save a dataset with the grouping you need.

## Dashboard datasets

A dashboard can reference a dataset before any chart uses it. Each reference
pins one dataset revision in the same workspace. Changing the dataset's draft
does not update that reference. A dashboard editor still needs access to the
referenced dataset.

Integrations can save a new dataset together with its dashboard reference. Both
changes commit in one save, using the dashboard revision last read. If another
edit wins or the dashboard save fails, the new dataset is not left behind.
Creating a dataset this way requires permission to create resources in the
workspace as well as permission to edit the dashboard.

Removing a dataset reference from a draft does not release a reference held by
an earlier publication. Nova refuses to delete a dataset while a live dashboard's
draft or retained publication depends on it.

## Preview dataset rows

Integrations can preview a saved table, view, SQL or semantic dataset. A preview needs the
revision you last read and permission to read its data with your current role.
Draft previews require edit permission and can use an exact saved revision even
after a newer draft is saved. Publication previews use the published
revision. Nova checks the source and your access again before returning rows.
If either changes during the request, reload and inspect the source again.

Each page contains at most 200 rows and one MiB of data and schema. The response
preserves exact integers and decimals, distinguishes nulls from empty text, and
includes timestamp offsets. Floating-point values retain their binary precision;
NaN and infinity are refused. Text values longer than 4,096 characters and local
timestamps that are ambiguous during a daylight-saving transition are refused.

Previews sort ascending by the output fields in their displayed order. A next
page is available only when another row was observed; no total is estimated.
Pagination stops at an offset of 10,000. Pages are separate queries, so changes
to the source data can change their contents. Reduce the page size if the result
exceeds the size limit. A preview has a 30-second deadline; retry later if query
capacity is busy.

## Aggregate dataset queries

Charts read a dataset through aggregate queries: the fields to group by (with
an optional day, week, month, quarter or year grain for dates), the values to
calculate (sum, average, minimum, maximum, count or distinct count), filters,
sort order and a row limit of up to 10,000. Every filter value is sent as a
typed value, never as query text, and must match its field's type.

A filter can also apply to a calculated total instead of to rows, for example
to keep only the cities whose total revenue is above 1,000. Such a filter must
use a number the same query calculates, with one aggregation, and can compare
it, check a range or check for no value. It is applied after the totals are
calculated, so it never changes the totals that remain. Row filters and total
filters can be combined with AND, but not with OR or NOT.

A query runs with your current role. Draft queries need edit access and use the
exact dataset revision requested; published dashboards read the dataset
revision their publication pins. The response says when more rows exist than
the limit allowed. Sums of whole numbers and decimals keep their exact values.

## Edit or repair a dataset source

An editor can inspect a new table, view, SQL query or semantic selection and explicitly save that
source as a new dataset revision. Submit the source identity and schema from
the new inspection together with the draft revision you last read. Nova checks
the source and permission again before saving. A conflicting edit requires a
reload and review.

If a table or view was replaced, the old dataset refuses to preview it. Review
the replacement source before saving. Replacement fields receive new identities;
existing charts retain their pinned dataset revision and need an explicit update.
The existing publication and revision history remain unchanged by a draft edit.

Source edits for datasets with local calculations or relationships are not
yet available. A parameterized SQL dataset can be edited only with another SQL
query and its parameter declarations. If a save response is lost, look up its operation receipt
before submitting another edit. Retrying the exact save with its original operation
key returns the recorded result, even if the source has since changed. Retry uses
the original login and role context and still requires edit access. Changing the
request or its expected revision requires a new operation key.

## Dashboard pages

A dashboard definition describes each page as a canvas: a page size, a grid
that helps line things up, and the items placed on it. An item is a chart, a
filter, text, an image, a shape, an icon, a connector line, a note or a group of
items. Each item has its own position and size in pixels, and the order of the
items is the order in which they are stacked. A page can also list the order in
which the keyboard moves between items and the order, or the items to leave
out, on a phone.

The grid is only a guide for placing items. Its settings (number of columns,
gutter, margin, row height, and whether it is shown and items snap to it) are
saved with the page.

The dashboard editor draws every item at its saved position, scaled with the
page to fit the window. Shapes, icons and connector lines are drawn as vectors.
A connector whose end item no longer exists is not drawn. Notes attached to a
data point, connector ends on a data point, and values read from a chart's
result are not shown yet: such a note and line are hidden, and such a value
shows a dash. On a phone, the page shows its charts, filters, text, images and
labelled icons in one column, in its phone order, then in reading order.

Dashboards saved before canvases existed used a grid of 12 columns. Nova keeps
those saved versions exactly as they are and reads them as canvas pages 1,200
pixels wide that scroll down; every chart keeps its place and size. Integrations
written for the grid can still create a dashboard in the grid form and still
send grid changes to move, add, replace or remove a widget. Nova applies them to
the canvas. Sending an earlier change again with its original operation ID
returns its original receipt.

A page holds at most 150 items, and groups can be nested at most three deep.

## Drafts and publications

Each successful edit creates a new draft revision. Publishing selects an
immutable revision, including the chart and dataset revisions it references.
Later edits do not change that publication. Restoring a publication creates
a new draft and preserves the earlier history.

Editors can read drafts and revision history. Viewers can read an authorized
publication. A document without a publication has no viewer version yet.
An integration can request an exact historical draft revision, for example to
inspect the dataset revision referenced by a dashboard. This requires current
edit access; a newer draft is never substituted for a missing revision.
Published dataset responses include fields, parameter declarations and metric
labels. The publication response excludes source SQL and metric formulas,
including when the owner reads it. Authorized editors can read the full draft definition.

Editors can also retrieve the dataset names for a selected dashboard revision
in one request. The list preserves that revision's order and references. A dataset
whose editing access is unavailable has no name in the response. Opening it still
requires a fresh permission check.

New dataset inspections and previews appear in query history with the dataset
ID and operation. Preview entries also include the revision. These entries
retain the execution outcome without displaying the generated source query.

## Safe changes

Every change needs an operation ID. Edits also need the revision the client
last read. If another edit has changed that revision, Nova refuses the stale
change with a conflict; the client must reload before deciding what to submit.

If a response is lost, the client can look up the operation receipt to determine
whether the change committed. Receipt access remains bound to the original
sign-in, active role and security context. Switching roles requires a fresh
permission check.

## Sharing and deletion

Workspace access and document permissions are separate. Personal documents
remain private to their owner. In a shared workspace, document grants can name
a user or an active role. Managing grants requires **manage** permission;
publishing requires **publish** and **edit**. Export permission is separate.

> **Note:** Document permission never grants access to the data behind a chart.

Only the document owner can delete it. Nova blocks deletion when another live
document's draft or retained publication depends on it, including dependencies
through an older chart revision. Deletion hides the document and retains its
definitions, change receipts and audit history. There is no permanent-purge or
undelete operation in this API.

## Current limits

- Local metric views and sensitive parameter previews are not available yet. Saving a chart requires an
  existing valid native dataset revision.
- Document requests are limited to one MiB. Lists return at most 100 records
  per page.
- Role changes, expired sessions and revoked access can refuse a pending action.
  A document grant does not bypass these checks.
