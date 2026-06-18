# Module 24: Advanced Indexes

> Inverted indexes for full-text search, N-Gram bloom filters, and index management.

---

## Inverted Index (Full-Text Search)

StarRocks 4.1 has built-in CLucene inverted index for full-text search.

### Create Inverted Index

```sql
-- English text
ALTER TABLE articles ADD INDEX idx_content (content) USING GIN (
    "parser" = "english"
);

-- Chinese text
ALTER TABLE articles ADD INDEX idx_content_cn (content_cn) USING GIN (
    "parser" = "chinese"
);

-- N-Gram tokenizer
ALTER TABLE articles ADD INDEX idx_title_ngram (title) USING GIN (
    "parser" = "ngram",
    "parser.ngram_len" = "3"
);

-- Standard tokenizer (word-level)
ALTER TABLE articles ADD INDEX idx_body_standard (body) USING GIN (
    "parser" = "standard"
);

-- Unicode tokenizer
ALTER TABLE articles ADD INDEX idx_text_unicode (text_col) USING GIN (
    "parser" = "unicode"
);
```

### Query with Full-Text Search

```sql
-- MATCH_ANY: any term matches
SELECT * FROM articles
WHERE content MATCH_ANY('machine learning')
ORDER BY score DESC;

-- MATCH_ALL: all terms must match
SELECT * FROM articles
WHERE content MATCH_ALL('deep learning neural network');

-- Match phrase
SELECT * FROM articles
WHERE content MATCH_ANY('"deep learning"');

-- Combined with other filters
SELECT * FROM articles
WHERE content MATCH_ANY('database')
AND category = 'technology'
AND published_at > '2026-01-01';
```

### Supported Parsers

| Parser | Use Case |
|--------|----------|
| `english` | English text with stemming |
| `chinese` | Chinese text with ICU segmentation |
| `standard` | Word-level tokenization |
| `unicode` | Unicode-aware tokenization |
| `ngram` | Substring matching (configurable n) |

---

## N-Gram Bloom Filter

```sql
-- Create N-Gram bloom filter index
ALTER TABLE logs ADD INDEX idx_log_msg (message) USING NGRAM_BF (
    "gram_num" = "3",
    "bloom_filter_fpp" = "0.01"
);
```

---

## Index Management UI

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
│  Parser: [english ▼]                                     │
│  [Create Index]                                          │
│                                                          │
│  ── Test Search ──                                       │
│  Query: [machine learning                    ]           │
│  Type: (●) MATCH_ANY  ( ) MATCH_ALL                     │
│  [Search]                                                │
│                                                          │
│  Results: 1,240 rows (showing first 20)                  │
│  ┌──────────┬────────────────────────┬───────┐          │
│  │ id       │ title                  │ score │          │
│  ├──────────┼────────────────────────┼───────┤          │
│  │ 42       │ Intro to ML            │ 0.95  │          │
│  │ 87       │ Deep Learning Guide    │ 0.89  │          │
│  │ 156      │ Neural Networks 101    │ 0.82  │          │
│  └──────────┴────────────────────────┴───────┘          │
└──────────────────────────────────────────────────────────┘
```
