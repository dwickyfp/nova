# Module 24: Advanced Indexes

> Inverted indexes for full-text search, N-Gram bloom filters, and index management.

---

## Inverted Index (Full-Text Search)

StarRocks 4.1 has a built-in CLucene-based inverted index for full-text search,
with a native **built-in** implementation available on shared-data clusters.
Nova exposes index management through the `/api/v1/indexes` API.

> **Verified against the pinned engine (4.1.4).** Every statement and predicate
> form below was exercised against `starrocks/fe-ubuntu:4.1.4` by
> `backend/tests/integration/test_inverted_index_l3.py`. Where the original spec
> in `docs/gap-analysis.md` diverged from the engine, the engine wins and the
> correction is called out inline.

### Preconditions

Three engine settings must hold before an inverted index can be created or used.
`GET /api/v1/indexes/capabilities` reports them so the UI can warn up front.

| Precondition | Scope | Detail |
|--------------|-------|--------|
| `enable_experimental_gin` | FE config | Off by default in 4.1.4. Without it, `ADD INDEX ... USING GIN` fails with *"The inverted index is disabled, enable it by setting FE config `enable_experimental_gin` to true."* Enable with `ADMIN SET FRONTEND CONFIG ("enable_experimental_gin" = "true")` or in `fe.conf`. |
| `replicated_storage=false` | Table property | Required on the target table. On 4.0+ the engine disables replication automatically **only** when the index is declared in `CREATE TABLE`; an index added later on a replicated table still fails. |
| Schema change completes | Engine behaviour | `ADD`/`DROP INDEX` is **asynchronous**. The statement returns immediately and the index appears once `SHOW ALTER TABLE COLUMN` reports `FINISHED` (observed ~14 s on the test stack). A second `ALTER` while one is in flight is rejected. |

### Create Inverted Index

The index can be declared at table creation, or added afterwards with
`ALTER TABLE` / `CREATE INDEX`.

```sql
-- At table creation
CREATE TABLE articles (
    id BIGINT NOT NULL,
    content STRING NOT NULL,
    INDEX idx_content (content) USING GIN ("parser" = "english")
)
DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1", "replicated_storage" = "false");

-- Added after creation (what Nova's /api/v1/indexes/create emits)
ALTER TABLE articles ADD INDEX idx_content (content) USING GIN ("parser" = "english");

-- Equivalent
CREATE INDEX idx_content ON articles (content) USING GIN ("parser" = "english");
```

The v4.1 **built-in** implementation (required on shared-data clusters) is
selected with `imp_lib`, and its n-gram dictionary with `dict_gram_num`:

```sql
ALTER TABLE articles ADD INDEX idx_builtin (content) USING GIN (
    "parser" = "english",
    "imp_lib" = "builtin",
    "dict_gram_num" = "2"
);
```

**Corrections to the original spec:**

- The spec showed `"parser" = "ngram"` with a `"parser.ngram_len" = "3"` knob.
  The 4.1 parser allow-list is `none` (default), `english`, `chinese`,
  `standard`, and `unicode`. Substring matching is instead served by the
  built-in implementation's `dict_gram_num`, or by `MATCH '%keyword%'` on a
  tokenized index. Treat `ngram` as **not a supported parser name** on 4.1.4.
- The spec implied the quoted `MATCH_ANY('machine learning')` call form.
  The engine parses `MATCH_ANY` as a **predicate**, not a function:
  `<column> MATCH_ANY 'keyword1 keyword2'` (space-separated keywords, no comma,
  no parentheses). See the query section below.

### Query with Full-Text Search

Full-text predicates are **pushdown-only**: they are accepted on a GIN-indexed
column in a `WHERE` clause and rejected anywhere else. When the indexed column
is tokenized (`parser` = `english` | `chinese` | `standard`), only these three
predicates are supported.

```sql
-- MATCH_ANY: any keyword matches (space-separated, no comma)
SELECT * FROM articles
WHERE content MATCH_ANY 'machine learning';

-- MATCH_ALL: every keyword must match
SELECT * FROM articles
WHERE content MATCH_ALL 'deep learning neural network';

-- MATCH: single keyword, supports the % wildcard for substring matching
SELECT * FROM articles WHERE content MATCH 'database';
SELECT * FROM articles WHERE content MATCH 'data%';

-- Combined with other filters
SELECT * FROM articles
WHERE content MATCH_ANY 'database'
AND category = 'technology'
AND published_at > '2026-01-01';
```

**Corrections and limitations observed on 4.1.4:**

