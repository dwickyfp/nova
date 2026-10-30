# Module 23: Charts and dashboards

> Save answers as charts, lay them out on dashboards, and keep both live under
> each viewer's own access.

Saved charts and dashboards live in BI, Nova's built-in Apache Superset,
shown inside Studio. The design is in
[Architecture 17](arch-17-superset.md).

---

## Saving a chart

- **From a conversation:** a chart in a Studio answer has a save button. Nova
  saves the query that produced it and creates a BI chart from it, as you.
- **From a query:** in Studio **Artifacts**, choose **New view → Sheet from a
  query**.
- **From an agent:** asking for a dashboard ("build a sales overview") creates
  its charts and the dashboard in BI.

Every saved view keeps its read-only query. Opening it runs the query again
with your current role, so the chart shows current data and only the rows and
columns your policies allow. Two people opening the same chart can see
different rows.

## Explore

**Artifacts** opens Explore: dashboards as cards and charts in a table, with
search, sort, favorites and a tab per kind. Open a view to read it; **Edit in
BI** opens the chart in Superset's Explore or the dashboard in its edit mode,
inside Nova. The full BI workspace is under **BI** in the sidebar.

Only roles an administrator marks as BI authors can create or edit charts and
datasets; other roles can view.

## Views saved before BI

Charts and dashboards saved before BI was added are read-only. Explore shows
how many there are with **Convert all**; each one shows a banner with
**Convert to BI**.

Converting keeps the view's place in Explore and in conversations and runs as
you. The report lists, per view:

| Result | Meaning |
|--------|---------|
| In BI | Converted. Edit it in BI from now on. |
| Kept read-only | It uses something BI computes differently: calculated or level-of-detail fields, table calculations, some aggregations (median, variance, standard deviation), context, aggregate or relative-date filters. **Convert anyway** converts it without them; recreate them in BI if you need them. |
| Not converted | BI refused the chart (for example its query no longer runs). The view stays read-only. |

"Looks different" notes (sort order, size or label encodings, dashboard
filters and layout) never stop a conversion. Converting a dashboard also
converts the charts placed on it.

Administrators can convert views for people who are not signed in with
`backend/scripts/convert_studio_artifacts.py` (see [HOW_TO_RUN.md](../HOW_TO_RUN.md)).
