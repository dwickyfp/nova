# 05 — AI Functions (`AI_*`)

> Seven global UDFs that wrap StarRocks' `ai_query()` with a fixed prompt per function type, configured through Nova's provider/model/alias tables.

Sources: `backend/app/modules/llm_functions/service.py`, `backend/app/modules/ai_ml/service.py`, `docker/init-nova.sql`.

---

## Concept

Nova registers seven SQL UDFs in StarRocks:

`AI_COMPLETE`, `AI_SENTIMENT`, `AI_CLASSIFY`, `AI_SUMMARIZE`, `AI_EXTRACT`, `AI_TRANSLATE`, `AI_FILTER`.

Each one is a thin wrapper around the engine's built-in `ai_query()`. Nova's job is to:

1. Store provider connections (endpoint + API key) in `NOVA_SYSTEM.CONFIG_AI_PROVIDERS`.
2. Store model definitions in `NOVA_SYSTEM.CONFIG_AI_MODELS`.
3. Map a function type to a provider + model via `NOVA_SYSTEM.CONFIG_MODEL_ALIASES` (the "alias").
4. Generate the UDF body with the resolved provider config baked in, and execute `CREATE GLOBAL FUNCTION`.
5. Grant `USAGE` on every signature to the standard roles so the functions behave like native built-ins.

When no alias is configured, Nova registers a **placeholder** UDF whose body returns a helpful error string rather than causing `function not found`.

> **Credential note:** the resolved provider config (including the decrypted API key) is embedded in the generated UDF body. It is never returned by the provider/model list endpoints — those mask the key (`has_api_key` + `api_key_masked`) and are the only way the UI reads it back. Do not print a generated UDF body in an API response, log, or issue.

---

## Function reference

All functions are `GLOBAL FUNCTION`s. Signatures use `STRING`; the backend grants `USAGE` for `STRING`, `VARCHAR`, and `VARCHAR(65533)` variants.

### `AI_COMPLETE(prompt STRING)`

General completion. Body wraps `ai_query(prompt, '{config}')`.

```sql
SELECT AI_COMPLETE('Write a one-line product description for a mechanical keyboard');
```

Custom system prompt form: `ai_query(CONCAT('{prompt}', prompt), '{config}')`.

### `AI_SENTIMENT(txt STRING)`

Returns a JSON object with `sentiment` (`positive|negative|neutral|mixed`) and `confidence`.

```sql
SELECT customer_id,
       AI_SENTIMENT('The delivery was fast and the box was intact') AS sentiment
FROM NOVA_DEMO.customers LIMIT 5;
```

Prompt: `Analyze the sentiment of the following text. Reply with JSON: {"sentiment": "positive|negative|neutral|mixed", "confidence": 0.0-1.0}\n\nText: `.

### `AI_CLASSIFY(txt STRING, categories STRING)`

Zero-shot classification into one of the supplied categories; replies with only the category name.

```sql
SELECT product_name,
       AI_CLASSIFY(product_name, 'Electronics,Audio,Accessories,Storage') AS category
FROM NOVA_CATALOG.products LIMIT 10;
```

Prompt: `Classify the following text into ONE of these categories: [<categories>]. Reply with ONLY the category name, nothing else.\n\nText: `.

### `AI_SUMMARIZE(txt STRING)`

Two-to-three sentence summary.

```sql
SELECT AI_SUMMARIZE('Nova is a management console for StarRocks. It adds a stage dialect, ML functions, and an LLM layer.');
```

Prompt: `Summarize the following text concisely in 2-3 sentences:\n\n`.

### `AI_EXTRACT(txt STRING, json_schema STRING)`

Structured extraction against a caller-supplied JSON schema; replies with only the JSON.

```sql
SELECT AI_EXTRACT(
  'Invoice INV-2026-07 for PT Teknologi Nusantara, amount 125000000 IDR, due 2026-08-31',
  '{"customer": "string", "amount": "number", "due": "string"}'
);
```

Prompt: `Extract structured information from the following text. Return as JSON matching this schema: <json_schema>. Reply with ONLY the JSON.\n\nText: `.

### `AI_TRANSLATE(txt STRING, target_lang STRING)`

Translation into `target_lang`; replies with only the translation.

```sql
SELECT AI_TRANSLATE('Selamat pagi', 'English');
```

Prompt: `Translate the following text to <target_lang>. Reply with ONLY the translation, no explanation.\n\nText: `.

### `AI_FILTER(txt STRING, criteria STRING)`

Semantic boolean predicate; replies with only `"true"` or `"false"`.

```sql
SELECT review_text
FROM reviews
WHERE AI_FILTER(review_text, 'mentions a delivery delay') = 'true';
```

