---
title: Renderer fixture
description: Markdown examples used by the documentation renderer tests.
---

## First section

A paragraph with `inline code`, **bold**, and a [link to home](/docs) plus [an anchor](#second-section).

1. Step one
2. Step two

> **Note:** This is a note callout.

> **Warning:** This is a warning.

> **Tip:** A tip.

```sql
SELECT region, SUM(amount) AS total
FROM DEMO_SALES.orders
GROUP BY region;
```

### A subsection

| Column | Meaning |
| --- | --- |
| region | Sales region |
| total | Sum of sales |

## Second section

```python
import novaduck
con = novaduck.connect()
```
