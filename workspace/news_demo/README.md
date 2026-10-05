# News demonstration fixture

This fixture exercises Studio News end to end: a Semantic View with News
switched on, an edition pressed by a scheduled execution account, and readers
whose row scopes decide which stories exist for them. The data is synthetic.

| Object | Name | Purpose |
| --- | --- | --- |
| Table | `news_demo.retail_sales` | 70 days by city, channel and category: 13,440 rows |
| Semantic View | `news_retail_sales` | `revenue` and `order_count` over the table |
| Publishing role | `news_editor` | SELECT without a row scope; bound to `nova_task_service_news` |
| Reader role | `news_reader` | SELECT with a per-user `city` scope |
| Users | `news_manager`, `news_bandung`, `news_jakarta`, `news_off`, `news_outsider` | The access matrix below |

## Labelled situations

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