Prompt: `Does the following text match this criteria? Reply with ONLY "true" or "false".\nCriteria: <criteria>\n\nText: `.

---

## Configuration

### Provider

`POST /api/v1/ai/providers`

```json
{
  "name": "openai",
  "type": "openai",
  "endpoint": "https://api.openai.com/v1",
  "api_key": "<secret>",
  "default_params": {}
}
```

- `type` ∈ `openai`, `openai_compatible`, `anthropic`.
- The API key is encrypted at rest (`app.common.crypto.encrypt`) and never returned in plaintext. Listing shows `has_api_key` and `api_key_masked`.
- `test_connection` hits `{endpoint}/models` with the provider-appropriate auth header and returns the available model IDs.

The endpoint is rewritten for the engine: `localhost`/`127.0.0.1` become `host.docker.internal`, and the path is normalised to end in `/chat/completions` (`llm_functions/service.py:468`). So the UI can store `https://api.openai.com/v1` and the engine still receives a valid chat-completions URL.

### Model

`POST /api/v1/ai/providers/{provider_id}/models`

```json
{ "name": "gpt-4o-mini", "display_name": "GPT-4o mini", "type": "chat", "max_tokens": 4096 }
```

### Alias (function binding)

`POST /api/v1/ai/functions` (LLM functions router) creates an alias of `function_type` → `provider_id` + `model_id`, optionally with a custom `system_prompt`. Creating or updating an alias re-registers the UDF for that type.

`function_type` ∈ `complete`, `sentiment`, `classify`, `summarize`, `extract`, `translate`, `filter`.

Only one alias per `function_type` is the default; setting `is_default` unsets the previous default.

---

## How a UDF is registered

`LLMFunctionService._register_single_udf` (`llm_functions/service.py:422`):

1. Resolve the alias's provider and model.
2. Build the config JSON: `{ "model": <name>, "api_key": <decrypted>, "endpoint": <normalised> }`, merged with the alias's `default_params`.
3. Escape single quotes for the SQL literal (`''`).
4. Use the alias's `system_prompt` if set, else the template prompt.
5. `DROP GLOBAL FUNCTION IF EXISTS <fn>(STRING);` then `CREATE GLOBAL FUNCTION <fn>(...) RETURNS <body>`.

On startup `register_all_udfs()` registers configured types and placeholders for unconfigured ones, then grants `USAGE`.

### Placeholder body

For a type with no alias, the body is:

```sql
CONCAT('ERROR: <FN> not configured. Set up an alias in AI Providers > Functions tab. Input was: ', <args…>)
```

So a call still succeeds architecturally and returns an explanatory string.

---

## `ai_query()` and the engine

The functions are SQL UDFs, so they execute inside StarRocks. `ai_query()` is the engine's built-in LLM call. Nova does not implement an HTTP call path for these; it configures the engine's function. This means:

- Network access to the provider must be reachable **from the StarRocks BE**, not just from the Nova backend.
- The provider key travels to the engine inside the UDF definition. It is not stored in `NOVA_SYSTEM` in plaintext (it is encrypted) and is not returned by the API, but it *is* present in the engine's function catalog.

---

## Invariant: drop protection

The seven AI functions plus `ML_PREDICT` are protected. `guard_sql` blocks `DROP GLOBAL FUNCTION` for any of them (`sql_guard.py:102`), and `ALTER ROLE ACCOUNTADMIN` etc. are separately blocked. See `09-guardrails-invariants.md`.

The backend re-registers them on every startup, so a manual drop is both blocked and self-healing.

---

## Limitations

- Only `openai`, `openai_compatible`, and `anthropic` provider types are implemented in `test_connection`; other types return `Unsupported provider type`.
- The engine's `ai_query()` is an upstream StarRocks feature. Its exact supported config keys beyond `model`, `api_key`, and `endpoint` are **unverified** here; see `10-starrocks-reference-comparison.md` when available.
- `AI_FILTER` compares its string result to `'true'`; a model that returns `True` or extra text will not match cleanly.
- `AI_CLASSIFY` returns free text; Nova does not validate it against the supplied category list.
- Placeholder functions return a string, not an error, so a query against an unconfigured function returns rows.

---

## Verification

| Claim | Test / source |
|-------|---------------|
| Seven UDF templates and their `ai_query` bodies | `llm_functions/service.py:UDF_TEMPLATES` |
| Placeholder UDFs registered when unconfigured | `llm_functions/service.py::_register_placeholder_udf` |
| `USAGE` granted for all signatures/roles | `llm_functions/service.py::_grant_udf_privileges` |
| Provider key encrypted and masked | `ai_ml/service.py::_mask_api_key` |
| DROP of built-in AI/ML functions blocked | `tests/unit/test_sql_guard*.py` |