- `MATCH_ANY('a, b')` with a comma is **not** the documented form; use spaces.
- `MATCH 'keyword with spaces'` returns nothing — the keyword must be a single
  term (or a `%term%` wildcard). Use `MATCH_ANY` for multiple terms.
- `MATCH` / `MATCH_ANY` / `MATCH_ALL` are **not** functions: `SELECT content
  MATCH 'x'` fails with *"Match can only used as a pushdown predicate on column
  with GIN in a single query."*
- The left operand must be a `STRING` (VARCHAR/CHAR) **NOT NULL** column.
- English/standard tokenization lowercases terms, so an uppercase keyword
  (`MATCH 'BEST'`) matches nothing; query with lowercase.
- Relevance scoring (`ORDER BY score`) is **not** available on the 4.1.4
  predicate path; results are filtered, not ranked. The `ORDER BY score` in the
  spec is a **`DEFER`** — see `docs/gap-analysis.md` §6.

### Drop Inverted Index

```sql
ALTER TABLE articles DROP INDEX idx_content;
-- or
DROP INDEX idx_content ON articles;
```

### List Indexes

Nova reads `SHOW INDEX FROM <db>.<table>` through
`GET /api/v1/indexes/<database>/<table>`. On 4.1.4 the `Index_type` column
carries the resolved properties, e.g.:

```
Key_name    Column_name   Index_type
idx_content content       GIN("imp_lib" = "clucene", "parser" = "english")
```

Note the engine fills in `imp_lib` even when the caller omitted it (default
`clucene` on shared-nothing).

---

## N-Gram Bloom Filter

```sql
ALTER TABLE logs ADD INDEX idx_log_msg (message) USING NGRAM_BF (
    "gram_num" = "3",
    "bloom_filter_fpp" = "0.01"
);
```

The `NGRAM_BF` kind is supported by the Nova API (`kind: "NGRAM_BF"` with
`properties: {"gram_num": "3", "bloom_filter_fpp": "0.01"}`). It takes none of
the GIN properties.

---

## API Reference

All endpoints run the assembled DDL on the **caller's** StarRocks connection,
so the engine's RBAC decides whether the operation is permitted. Every create and
drop writes to `NOVA_SYSTEM.AUDIT_LOG` on both the success and failure paths.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/indexes/create` | Create a GIN / BITMAP / NGRAM_BF index |
| `POST` | `/api/v1/indexes/drop` | Drop an index |
| `GET`  | `/api/v1/indexes/{database}/{table}` | List a table's indexes |
| `POST` | `/api/v1/indexes/validate-predicate` | Validate a `MATCH*` predicate shape |
| `GET`  | `/api/v1/indexes/capabilities` | Report engine preconditions and supported values |

All inputs are validated against allow-lists (`app/common/identifiers.py`)
before any SQL is assembled; an invalid identifier, parser, property key or kind
is refused with **403** and never reaches the engine.

---

## Deferred

- **Relevance scoring** (`ORDER BY score`) — not available on the 4.1.4
  predicate path; results are filtered, not ranked.
- **Inverted index management UI** (Table Manager indexes tab) — backend first,
  per the issue's sizing; the API is the contract the UI will call.
- **Search preview with relevance scoring** — blocked by the scoring gap.

---

## Index Management UI (target)

```
┌─ Table: articles — Indexes ─────────────────────────────┐
│                                                          │
│  [+ Add Index]                                           │
│                                                          │
│  Name            Column    Type           Status         │
│  idx_content     content   Inverted(GIN)  🟢 Active     │
│  idx_title       title     Bitmap         🟢 Active     │
│  idx_msg_ngram   message   N-Gram BF      🟢 Active     │
│  idx_created     created   Bitmap         🟢 Building   │
│                                                          │
│  ── Create Inverted Index ──                             │
│  Column: [content ▼]                                     │
│  Parser: [english ▼]   Imp lib: [clucene ▼]             │
│  [Create Index]                                          │
│                                                          │
│  ── Test Search ──                                       │
│  Query: [machine learning                    ]           │
│  Type: (●) MATCH_ANY  ( ) MATCH_ALL                     │
│  [Search]                                                │
│                                                          │
│  Results: 1 row (showing first 20) — unranked           │
│  ┌──────────┬────────────────────────┐                  │
│  │ id       │ content                │                  │
│  ├──────────┼────────────────────────┤                  │
│  │ 42       │ Intro to ML            │                  │
│  └──────────┴────────────────────────┘                  │
└──────────────────────────────────────────────────────────┘
```
