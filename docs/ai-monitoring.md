# AI Monitoring

> Usage accounting for persisted assistant turns, Smart runs, and audited AI SQL.

## Concept

Open **Monitoring > AI Monitoring** at `/monitoring/ai`. The page defaults to
seven days and refreshes every 60 seconds while open. Auto-refresh can be paused;
Refresh requests a new snapshot immediately. Select Today, 7, 14, or 30 days,
then narrow the whole dashboard by source, saved model name, or user. Selecting
a name in Top models or Top users applies the corresponding filter. Clear
filters preserves the selected period. Activity history shows 25 rows per page.

`GET /api/v1/monitoring/ai/usage` reads the existing StarRocks history. It is
restricted to the same active administrator roles as the other monitoring
endpoints. It returns metadata only: no prompts, answers, SQL, credentials,
provider endpoints, or worker payloads.

The default window is seven UTC calendar dates including today. The comparison
uses the same dates shifted back seven days, ending at the same UTC time of day.
The endpoint accepts `days` from 1 through 30, `source`, `model`, `user_name`,
`offset`, and `limit`. Filters apply to every summary and to the activity table.

## Summaries

| Summary | Definition |
| --- | --- |
| Reported tokens | Sum of available persisted total-token values |
| Input / output tokens | Sum of each available component |
| Usage coverage | Activities with a total-token value divided by all observed activities |
| Average tokens | Reported tokens divided by activities with a reported total |
| Daily trend | One row per UTC date, including dates with no activity |
| Period change | Percentage change from the matching previous window; unavailable when the prior total is zero or a source is incomplete |
| Actions | Counts and tokens grouped by workspace assistant, Studio/assistant, Smart coordinator, Smart specialist, or SQL function combination |
| Top models | Groups ordered by reported tokens, with missing model names in an explicit unattributed group |
| Top users | Groups ordered by reported tokens |
| Failed activities | Explicit failed Smart runs and errored SQL statements; persisted assistant messages have unknown outcome |
| Duration | Available SQL execution durations only; assistant and Smart latency is not inferred |

## Accounting boundaries

- A conversation activity is a persisted assistant turn, potentially containing
  several provider calls. It is not a single provider request.
- Smart coordinator and specialist counters are counted separately. Smart final
  messages already aggregate these counters and are excluded to avoid counting
  them twice. Regular agent turns remain in assistant history.
- Smart run tokens are attributed to the run's start date. In-flight values can
  increase as checkpoints are saved. A run spanning the window boundary belongs
  to the window where it started.
- A Smart zero counter is unknown usage: the stored default cannot distinguish
  a provider that reported zero from a provider that omitted usage.
- Existing Smart records do not reliably record per-model token splits. Their
  tokens remain unattributed. Existing assistant model names are the names saved
  with the turn; current provider configuration is never used to rewrite history.
- SQL activities count statements with executable AI function syntax, not input
  rows or provider calls. A statement containing multiple functions has a combined
  action label and counts once. Comments, quoted strings, EXPLAIN, and function
  definitions are excluded using the existing StarRocks lexer.
- Audit timestamps use Nova's configured database timezone. The reader converts
  these timestamps and the query bounds with
  [StarRocks CONVERT_TZ](https://docs.starrocks.io/docs/sql-reference/sql-functions/date-time-functions/convert_tz/)
  so SQL dates align with assistant and Smart timestamps, which are stored in UTC.
- AI SQL wrappers currently return their result without token metadata to Nova.
  SQL activity therefore has unavailable tokens and model, never an estimated
  count or a false zero. Failed statements may have failed before any AI call.
- AI Search embeddings, decision routing, and connection tests do not have
  independently queryable usage accounting here. Nested calls already included
  in a saved turn are part of that turn's total. This is an activity dashboard,
  not a provider billing ledger. Dollar cost is not calculated without historical
  pricing and complete metering.
- Deleting conversations or pruning audit/run history removes those records
  from this view. Historical usage is not reconstructed from current settings.

## Availability and bounds

Each source and period has a 15-second timeout and a 10,000-row cap. The API
reports `limited` or `unavailable` for the affected source and period. Summary
values then describe available records only; period comparison is disabled.
For SQL, the cap applies to candidate audit statements before lexical filtering.
An outage of every current source returns 503 rather than an empty dashboard.
Activity pagination does not change aggregate values.

## Design direction

Use Nova's existing console typography, spacing, semantic surfaces, and chart
tokens from `DESIGN.md`. ENERGY 1 / RHYTHM 2 / MOTION 1: a quiet operational view,
with the token trend as the focal point and compact comparison tables for detail.
Separate the total-token summary from coverage so unknown usage is always visible.
Keep the application header outside the monitoring content scroller; contain
wide tables in their own horizontal scroll regions.

The total and coverage sit together because a usage number without its missing
data count is misleading. The chart uses Nova's first chart token only; it
answers which dates consumed the most reported tokens. Input/output totals stay
numeric because not every provider supplies both components. Ranking panels use
text links so the model or user becomes a filter without another drill-down page.
Refresh uses the existing refresh glyph because it describes an actual reload;
the sidebar uses the existing trend glyph to identify usage monitoring. No new
logos, avatars, decorative illustrations, or motion were introduced.

## Verification

Validation completed: 95 backend tests and 19 Chromium browser tests passed.
TypeScript and targeted frontend/backend lint checks passed. Production build
validation includes the generated `/monitoring/ai` route.

Backend tests cover token arithmetic, null versus zero, current/previous periods,
source failures, row limits, filters, pagination, AI SQL detection, UTC conversion
parameters, Smart root inclusion, exclusion of duplicate Smart final messages,
and the active administrator role boundary. The API only reads existing tables.

Browser tests render the real dashboard with controlled API fixtures. They cover
all filters, rankings, refresh, auto-refresh toggle, pagination, expandable daily
data and source availability, empty/loading/error/denied states, keyboard focus,
and theme contrast. The fixture values belong to tests and are not shipped as
dashboard fallback data.

Responsive checks use a 320px viewport, a 1280px viewport, and a simulated open
assistant panel. The header stays outside the content scroller, the page does
not acquire horizontal overflow, and long activity tables stay in their own
horizontal scroll regions. Light and dark screenshots were reviewed.

Live verification is limited: the documented default Nova account was rejected
by the running local application, and a read-only StarRocks connection using the
local CLI configuration returned OperationalError. No account, credential, or
authentication configuration was changed. The live authenticated route and live
database totals still need verification in the user's configured session.
