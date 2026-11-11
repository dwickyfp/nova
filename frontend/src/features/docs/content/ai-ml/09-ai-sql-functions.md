---
title: AI SQL functions
description: Generate, classify, summarize, extract, translate, and filter text from SQL.
---

## Before you start

An administrator must configure a function alias under
[AI Providers → Functions](/docs/administration/ai-providers#the-functions-tab).
An alias selects the provider and model for that function. Merely registering
a function does not configure its model.

The examples below use short sample text. Model responses can vary. When using
a table column instead, read only the rows you need and confirm that sending
that data to the configured provider is appropriate.

## Generate text

```sql
SELECT AI_COMPLETE('Reply with the word ready.') AS answer;
```

The tested response was `ready`.

## Sentiment and classification

```sql
SELECT AI_SENTIMENT('The delivery arrived early and I am happy.') AS sentiment;
SELECT AI_CLASSIFY('Please explain this charge.', 'billing,delivery,other') AS category;
```

The sentiment result was JSON describing a positive sentiment. Classification
returned `billing`, one of the supplied labels.

## Summarize and extract

```sql
SELECT AI_SUMMARIZE('The store opened at nine. Ten orders arrived before lunch. All orders were shipped.') AS summary;
SELECT AI_EXTRACT('The total is 120.', '{"type":"object","properties":{"total":{"type":"number"}}}') AS details;
```

Extraction returned `{"total":120}`. Inspect the returned text before relying
on its structure or accuracy; model-generated content still needs validation.

## Translate and filter

```sql
SELECT AI_TRANSLATE('Good morning', 'Indonesian') AS translated;
SELECT AI_FILTER('My parcel arrived damaged.', 'mentions damage') AS matches;
```

The tested outputs were `Selamat pagi` and the text `true` respectively.
`AI_FILTER` returns text, so compare its result with `'true'` when using it in
a condition.

## Limits and errors

- If an alias is missing, the result can contain text starting with
  `ERROR: AI_COMPLETE not configured` (or the corresponding function name).
  A successful SQL statement is not proof that the model call succeeded.
- Provider availability, limits, and response time affect these functions.
- The seven text functions above use language models. Meaning-based
  [AI Search](/docs/ai-ml/ai-search) separately needs an embedding model.
- SQL examples use the configured function alias. Changing that alias can
  change the output for subsequent queries.
