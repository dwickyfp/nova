# News demonstration fixture

This fixture exercises Studio News end to end for one fictional company,
Nusantara Retail, whose morning edition combines four desks. Each desk is one
Semantic View with News switched on; the edition is pressed by a scheduled
execution account, and a reader's row scopes decide which stories exist for
them. The data is synthetic.

| Desk | Table | Semantic View | Watched metrics | Sliced by |
| --- | --- | --- | --- | --- |
| Sales | `news_demo.retail_sales` | `news_retail_sales` | `revenue` | city, channel, category |
| Workforce | `news_demo.workforce_daily` | `news_workforce` | `overtime_hours`, `absence_hours` | city, department |
| Finance | `news_demo.operating_expenses` | `news_operating_expenses` | `operating_expense` | city, cost center |
| Engineering | `news_engineering.service_reliability` | `news_service_reliability` | `incident_count`, `downtime_minutes` | service, team |

Three of the four desks watch metrics where a rise is bad for the business, so
the model's judgement of each metric decides the colour of a change: overtime up
reads as unfavourable, expense down as favourable.

| Object | Name | Purpose |
| --- | --- | --- |
| Publishing role | `news_editor` | SELECT on every desk without a row scope; bound to `nova_task_service_news` |
| Reader role | `news_reader` | SELECT on `news_demo` only, with a per-user `city` scope on its three tables |
| Users | `news_manager`, `news_bandung`, `news_jakarta`, `news_off`, `news_outsider` | The access matrix below |

Ranger accepts one access policy per resource, so the two roles are granted at
different levels (table and database), and Engineering sits in its own database
that the reader role is not granted.

## Other desks

The generator in `backend/tests/benchmark/news/domains.py` builds the three
extra desks and derives what each edition must report from the design alone.

| Day (from newest) | Workforce | Finance | Engineering |
| --- | --- | --- | --- |
| 0 | Overtime in Bandung +45% | Marketing spend +26% | Incidents on `payments-api` ×3 (and its owning team) |
| 1 | Absence in Warehouse +25% | Surabaya expense −22% | Platform downtime +40% (and its two services) |
| 2 | Quiet | Jakarta expense +18% | A 7% rise, under the threshold |
| Decoys | A 60% swing on a department of a few people; a 6% dip | A 60% swing on a cost center with a few postings; a baseline-week outlier | A 60% swing on a barely monitored service |

A city reader gets their city on Sales, Workforce and Finance and no Engineering
desk at all. A change that spans every city, such as the Marketing rise, is not
shown to a one-city reader.

## Labelled situations: Sales

The generator in `backend/tests/benchmark/news/dataset.py` places one situation
on each of the last seven days. The labels come from how the data is built, so
they do not depend on the detector.

| Day (from newest) | Situation | Expected |
| --- | --- | --- |
| 0 | Bandung revenue −35%; a 60% swing on a category with a few orders a day | One critical Bandung story; no story for the thin category |
| 1 | Every city, channel and category +15% | A total story and one per supported slice |
| 2 | Online channel −20% | One Online story |
| 3 | Surabaya +40%; a baseline week holds a Medan outlier | One Surabaya story; nothing for Medan |
| 4 | Nothing | An empty edition |
| 5 | Semarang −6% | Nothing: under the 10% threshold |
| 6 | Makassar −14% | One Makassar story |

## Who reads what

| User | Role and scope | Bandung story | Total story |
| --- | --- | --- | --- |
| `news_manager` | `news_editor`, no scope | Yes | Yes |
| `news_bandung` | `news_reader`, `city = Bandung` | Yes | No |
| `news_jakarta` | `news_reader`, `city = Jakarta` | No | No |
| `news_off` | `news_editor`, News not enabled | Refused | Refused |
| `news_outsider` | `news_outsider`, no grant | No section | No section |

A reader with partial access never sees a total story: the total they can read
differs from the one the edition reports.

## Offline benchmark

No engine and no model call. From `backend/`:

```sh
uv run python -m tests.benchmark.news.run
uv run pytest tests/benchmark/news
```

The report gives detection precision and recall against the labels, severity
agreement, figures in the narrative that no proven value accounts for, and the
number of reader/story pairs that disagree with the access oracle.

## Live run on the governed stack

Start the governed stack with the API, scheduler and worker, and configure a
default LLM under AI Providers if the stories should be model-written. From
`backend/`:

```sh
uv run python scripts/seed_news_demo.py
NOVA_ADMIN_PASSWORD=... uv run python scripts/verify_news_demo.py
```

The seed generates user passwords into `/tmp/nova-news-demo.env` with mode
`0600` (override with `NEWS_DEMO_CREDENTIAL_FILE`). Do not commit that file. The
verification is not read-only: it creates the Semantic View, switches News on,
and sets the per-user entitlement. It waits up to 20 minutes for the first
scheduled edition.
