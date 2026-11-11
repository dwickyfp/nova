# Writing Nova docs

The pages under this folder are the user documentation served at `/docs`
(user menu → Docs). Every feature that users can see or call must have a page
here, and every change to such a feature updates its page in the same change.

## Where pages live

```
content/<section>/<NN>-<slug>.md
```

- `<section>` is one of the folders registered in `../nav.ts`. A new section
  needs an entry there (title and order); pages are discovered automatically.
- `<NN>` is a two-digit number that orders pages inside the section. It is not
  part of the URL: `content/sql/03-stages.md` is served at `/docs/sql/stages`.
- `<slug>` is lowercase kebab-case and stays stable once published, because
  other pages link to it.

## Front matter

Every page starts with:

```
---
title: Load files from a stage
description: One plain sentence saying what the reader can do after reading.
---
```

Only `title` and `description` are read. Do not repeat the title as a `#`
heading in the body; the page renders it. Start the body with `##` headings.

## What a page contains

Write for someone who has never used a database tool before, in plain English.

1. **What it is** — one or two sentences, no jargon. Define any term the reader
   needs (for example "a stage is a named folder of files Nova can read").
2. **Before you start** — the role or privilege needed, and anything that must
   exist first.
3. **Steps** — numbered, one action per step, naming the exact button, menu,
   or field as it appears on screen (in **bold**).
4. **Examples** — complete, copy-paste SQL or Python that was run in this
   product and worked. Show the result when it helps.
5. **Limits and errors** — what is not supported and what common error
   messages mean.

Rules:

- Only document behavior that was verified in the running product. Never copy
  an example from StarRocks, Snowflake, or another product without running it
  in Nova first.
- Do not mention internal endpoints, file paths, environment variables,
  storage vendors, physical bucket names, or credentials.
- Use the names users see: menu items, page titles, button labels.
- Use fenced code blocks with a language: `sql`, `python`, `text`.
- Callouts are blockquotes that start with a bold label:
  `> **Note:** ...`, `> **Tip:** ...`, `> **Warning:** ...`.
- Link to other pages with absolute docs paths: `[stages](/docs/sql/stages)`.
- Tables use GitHub-flavored Markdown.
