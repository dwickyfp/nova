---
name: ai-functions
title: Use AI_* functions
summary: Call Nova's seven AI_* SQL UDFs correctly, including the JSON/schema arguments and configuration caveats.
triggers: ai_complete, ai_sentiment, ai_classify, ai_summarize, ai_extract, ai_translate, ai_filter, llm, sentiment, summarize, translate, classify text
source: docs/sql_docs/05-ai-functions.md, docs/sql_docs/11-query-catalog.md
---

# Skill: ai-functions

Seven `AI_*` functions are StarRocks global UDFs wrapping the engine's
`ai_query(VARCHAR, JSON)`. They run in the engine and are configured by a
provider alias. If no alias is set, the UDF returns an error **string** rather
than failing as "function not found".

## Signatures

| Function | Parameters | Returns |
|---|---|---|
| `AI_COMPLETE(prompt STRING)` | freeform prompt | text |
| `AI_SENTIMENT(txt STRING)` | text | JSON sentiment |
| `AI_CLASSIFY(txt STRING, categories STRING)` | text + candidate labels | one label |
| `AI_SUMMARIZE(txt STRING)` | text | 2–3 sentence summary |
| `AI_EXTRACT(txt STRING, json_schema STRING)` | text + JSON schema | JSON |
| `AI_TRANSLATE(txt STRING, target_lang STRING)` | text + language | translation |
| `AI_FILTER(txt STRING, criteria STRING)` | text + criteria | `"true"`/`"false"` |

## Templates

```sql
SELECT AI_SUMMARIZE(review_text) AS summary FROM reviews LIMIT 10;

SELECT AI_SENTIMENT(feedback) AS sentiment, id FROM customer_feedback;

SELECT AI_CLASSIFY(ticket_body, 'billing,tech,other') AS category FROM tickets;

SELECT AI_EXTRACT(doc, '{"type":"object","properties":{"total":{"type":"number"}}}') FROM invoices;
```

Combine with a stage read:

```sql
SELECT AI_SENTIMENT($1) FROM @reviews.batch.csv;
```

## Caveats

- Use `endpoint`, not `endpoint_url`, in any provider JSON. The docs' sample file
  still says `endpoint_url`; code and engine use `endpoint`.
- Provider JSON fields valid on the engine: `model` (required), `api_key`
  (required), `endpoint` (optional), `temperature`, `max_tokens`, `top_p`,
  `timeout_ms`.
- `ai_query()` availability in the running 4.1.4 image is source-verified, not
  live-probed — pass that through as a caveat if it matters.
- Never emit a credential; `api_key` is injected in-engine and never returned by
  Nova.
